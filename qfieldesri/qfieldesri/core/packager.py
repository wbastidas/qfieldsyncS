# -*- coding: utf-8 -*-
"""Empaquetado de una geodatabase de ESRI a un proyecto de QField.

Es el equivalente de ``libqfieldsync.offline_converter.OfflineConverter``, pero
partiendo de la geodatabase, que aqui es la unica fuente. El resultado
es una carpeta autocontenida que se copia tal cual al dispositivo::

    <salida>/<proyecto>/
        <proyecto>.qgs          archivo de proyecto que abre QField
        data.gpkg               todos los datos, un GeoPackage
        DCIM/ audio/ video/ files/   adjuntos capturados en campo
        qfieldesri_manifest.json     como volver a la geodatabase

El manifiesto es la pieza que hace posible la sincronizacion de vuelta: guarda
que clase de la geodatabase produjo cada tabla, con que campo se identifica
cada registro y que campos se pueden reescribir.
"""

import datetime
import io
import json
import os
import re

from ..profiles import FORM_GROUP_ORDER, load_profile
from ..symbology import StyleSheet, SymbologyResolver, load_symbology
from ..utils import wkb as wkb_utils
from ..utils.checksum import feature_checksum
from ..writers.geopackage import GeoPackageWriter, adapt_value, esri_type_to_gpkg
from ..writers.qfield_project import (
    FieldSpec,
    LayerSpec,
    QFieldProjectWriter,
    RelationSpec,
    WidgetSpec,
)
from .config import LayerAction
from .model import (
    CATEGORY_SYSTEM,
    DomainInfo,
    SpatialReferenceInfo,
)
from .naming import normalize as normalize_class
from .scope import LayerFilter, ScopeResolver, combine

MANIFEST_NAME = "qfieldesri_manifest.json"
MANIFEST_VERSION = 1

#: Tabla auxiliar (no registrada en ``gpkg_contents``) con la huella de cada
#: entidad tal como salio de la geodatabase. Es lo que permite saber, al
#: volver, que se edito en campo y que cambio en la geodatabase mientras tanto.
BASELINE_TABLE = "qfe_baseline"
BASELINE_COLUMNS = (
    ("table_name", "TEXT NOT NULL"),
    ("fid", "INTEGER NOT NULL"),
    ("source_key", "TEXT"),
    ("checksum", "TEXT NOT NULL"),
)

#: Geometria de ESRI -> (tipo GeoPackage, geometria, wkbType, multiparte)
GEOMETRY_MAP = {
    "Point": ("POINT", "Point", "Point", False),
    "Multipoint": ("MULTIPOINT", "Point", "MultiPoint", True),
    "Polyline": ("MULTILINESTRING", "Line", "MultiLineString", True),
    "Polygon": ("MULTIPOLYGON", "Polygon", "MultiPolygon", True),
}

#: Campos que nunca se copian al GeoPackage: los recalcula el propio motor.
SKIPPED_FIELD_TYPES = ("geometry", "raster")
SKIPPED_FIELD_NAMES = ("SHAPE_LENGTH", "SHAPE_AREA", "SHAPE.LEN", "SHAPE.AREA")

#: Tipos que ArcGIS gestiona solo: viajan al dispositivo (hacen falta para
#: identificar el registro al volver) pero ocultos y no editables.
READ_ONLY_TYPES = ("oid", "globalid")


class PackagingError(Exception):
    pass


class PackagingResult(object):
    """Resumen de lo que produjo un empaquetado."""

    def __init__(self, project_dir, project_file, gpkg_file, manifest):
        self.project_dir = project_dir
        self.project_file = project_file
        self.gpkg_file = gpkg_file
        self.manifest = manifest
        self.warnings = []
        self.layer_counts = {}
        #: explicacion legible de como se acoto la exportacion
        self.scope_description = ""
        #: de donde salio la simbologia de cada capa
        self.symbology_description = ""

    @property
    def total_features(self):
        return sum(self.layer_counts.values())

    def __repr__(self):  # pragma: no cover
        return "<PackagingResult %s (%d capas, %d entidades)>" % (
            self.project_dir,
            len(self.layer_counts),
            self.total_features,
        )


class Packager(object):
    """Convierte una geodatabase en un proyecto de QField."""

    def __init__(self, reader, config, progress=None):
        self.reader = reader
        self.config = config
        self.profile = load_profile(config.profile)
        #: ``callable(mensaje, porcentaje)`` para el Toolbox o la consola
        self.progress = progress or (lambda message, percent=None: None)
        self.workspace = None
        #: plan de ambito resuelto (que filtro le toca a cada clase)
        self.scope_plan = None
        #: resolutor de simbologia (estilo del usuario, ArcGIS, perfil, auto)
        self.symbology = None
        self._catalog_tables = {}
        self._domain_cache = {}
        #: claves recogidas de los Puestos, para filtrar despues sus Unidades
        self._parent_keys = {}
        self._needed_parent_keys = {}

    # ------------------------------------------------------------------
    def run(self):
        project_dir = os.path.join(self.config.output_dir, self.config.project_name)
        _ensure_dir(project_dir)
        gpkg_path = os.path.join(project_dir, "data.gpkg")
        project_path = os.path.join(
            project_dir, "%s.qgs" % _sanitize_filename(self.config.project_name)
        )

        self.progress("Leyendo el esquema de la geodatabase", 0)
        self.workspace = self.reader.describe_workspace()

        layers = self._select_layers()
        if not layers:
            raise PackagingError(
                "No hay ninguna clase seleccionada para empaquetar. Revise la "
                "configuracion o el filtro de capas."
            )

        layers = self._apply_scope(layers)
        self._prepare_symbology()

        crs = self._project_crs(layers)
        manifest = self._new_manifest(crs)
        result = PackagingResult(project_dir, project_path, gpkg_path, manifest)

        project = QFieldProjectWriter(
            title=self.config.title,
            crs=crs,
            datasource="./data.gpkg",
            qfield_options=self._qfield_options(),
        )

        with GeoPackageWriter(gpkg_path) as gpkg:
            srs_id = gpkg.add_srs(
                crs.code, "EPSG", crs.wkt, crs.name or "CRS del proyecto"
            )
            gpkg.create_private_table(BASELINE_TABLE, BASELINE_COLUMNS)
            self._write_catalog_tables(gpkg, project, layers)

            total = len(layers)
            for index, layer_info in enumerate(layers):
                percent = int(100.0 * index / total)
                self.progress("Empaquetando %s" % layer_info.name, percent)
                entry = self._package_layer(
                    gpkg, project, layer_info, srs_id, crs, index
                )
                manifest["layers"].append(entry)
                result.layer_counts[layer_info.name] = entry["feature_count"]

            self._extend_project_extent(project, gpkg, layers)

        self._add_relations(project, layers, manifest)

        self.progress("Escribiendo el proyecto de QField", 95)
        project.write(project_path)
        self._write_attachment_dirs(project_dir)
        self._write_manifest(project_dir, manifest)
        if self.scope_plan is not None and not self.scope_plan.is_empty:
            result.scope_description = self.scope_plan.describe()
        if self.symbology is not None:
            result.symbology_description = self.symbology.summary()
            result.warnings.extend(self.symbology.warnings)
            for warning in self.symbology.warnings:
                self.progress("Simbologia: %s" % warning)
        self.progress("Empaquetado terminado", 100)
        return result

    # ------------------------------------------------------------------
    # seleccion de capas
    # ------------------------------------------------------------------
    def _select_layers(self):
        """Capas a empaquetar, en el orden en que se dibujaran."""
        selected = []
        for layer_info in self.workspace.layers:
            # Una clase sin configuracion propia se incluye con los valores
            # por omision; para dejarla fuera hay que marcarla como REMOVE
            # (que es lo que hace la opcion --solo de la linea de comandos).
            config = self.config.layer_config(layer_info.name)
            if not config.is_included:
                continue
            if layer_info.dataset_type != layer_info.FEATURE_CLASS and not (
                self.config.include_related_tables
            ):
                continue
            selected.append(layer_info)

        if self.config.include_related_tables:
            selected = self._add_related_tables(selected)

        # Poligonos primero, luego lineas y puntos encima: es el orden util en
        # una pantalla de telefono.
        order = {"Polygon": 0, "Polyline": 1, "Multipoint": 2, "Point": 3}
        selected.sort(
            key=lambda layer: (
                order.get(layer.geometry_type, 4),
                layer.name.lower(),
            )
        )
        return selected

    def _apply_scope(self, layers):
        """Resuelve el ambito y ordena las clases para poder aplicarlo.

        Las clases que heredan el filtro de su Puesto tienen que empaquetarse
        **despues** que el Puesto, porque su filtro son las claves de los
        registros que de verdad se exportaron.
        """
        resolver = ScopeResolver(self.workspace, self.profile, self.reader)
        self.scope_plan = resolver.resolve(self.config.scope, layers)

        if self.scope_plan.is_empty:
            return layers

        if self.config.scope.is_spatial and not self.config.area_of_interest:
            self.config.area_of_interest = self.scope_plan.aoi_wkt
            self.config.area_of_interest_crs = self.scope_plan.aoi_crs

        self._needed_parent_keys = {}
        for layer_filter in self.scope_plan.filters.values():
            if layer_filter.method == LayerFilter.BY_RELATIONSHIP:
                self._needed_parent_keys.setdefault(layer_filter.parent, set()).add(
                    layer_filter.parent_field
                )

        for line in self.scope_plan.describe().splitlines():
            self.progress(line)
        return self._order_parents_first(layers)

    def _prepare_symbology(self):
        """Reune las fuentes de simbologia y monta el resolutor.

        ArcGIS guarda la simbologia fuera de la geodatabase, asi que aqui se
        junta lo que haya: el archivo de estilo del usuario, los archivos de
        capa exportados de ArcGIS, el estilo del perfil y, para el resto, la
        resolucion automatica.
        """
        stylesheet = None
        if self.config.style_file:
            stylesheet = StyleSheet.load(self.config.style_file)
            self.progress(
                "Estilo del usuario: %s (%d capas)"
                % (os.path.basename(self.config.style_file), len(stylesheet))
            )

        imported = {}
        warnings = []
        if self.config.symbology_source:
            imported, warnings = load_symbology(self.config.symbology_source)
            self.progress(
                "Simbologia importada de ArcGIS: %d capas desde %s"
                % (len(imported), _describe_source(self.config.symbology_source))
            )

        self.symbology = SymbologyResolver(
            profile=self.profile, stylesheet=stylesheet, imported=imported
        )
        self.symbology.warnings.extend(warnings)

    def _layer_style(self, layer_info, exported, count, index):
        """Estilo de una capa, con el origen que corresponda.

        Una tabla sin geometria no se dibuja en el mapa: darle simbologia solo
        serviria para inflar el informe con capas que nadie ve.
        """
        geometry = _qgis_geometry(layer_info.geometry_type)
        if geometry is None:
            return None
        return self.symbology.style_for(
            layer_info.name,
            geometry,
            subtype_field=layer_info.subtype_field,
            subtype_categories=[
                (subtype.code, subtype.name) for subtype in layer_info.subtypes
            ],
            field_names=[field.name for field in exported],
            feature_count=count,
            color_index=index,
        )

    def _order_parents_first(self, layers):
        """Coloca cada Puesto antes que las Unidades que dependen de el."""
        by_name = dict((layer.name, layer) for layer in layers)
        ordered = []
        placed = set()

        def place(layer, seen):
            if layer.name in placed or layer.name in seen:
                return
            seen.add(layer.name)
            layer_filter = self.scope_plan.filter_for(layer.name)
            if (
                layer_filter is not None
                and layer_filter.method == LayerFilter.BY_RELATIONSHIP
                and layer_filter.parent in by_name
            ):
                place(by_name[layer_filter.parent], seen)
            if layer.name not in placed:
                ordered.append(layer)
                placed.add(layer.name)

        for layer in layers:
            place(layer, set())
        return ordered

    def _add_related_tables(self, selected):
        """Arrastra las tablas ``Unidad`` de los ``Puesto`` seleccionados.

        Sin esto el tecnico veria el poste pero no las estructuras montadas en
        el, que es justo lo que va a revisar en campo.
        """
        # Se comparan clases, no cadenas: en una geodatabase corporativa la
        # relacion puede nombrarlas con otra calificacion.
        names = set(normalize_class(layer.name) for layer in selected)
        result = list(selected)
        for relationship in self.workspace.relationships:
            if relationship.is_attachment:
                continue
            if normalize_class(relationship.origin) not in names:
                continue
            destination = self.workspace.layer(relationship.destination)
            if destination is None or normalize_class(destination.name) in names:
                continue
            config = self.config.find_layer_config(destination.name)
            if config is not None and not config.is_included:
                continue
            result.append(destination)
            names.add(normalize_class(destination.name))
        return result

    def _project_crs(self, layers):
        if self.config.crs_code:
            return SpatialReferenceInfo(
                code=self.config.crs_code, name="EPSG:%s" % self.config.crs_code
            )
        for layer_info in layers:
            if layer_info.spatial_reference and layer_info.spatial_reference.code:
                return layer_info.spatial_reference
        if self.profile.crs:
            return SpatialReferenceInfo(
                code=self.profile.crs, name="EPSG:%s" % self.profile.crs
            )
        return SpatialReferenceInfo()

    # ------------------------------------------------------------------
    # capas
    # ------------------------------------------------------------------
    def _package_layer(self, gpkg, project, layer_info, srs_id, crs, index):
        config = self.config.layer_config(layer_info.name)
        exported = self._exported_fields(layer_info)
        table = _sanitize_table(layer_info.name)

        gpkg_fields = [
            (
                field.name,
                esri_type_to_gpkg(field.field_type, field.length),
                False,  # la obligatoriedad se expresa en el formulario, no en
                # el esquema: un NOT NULL en el GeoPackage haria fallar la
                # captura en campo antes de poder completar la ficha.
            )
            for field in exported
        ]

        promote = False
        if layer_info.is_spatial:
            geometry = GEOMETRY_MAP.get(layer_info.geometry_type)
            if geometry is None:
                raise PackagingError(
                    "Geometria no soportada en '%s': %s"
                    % (layer_info.name, layer_info.geometry_type)
                )
            gpkg_type, qgis_geometry, wkb_type, promote = geometry
            if layer_info.has_z:
                wkb_type += "Z"
            gpkg.create_feature_table(
                table,
                gpkg_fields,
                gpkg_type,
                srs_id,
                has_z=layer_info.has_z,
                has_m=layer_info.has_m,
                identifier=self.profile.class_alias(layer_info.name, layer_info.alias),
                spatial_index=self.config.spatial_index,
                promote_to_multi=promote,
            )
        else:
            qgis_geometry = wkb_type = None
            gpkg.create_attribute_table(
                table,
                gpkg_fields,
                identifier=self.profile.class_alias(layer_info.name, layer_info.alias),
            )

        count = 0
        writable = [field.name for field in exported if _is_writable(field)]
        if config.action != LayerAction.EMPTY:
            count = self._copy_features(
                gpkg, table, layer_info, exported, config, writable
            )

        layer_spec = LayerSpec(
            table=table,
            title=self.profile.class_alias(layer_info.name, layer_info.alias),
            geometry_type=qgis_geometry,
            wkb_type=wkb_type,
            fields=[self._field_spec(layer_info, field, config) for field in exported],
            crs=layer_info.spatial_reference or crs,
            extent=gpkg.layer_extent(table),
            group=config.group or self.profile.group_of(layer_info.name),
            visible=config.visible,
            read_only=(config.action == LayerAction.READ_ONLY),
            allow_feature_addition=(
                config.allow_feature_addition and config.action != LayerAction.READ_ONLY
            ),
            allow_feature_deletion=(
                config.allow_feature_deletion and config.action != LayerAction.READ_ONLY
            ),
            geometry_locked=(config.geometry_lock != "none"),
            display_expression=self._display_expression(layer_info, config, exported),
            subtype_field=layer_info.subtype_field,
            subtype_categories=[
                (subtype.code, subtype.name) for subtype in layer_info.subtypes
            ],
            color_index=index,
            style=self._layer_style(layer_info, exported, count, index),
        )
        layer_spec.fields = _sort_fields_by_group(layer_spec.fields)
        project.add_layer(layer_spec)

        return {
            "source_class": layer_info.name,
            "source_path": layer_info.path,
            "table": table,
            "layer_id": layer_spec.id,
            "geometry_type": layer_info.geometry_type,
            "key_field": self._key_field(layer_info),
            "oid_field": layer_info.oid_field,
            "globalid_field": layer_info.globalid_field,
            "subtype_field": layer_info.subtype_field,
            "where_clause": config.where_clause,
            "action": config.action,
            # Una capa de contexto no se escribe de vuelta jamas, ni aunque el
            # GeoPackage llegue modificado: en una red electrica, lo que viaja
            # como referencia no puede terminar editando la base de origen.
            "read_only": config.action == LayerAction.READ_ONLY,
            "attachment_fields": dict(config.attachment_fields),
            "writable_fields": writable,
            "exported_fields": [field.name for field in exported],
            "feature_count": count,
        }

    def _exported_fields(self, layer_info):
        """Campos que viajan al GeoPackage.

        Se exportan **todos** los campos utiles, aunque el formulario oculte
        algunos: si un campo no viaja, su valor se perderia al devolver el
        registro a la geodatabase. Lo que decide la configuracion es la
        visibilidad en el formulario, no la presencia del dato.
        """
        fields = []
        for field in layer_info.fields:
            field_type = (field.field_type or "").lower()
            if field_type in SKIPPED_FIELD_TYPES:
                continue
            if field.name.upper() in SKIPPED_FIELD_NAMES:
                continue
            if field_type == "blob":
                # Los adjuntos binarios van por la via de los adjuntos de
                # ArcGIS, no como columna del GeoPackage.
                continue
            fields.append(field)
        return fields

    def _where_clauses_for(self, layer_info, config):
        """Clausulas a recorrer para una clase: filtro del usuario + ambito.

        Se devuelve una lista porque una clausula ``IN`` con miles de valores
        no la admite ningun motor: se trocea y se recorre la clase una vez por
        trozo.
        """
        base = config.where_clause or None
        layer_filter = (
            self.scope_plan.filter_for(layer_info.name)
            if self.scope_plan is not None
            else None
        )
        if layer_filter is None:
            return [base]

        if layer_filter.method == LayerFilter.BY_RELATIONSHIP:
            # El filtro de una Unidad son las claves de los Puestos que de
            # verdad se exportaron, recogidas al empaquetar el Puesto.
            values = sorted(
                self._parent_keys.get(
                    (layer_filter.parent, layer_filter.parent_field), set()
                )
            )
            layer_filter = LayerFilter(
                layer_filter.layer,
                LayerFilter.BY_ATTRIBUTE,
                field=layer_filter.field,
                values=values,
            )

        clauses = layer_filter.where_clauses(
            delimit=lambda name: self.reader.delimit_field(layer_info, name)
        )
        if not clauses:
            return [base]
        return [combine(base, clause) for clause in clauses]

    def _copy_features(self, gpkg, table, layer_info, exported, config, writable):
        field_names = [field.name for field in exported]
        key_field = self._key_field(layer_info)
        promote = (
            layer_info.is_spatial
            and GEOMETRY_MAP.get(layer_info.geometry_type, (None, None, None, False))[3]
        )

        collect = self._needed_parent_keys.get(layer_info.name, ())

        count = 0
        baseline = []
        for where_clause in self._where_clauses_for(layer_info, config):
            for wkb, attributes in self.reader.iter_features(
                layer_info,
                field_names,
                where_clause=where_clause,
                aoi_wkt=self.config.area_of_interest,
                aoi_crs=self.config.area_of_interest_crs,
                limit=config.max_features,
            ):
                fid = gpkg.insert(table, attributes, wkb=wkb)
                baseline.append(
                    (
                        table,
                        fid,
                        _text(attributes.get(key_field)),
                        self._checksum(attributes, writable, wkb, promote),
                    )
                )
                for parent_field in collect:
                    value = attributes.get(parent_field)
                    if value not in (None, ""):
                        self._parent_keys.setdefault(
                            (layer_info.name, parent_field), set()
                        ).add(value)
                count += 1
                if len(baseline) >= 5000:
                    gpkg.insert_private(
                        BASELINE_TABLE,
                        [column for column, _kind in BASELINE_COLUMNS],
                        baseline,
                    )
                    baseline = []
                    self.progress("  %s: %d entidades" % (layer_info.name, count))
            if config.max_features and count >= config.max_features:
                break
        gpkg.flush(table)
        if baseline:
            gpkg.insert_private(
                BASELINE_TABLE,
                [column for column, _kind in BASELINE_COLUMNS],
                baseline,
            )
        return count

    @staticmethod
    def _checksum(attributes, writable, wkb, promote):
        """Huella comparable con la que calculara el sincronizador.

        Se toma sobre los valores ya convertidos al GeoPackage y sobre el WKB
        normalizado (promocionado a multiparte cuando corresponda), que es
        exactamente lo que quedara guardado en el paquete.
        """
        adapted = dict((name, adapt_value(attributes.get(name))) for name in writable)
        normalized = None
        if wkb:
            info = wkb_utils.analyze(wkb)
            if promote:
                info = wkb_utils.promote_to_multi(info)
            normalized = info.wkb
        return feature_checksum(adapted, writable, normalized)

    # ------------------------------------------------------------------
    # formulario
    # ------------------------------------------------------------------
    def _field_spec(self, layer_info, field, config):
        category = self.profile.category_of(layer_info.name, field.name)
        managed = (field.field_type or "").lower() in READ_ONLY_TYPES
        hidden = (
            managed
            or field.name in config.hidden_fields
            or category not in config.visible_categories
        )
        editable = (
            not managed
            and field.editable
            and field.name not in config.readonly_fields
            and config.is_editable
        )

        widget = self._widget_for(layer_info, field, config, hidden)
        group = (
            "Sistema"
            if hidden
            else self.profile.form_group_of(layer_info.name, field.name)
        )
        return FieldSpec(
            name=field.name,
            alias=self.profile.alias_of(layer_info.name, field.name, field.alias),
            widget=widget,
            editable=editable,
            not_null=(
                not field.nullable and not managed and category != CATEGORY_SYSTEM
            ),
            default_expression=self._default_expression(layer_info, field),
            group=group,
            hidden=hidden,
        )

    def _widget_for(self, layer_info, field, config, hidden):
        if hidden:
            return WidgetSpec("Hidden")

        attachment = config.attachment_fields.get(field.name)
        if attachment:
            return self._attachment_widget(layer_info, field, attachment)

        if field.name == layer_info.subtype_field and layer_info.subtypes:
            return WidgetSpec(
                "ValueMap",
                {
                    "map": [
                        {subtype.name: subtype.code} for subtype in layer_info.subtypes
                    ]
                },
            )

        domain = self._effective_domain(layer_info, field)
        if domain is not None:
            if domain.is_coded:
                if domain.name in self._catalog_tables:
                    return self._value_relation_widget(domain)
                return WidgetSpec(
                    "ValueMap",
                    {
                        "map": [
                            {_label(label): code} for code, label in domain.coded_values
                        ]
                    },
                )
            return WidgetSpec(
                "Range",
                {
                    "Min": domain.range_min,
                    "Max": domain.range_max,
                    "Style": "SpinBox",
                    "AllowNull": bool(field.nullable),
                    "Precision": 0 if _is_integer(field) else 3,
                },
            )

        field_type = (field.field_type or "").lower()
        if field_type in ("date", "datetime"):
            return WidgetSpec(
                "DateTime",
                {
                    "field_format": "yyyy-MM-dd HH:mm:ss",
                    "display_format": "yyyy-MM-dd HH:mm:ss",
                    "calendar_popup": True,
                    "allow_null": bool(field.nullable),
                    "field_iso_format": False,
                },
            )
        if field_type == "dateonly":
            return WidgetSpec(
                "DateTime",
                {
                    "field_format": "yyyy-MM-dd",
                    "display_format": "yyyy-MM-dd",
                    "calendar_popup": True,
                    "allow_null": bool(field.nullable),
                },
            )
        if field_type in ("smallinteger", "integer", "biginteger", "double", "single"):
            return WidgetSpec(
                "Range",
                {
                    "Style": "SpinBox",
                    "AllowNull": True,
                    "Precision": 0 if _is_integer(field) else 3,
                },
            )
        if field.length and field.length > 200:
            return WidgetSpec("TextEdit", {"IsMultiline": True, "UseHtml": False})
        return WidgetSpec("TextEdit", {"IsMultiline": False, "UseHtml": False})

    def _attachment_widget(self, layer_info, field, attachment_type):
        """Widget de foto/archivo con la expresion de nombrado de QFieldSync."""
        kinds = {
            "image": ("DCIM", 1, "{extension}"),
            "audio": ("audio", 3, "{extension}"),
            "video": ("video", 4, "{extension}"),
            "file": ("files", 0, "{filename}"),
        }
        directory, viewer, suffix = kinds.get(attachment_type, kinds["image"])
        expression = "'%s/%s_' || format_date(now(),'yyyyMMddhhmmsszzz') || '.%s'" % (
            directory,
            _sanitize_table(layer_info.name),
            suffix,
        )
        if attachment_type == "file":
            expression = (
                "'%s/%s_' || format_date(now(),'yyyyMMddhhmmsszzz') || '_%s'"
                % (directory, _sanitize_table(layer_info.name), suffix)
            )
        return WidgetSpec(
            "ExternalResource",
            {
                "DocumentViewer": viewer,
                "DocumentViewerHeight": 0,
                "DocumentViewerWidth": 0,
                "FileWidget": True,
                "FileWidgetButton": True,
                "FileWidgetFilter": "",
                "RelativeStorage": 1,  # relativo al proyecto
                "StorageMode": 0,
                "StorageType": "",
                "PropertyCollection": {
                    "name": None,
                    "properties": {
                        "propertyRootPath": {
                            "active": True,
                            "expression": expression,
                            "type": 3,
                        }
                    },
                    "type": "collection",
                },
                "qfieldesri_naming": expression,
            },
        )

    def _value_relation_widget(self, domain):
        table = self._catalog_tables[domain.name]
        return WidgetSpec(
            "ValueRelation",
            {
                "Layer": table["layer_id"],
                "LayerName": table["table"],
                "LayerSource": "./data.gpkg|layername=%s" % table["table"],
                "LayerProviderName": "ogr",
                "Key": "codigo",
                "Value": "descripcion",
                "AllowNull": True,
                "AllowMulti": False,
                "OrderByValue": True,
                "UseCompleter": True,
                "NofColumns": 1,
                "FilterExpression": "",
            },
        )

    def _effective_domain(self, layer_info, field):
        """Dominio aplicable al campo, uniendo los de todos los subtipos.

        QField no puede cambiar la lista de valores segun el subtipo del
        registro, asi que se ofrece la union de los dominios posibles. El
        verificador avisa de los campos donde esto ocurre para que el
        supervisor lo tenga presente al revisar lo capturado.
        """
        names = layer_info.all_domains_for(field.name)
        if not names:
            return None
        key = (layer_info.name, field.name)
        if key in self._domain_cache:
            return self._domain_cache[key]

        domains = [
            self.workspace.domains[name]
            for name in names
            if name in self.workspace.domains
        ]
        if not domains:
            self._domain_cache[key] = None
            return None
        if len(domains) == 1:
            self._domain_cache[key] = domains[0]
            return domains[0]

        if domains[0].is_coded:
            merged = []
            seen = set()
            for domain in domains:
                for code, label in domain.coded_values:
                    if code in seen:
                        continue
                    seen.add(code)
                    merged.append((code, label))
            union = DomainInfo(
                "+".join(domain.name for domain in domains),
                DomainInfo.CODED,
                field_type=domains[0].field_type,
                coded_values=merged,
            )
        else:
            union = DomainInfo(
                "+".join(domain.name for domain in domains),
                DomainInfo.RANGE,
                range_min=min(domain.range_min for domain in domains),
                range_max=max(domain.range_max for domain in domains),
            )
        self._domain_cache[key] = union
        return union

    def _default_expression(self, layer_info, field):
        """Valor por defecto del subtipo por defecto, como expresion del formulario."""
        value = field.default_value
        for subtype in layer_info.subtypes:
            if subtype.is_default and field.name in subtype.defaults:
                value = subtype.defaults[field.name]
                break
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return "'%s'" % str(value).replace("'", "''")

    def _display_expression(self, layer_info, config, exported):
        if config.display_field:
            return '"%s"' % config.display_field
        # Se prefiere un codigo legible por el tecnico antes que el OBJECTID.
        preferred = (
            "CODIGO",
            "CODIGOESTRUCTURA",
            "NUMEROPOSTE",
            "NOMBRE",
            "ALIMENTADORID",
            "ALIMENTADOR",
        )
        names = set(field.name.upper() for field in exported)
        for candidate in preferred:
            if candidate in names:
                return '"%s"' % candidate
        if layer_info.globalid_field:
            return '"%s"' % layer_info.globalid_field
        return '"%s"' % layer_info.oid_field

    def _key_field(self, layer_info):
        """Campo con el que se reconoce un registro al volver de QField."""
        return layer_info.globalid_field or layer_info.oid_field

    # ------------------------------------------------------------------
    # catalogos de dominios grandes
    # ------------------------------------------------------------------
    def _write_catalog_tables(self, gpkg, project, layers):
        """Vuelca los dominios grandes como tablas del GeoPackage.

        El modelo de CNEL tiene dominios de mas de mil miembros
        (``UP_TRF_TODOS`` tiene 1853). Meterlos como ``ValueMap`` en el ``.qgs``
        lo haria enorme y lento de abrir en el telefono; como tabla con un
        ``ValueRelation`` encima, QField los busca con un desplegable filtrable.
        """
        needed = {}
        for layer_info in layers:
            for field in self._exported_fields(layer_info):
                # Se resuelve el dominio efectivo (que puede ser la union de
                # los dominios de varios subtipos) para que la tabla de
                # catalogo y el widget hablen siempre del mismo conjunto.
                domain = self._effective_domain(layer_info, field)
                if (
                    domain is not None
                    and domain.is_coded
                    and len(domain) > self.config.big_domain_threshold
                ):
                    needed[domain.name] = domain

        for name in sorted(needed):
            domain = needed[name]
            table = "dom_%s" % _sanitize_table(name)
            gpkg.create_attribute_table(
                table,
                [("codigo", "TEXT(255)"), ("descripcion", "TEXT(255)")],
                identifier="Dominio: %s" % name,
                description="Catalogo de valores del dominio '%s'" % name,
            )
            for code, label in domain.coded_values:
                gpkg.insert(
                    table, {"codigo": _text(code), "descripcion": _label(label)}
                )
            gpkg.flush(table)

            layer_spec = LayerSpec(
                table=table,
                title="Dominio: %s" % name,
                fields=[
                    FieldSpec(
                        "codigo", "Codigo", WidgetSpec("TextEdit"), editable=False
                    ),
                    FieldSpec(
                        "descripcion",
                        "Descripcion",
                        WidgetSpec("TextEdit"),
                        editable=False,
                    ),
                ],
                group="Catalogos",
                visible=False,
                read_only=True,
                allow_feature_addition=False,
                display_expression='"descripcion"',
            )
            project.add_layer(layer_spec)
            self._catalog_tables[name] = {
                "table": table,
                "layer_id": layer_spec.id,
            }

    # ------------------------------------------------------------------
    # relaciones
    # ------------------------------------------------------------------
    def _add_relations(self, project, layers, manifest):
        tables = dict(
            (normalize_class(layer.name), _sanitize_table(layer.name))
            for layer in layers
        )
        for relationship in self.workspace.relationships:
            if relationship.is_attachment:
                continue
            parent = tables.get(normalize_class(relationship.origin))
            child = tables.get(normalize_class(relationship.destination))
            if not parent or not child:
                continue
            parent_layer = self.workspace.layer(relationship.origin)
            child_layer = self.workspace.layer(relationship.destination)
            if parent_layer is None or child_layer is None:
                continue
            if parent_layer.field(relationship.origin_key) is None:
                continue
            if child_layer.field(relationship.destination_key) is None:
                continue
            spec = RelationSpec(
                name=relationship.name,
                parent_table=parent,
                child_table=child,
                parent_field=relationship.origin_key,
                child_field=relationship.destination_key,
                label=relationship.forward_label or relationship.destination,
                strength=(
                    "Composition" if relationship.is_composite else "Association"
                ),
            )
            project.add_relation(spec)
            manifest["relations"].append(
                {
                    "name": relationship.name,
                    "parent_table": parent,
                    "child_table": child,
                    "parent_field": relationship.origin_key,
                    "child_field": relationship.destination_key,
                    "composite": relationship.is_composite,
                }
            )

    # ------------------------------------------------------------------
    # salidas auxiliares
    # ------------------------------------------------------------------
    def _extend_project_extent(self, project, gpkg, layers):
        extent = None
        for layer_info in layers:
            layer_extent = gpkg.layer_extent(_sanitize_table(layer_info.name))
            if layer_extent is None:
                continue
            extent = _union(extent, layer_extent)
        project.project_extent = extent

    def _qfield_options(self):
        options = {
            "initialMapMode": self.config.initial_map_mode,
            "maximumImageWidthHeight": self.config.max_image_width_height,
            "layerActionPreference": "offline",
            "originalProjectPath": self.config.workspace,
        }
        if self.config.digitizing_logs_layer:
            options["digitizingLogsLayer"] = self.config.digitizing_logs_layer
        if self.config.basemap_layer:
            options["createBaseMap"] = True
            options["baseMapType"] = "singleLayer"
            options["baseMapLayer"] = self.config.basemap_layer
        return options

    def _write_attachment_dirs(self, project_dir):
        if not self.config.include_attachments:
            return
        for name in self.config.attachment_dirs:
            _ensure_dir(os.path.join(project_dir, name))

    def _new_manifest(self, crs):
        return {
            "manifest_version": MANIFEST_VERSION,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "generator": "qfieldESRI",
            "reader": self.reader.name,
            "reader_supports_write": bool(self.reader.supports_write),
            "workspace": self.config.workspace,
            "workspace_type": self.workspace.workspace_type,
            "workspace_versioned": self.workspace.is_versioned,
            "connection_note": self.config.connection_note,
            "profile": self.profile.id,
            "project_name": self.config.project_name,
            "crs": crs.code,
            "area_of_interest": self.config.area_of_interest,
            "scope": self.config.scope.to_dict(),
            "symbology_source": self.config.symbology_source,
            "style_file": self.config.style_file,
            "layers": [],
            "relations": [],
        }

    def _write_manifest(self, project_dir, manifest):
        path = os.path.join(project_dir, MANIFEST_NAME)
        with io.open(path, "w", encoding="utf-8") as handle:
            text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write(_unicode(text))
        return path


# ----------------------------------------------------------------------
def load_manifest(project_dir):
    """Lee el manifiesto de un paquete generado por qfieldESRI."""
    path = project_dir
    if os.path.isdir(path):
        path = os.path.join(path, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise PackagingError(
            "No se encuentra '%s'. La carpeta no parece un paquete de "
            "qfieldESRI." % MANIFEST_NAME
        )
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _qgis_geometry(esri_geometry):
    """Geometria de ESRI -> la que usa el estilo (``Point``/``Line``/``Polygon``)."""
    entry = GEOMETRY_MAP.get(esri_geometry)
    return entry[1] if entry else None


def _sort_fields_by_group(fields):
    """Ordena los campos por pestana del formulario, sin perder el orden interno."""
    order = dict((name, index) for index, name in enumerate(FORM_GROUP_ORDER))
    return sorted(
        fields,
        key=lambda field: (order.get(field.group, len(order)),),
    )


def _is_writable(field):
    field_type = (field.field_type or "").lower()
    return field.editable and field_type not in READ_ONLY_TYPES


def _is_integer(field):
    return (field.field_type or "").lower() in (
        "smallinteger",
        "integer",
        "biginteger",
        "oid",
    )


def _label(value):
    text = _text(value)
    return text or "(sin descripcion)"


def _text(value):
    if value is None:
        return ""
    return _unicode(value)


def _unicode(value):
    try:
        return unicode(value)
    except NameError:
        return str(value)


def _sanitize_table(name):
    """Nombre de tabla valido en SQLite y legible en QField."""
    # En una geodatabase corporativa el nombre llega calificado
    # ('GYE.SDE.Barra'); en QField solo interesa la ultima parte.
    name = name.split(".")[-1]
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def build_stylesheet(workspace, resolver, description=None):
    """Resuelve el estilo de todas las clases espaciales y lo deja editable.

    Sirve para dos cosas que en el fondo son la misma: ver que simbologia se va
    a aplicar antes de empaquetar, y obtener un archivo de estilo de arranque
    que el usuario retoca a mano y vuelve a pasar con ``--estilo``.
    """
    from ..symbology import describe_layer_style

    sheet = StyleSheet({"capas": {}})
    if description:
        sheet.description = description
    for layer in workspace.layers:
        geometry = _qgis_geometry(layer.geometry_type)
        if geometry is None:
            continue
        style = resolver.style_for(
            layer.name,
            geometry,
            subtype_field=layer.subtype_field,
            subtype_categories=[
                (subtype.code, subtype.name) for subtype in layer.subtypes
            ],
            field_names=[field.name for field in layer.fields],
            feature_count=layer.feature_count,
        )
        sheet.layers[layer.name] = describe_layer_style(style, geometry)
    return sheet


def _describe_source(source):
    """Como nombrar el origen de simbologia en los mensajes."""
    from ..symbology import ACTIVE_DOCUMENT_ALIASES

    if source.lower() in ACTIVE_DOCUMENT_ALIASES:
        return "el mapa abierto en ArcGIS"
    return os.path.basename(source.rstrip("\\/")) or source


def _sanitize_filename(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _union(first, second):
    if first is None:
        return second
    if second is None:
        return first
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )
