"""Lector de geodatabases de ESRI basado en ``arcpy``.

Sirve indistintamente para:

* una **File Geodatabase** (``C:/datos/GYE.gdb``),
* una **Personal Geodatabase** (``.mdb``),
* una **geodatabase corporativa** (SDE) a traves de un archivo de conexion
  ``.sde``. En ese caso se respetan la version y la sesion de edicion, que es
  lo unico que realmente cambia respecto de la File Geodatabase.

El modulo se importa solo cuando hay arcpy; ``qfieldesri.readers.get_reader``
se encarga de elegir motor. Escrito para funcionar tanto en el Python 2.7 de
ArcMap 10.x como en el Python 3 de ArcGIS Pro.
"""

import os

from ..core.model import (
    DomainInfo,
    FieldInfo,
    LayerInfo,
    RelationshipInfo,
    SpatialReferenceInfo,
    SubtypeInfo,
    WorkspaceInfo,
)
from .base import GeodatabaseReader, ReaderError

try:
    import arcpy
except ImportError as error:  # pragma: no cover - depende del entorno
    raise ImportError(
        "arcpy no esta disponible. Ejecute qfieldESRI desde el Python de "
        "ArcGIS Desktop / ArcGIS Pro, o use el lector 'ogr'. (%s)" % error
    )


#: Geometrias de ESRI -> nombre usado en el resto de qfieldESRI.
GEOMETRY_TYPES = {
    "point": "Point",
    "multipoint": "Multipoint",
    "polyline": "Polyline",
    "polygon": "Polygon",
}


class ArcpyReader(GeodatabaseReader):
    name = "arcpy"
    supports_write = True

    def __init__(self, workspace):
        GeodatabaseReader.__init__(self, workspace)
        self._editor = None
        self._describe_cache = {}
        self._domains = None

    # ------------------------------------------------------------------
    # ciclo de vida
    # ------------------------------------------------------------------
    def open(self):
        if not arcpy.Exists(self.workspace):
            raise ReaderError("No se encuentra la geodatabase: %s" % self.workspace)
        arcpy.env.workspace = self.workspace
        arcpy.env.overwriteOutput = True
        # Mantener los identificadores globales tal cual al leer y escribir.
        try:
            arcpy.env.preserveGlobalIds = True
        except AttributeError:  # ArcMap 10.x no tiene este entorno
            pass
        return self

    def close(self):
        if self._editor is not None:
            self.stop_editing(save=False)
        self._describe_cache = {}

    # ------------------------------------------------------------------
    # metadatos
    # ------------------------------------------------------------------
    def _describe(self, dataset):
        if dataset not in self._describe_cache:
            self._describe_cache[dataset] = arcpy.Describe(dataset)
        return self._describe_cache[dataset]

    def workspace_type(self):
        description = self._describe(self.workspace)
        factory = getattr(description, "workspaceFactoryProgID", "") or ""
        if "SdeWorkspaceFactory" in factory:
            return WorkspaceInfo.ENTERPRISE
        if "FileGDBWorkspaceFactory" in factory:
            return WorkspaceInfo.FILE_GDB
        if "AccessWorkspaceFactory" in factory:
            return WorkspaceInfo.PERSONAL
        # En ArcMap 10.x el ProgID de una File Geodatabase puede venir vacio.
        if str(self.workspace).lower().endswith(".gdb"):
            return WorkspaceInfo.FILE_GDB
        if str(self.workspace).lower().endswith(".sde"):
            return WorkspaceInfo.ENTERPRISE
        return WorkspaceInfo.OTHER

    def list_datasets(self):
        """Recorre el workspace y devuelve ``(ruta, feature_dataset)``.

        Se usa ``arcpy.da.Walk`` cuando existe (10.1+) porque es el unico modo
        fiable de bajar a los feature datasets (``Electrico``,
        ``Electrico_Complementos``) sin ir cambiando ``arcpy.env.workspace``.
        """
        results = []
        walk = getattr(arcpy.da, "Walk", None)
        if walk is None:  # pragma: no cover - ArcGIS 10.0
            return self._list_datasets_legacy()

        for dirpath, _dirnames, filenames in walk(
            self.workspace, datatype=["FeatureClass", "Table"]
        ):
            for filename in filenames:
                parent = os.path.basename(dirpath)
                feature_dataset = None
                if os.path.normpath(dirpath) != os.path.normpath(self.workspace):
                    feature_dataset = parent
                results.append((os.path.join(dirpath, filename), feature_dataset))
        return results

    def _list_datasets_legacy(self):  # pragma: no cover - ArcGIS 10.0
        results = []
        arcpy.env.workspace = self.workspace
        for name in arcpy.ListFeatureClasses() or []:
            results.append((os.path.join(self.workspace, name), None))
        for name in arcpy.ListTables() or []:
            results.append((os.path.join(self.workspace, name), None))
        for dataset in arcpy.ListDatasets("", "Feature") or []:
            arcpy.env.workspace = os.path.join(self.workspace, dataset)
            for name in arcpy.ListFeatureClasses() or []:
                results.append((os.path.join(self.workspace, dataset, name), dataset))
        arcpy.env.workspace = self.workspace
        return results

    def read_domains(self):
        """Todos los dominios del workspace."""
        if self._domains is not None:
            return self._domains

        domains = {}
        list_domains = getattr(arcpy.da, "ListDomains", None)
        if list_domains is None:  # pragma: no cover - ArcGIS 10.0
            self._domains = domains
            return domains

        for domain in list_domains(self.workspace):
            if domain.domainType == "CodedValue":
                # ``codedValues`` es un dict codigo -> descripcion; se ordena
                # por descripcion porque es lo que vera el tecnico en campo.
                coded = sorted(
                    domain.codedValues.items(), key=lambda item: _sort_key(item[1])
                )
                domains[domain.name] = DomainInfo(
                    domain.name,
                    DomainInfo.CODED,
                    field_type=domain.type,
                    coded_values=coded,
                    description=getattr(domain, "description", "") or "",
                )
            else:
                domains[domain.name] = DomainInfo(
                    domain.name,
                    DomainInfo.RANGE,
                    field_type=domain.type,
                    range_min=domain.range[0],
                    range_max=domain.range[1],
                    description=getattr(domain, "description", "") or "",
                )
        self._domains = domains
        return domains

    def read_relationships(self):
        """Relationship classes del workspace, incluidas las de adjuntos."""
        relationships = []
        walk = getattr(arcpy.da, "Walk", None)
        if walk is None:  # pragma: no cover - ArcGIS 10.0
            return relationships

        for dirpath, _dirnames, filenames in walk(
            self.workspace, datatype="RelationshipClass"
        ):
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                try:
                    description = arcpy.Describe(path)
                except Exception as error:  # una relacion ilegible no para todo
                    arcpy.AddWarning(
                        "No se pudo leer la relacion '%s': %s" % (path, error)
                    )
                    continue
                origin_keys = _keys_of(description, "OriginPrimary")
                destination_keys = _keys_of(description, "OriginForeign")
                if not (
                    description.originClassNames
                    and description.destinationClassNames
                    and origin_keys
                    and destination_keys
                ):
                    continue
                relationships.append(
                    RelationshipInfo(
                        name=filename,
                        origin=description.originClassNames[0],
                        destination=description.destinationClassNames[0],
                        origin_key=origin_keys[0],
                        destination_key=destination_keys[0],
                        cardinality=getattr(description, "cardinality", "OneToMany"),
                        relationship_type=(
                            RelationshipInfo.COMPOSITE
                            if getattr(description, "isComposite", False)
                            else RelationshipInfo.SIMPLE
                        ),
                        forward_label=getattr(description, "forwardPathLabel", ""),
                        backward_label=getattr(description, "backwardPathLabel", ""),
                        is_attachment=getattr(
                            description, "isAttachmentRelationship", False
                        ),
                    )
                )
        return relationships

    def describe_layer(self, path, feature_dataset=None):
        """Metadatos de una clase de entidad o tabla."""
        description = self._describe(path)
        name = description.name
        is_feature_class = getattr(description, "dataType", "") == "FeatureClass"

        spatial_reference = None
        geometry_type = None
        has_z = has_m = False
        if is_feature_class:
            geometry_type = GEOMETRY_TYPES.get(
                (getattr(description, "shapeType", "") or "").lower()
            )
            has_z = bool(getattr(description, "hasZ", False))
            has_m = bool(getattr(description, "hasM", False))
            spatial_reference = _spatial_reference_info(
                getattr(description, "spatialReference", None)
            )

        fields = []
        for field in description.fields:
            fields.append(
                FieldInfo(
                    name=field.name,
                    field_type=field.type,
                    alias=field.aliasName,
                    length=field.length,
                    nullable=bool(field.isNullable),
                    editable=bool(field.editable),
                    domain=field.domain or None,
                    default_value=getattr(field, "defaultValue", None),
                )
            )

        subtypes = self._read_subtypes(path)
        return LayerInfo(
            name=name,
            dataset_type=(
                LayerInfo.FEATURE_CLASS if is_feature_class else LayerInfo.TABLE
            ),
            path=path,
            alias=getattr(description, "aliasName", "") or name,
            geometry_type=geometry_type,
            has_z=has_z,
            has_m=has_m,
            spatial_reference=spatial_reference,
            fields=fields,
            subtypes=subtypes,
            subtype_field=getattr(description, "subtypeFieldName", "") or None,
            oid_field=getattr(description, "OIDFieldName", "OBJECTID"),
            globalid_field=getattr(description, "globalIDFieldName", "") or None,
            feature_dataset=feature_dataset,
            is_versioned=bool(getattr(description, "isVersioned", False)),
        )

    def _read_subtypes(self, path):
        list_subtypes = getattr(arcpy.da, "ListSubtypes", None)
        if list_subtypes is None:  # pragma: no cover - ArcGIS 10.0
            return []
        try:
            raw = list_subtypes(path)
        except Exception:
            return []

        subtypes = []
        for code in sorted(raw):
            entry = raw[code]
            if not entry.get("SubtypeField"):
                # ``ListSubtypes`` devuelve una entrada unica con
                # ``SubtypeField`` vacio cuando la clase no usa subtipos: eso
                # no es un subtipo, son los valores por defecto de la clase.
                continue
            domains = {}
            defaults = {}
            for field_name, value in (entry.get("FieldValues") or {}).items():
                default_value, domain = value[0], value[1]
                if domain is not None:
                    domains[field_name] = domain.name
                if default_value is not None:
                    defaults[field_name] = default_value
            subtypes.append(
                SubtypeInfo(
                    code=code,
                    name=entry.get("Name", str(code)),
                    is_default=bool(entry.get("Default", False)),
                    domains=domains,
                    defaults=defaults,
                )
            )
        return subtypes

    def describe_workspace(self, layer_names=None):
        wanted = None
        if layer_names:
            wanted = set(name.lower() for name in layer_names)

        layers = []
        feature_datasets = []
        for path, feature_dataset in self.list_datasets():
            name = os.path.basename(path)
            if wanted is not None and name.lower() not in wanted:
                continue
            try:
                layers.append(self.describe_layer(path, feature_dataset))
            except Exception as error:
                # Una clase ilegible (permisos en SDE, bloqueo de esquema) no
                # debe tumbar el analisis completo del workspace.
                arcpy.AddWarning("No se pudo describir '%s': %s" % (path, error))
                continue
            if feature_dataset and feature_dataset not in feature_datasets:
                feature_datasets.append(feature_dataset)

        return WorkspaceInfo(
            path=self.workspace,
            workspace_type=self.workspace_type(),
            layers=layers,
            domains=self.read_domains(),
            relationships=self.read_relationships(),
            feature_datasets=feature_datasets,
            is_versioned=self._is_versioned(),
        )

    def _is_versioned(self):
        """Si los datos de esta conexion estan registrados como versionados.

        El versionado es una propiedad **de cada dataset**, no del workspace,
        asi que se pregunta a las clases: basta con que una este versionada
        para que la sesion de edicion tenga que abrirse en ese modo. Si no se
        puede saber (permisos, clase ilegible), se supone que si, que es el
        modo habitual de una geodatabase corporativa.
        """
        if self.workspace_type() != WorkspaceInfo.ENTERPRISE:
            return False

        checked = False
        for path, _dataset in self.list_datasets():
            try:
                description = self._describe(path)
            except Exception:  # noqa: S112 - una clase ilegible no decide nada
                continue
            versioned = getattr(description, "isVersioned", None)
            if versioned is None:
                continue
            checked = True
            if versioned:
                return True
        return not checked

    # ------------------------------------------------------------------
    # lectura de entidades
    # ------------------------------------------------------------------
    def _make_source(self, layer_info, where_clause=None, aoi_wkt=None, aoi_crs=None):
        """Devuelve la fuente a recorrer, aplicando el area de interes.

        El recorte espacial se hace con una capa temporal en memoria y
        ``SelectLayerByLocation``, que es lo unico que funciona igual en
        ArcMap y en Pro y que aprovecha el indice espacial del servidor cuando
        la fuente es una geodatabase corporativa.
        """
        if not aoi_wkt or not layer_info.is_spatial:
            return layer_info.path, None

        layer_name = "qfe_%s" % abs(hash(layer_info.path))
        if arcpy.Exists(layer_name):
            arcpy.Delete_management(layer_name)
        arcpy.MakeFeatureLayer_management(
            layer_info.path, layer_name, where_clause or ""
        )
        spatial_reference = None
        if aoi_crs:
            spatial_reference = arcpy.SpatialReference(aoi_crs)
        elif layer_info.spatial_reference and layer_info.spatial_reference.code:
            spatial_reference = arcpy.SpatialReference(
                layer_info.spatial_reference.code
            )
        aoi = _geometry_from_wkt(aoi_wkt, spatial_reference)
        arcpy.SelectLayerByLocation_management(
            layer_name, "INTERSECT", aoi, "", "NEW_SELECTION"
        )
        return layer_name, layer_name

    def iter_features(
        self,
        layer_info,
        field_names,
        where_clause=None,
        aoi_wkt=None,
        aoi_crs=None,
        limit=0,
    ):
        source, temporary = self._make_source(
            layer_info, where_clause, aoi_wkt, aoi_crs
        )
        cursor_fields = list(field_names)
        geometry_index = -1
        if layer_info.is_spatial:
            geometry_index = len(cursor_fields)
            cursor_fields.append("SHAPE@WKB")

        # Cuando ya hay una seleccion espacial, la clausula WHERE va aplicada
        # en la capa temporal; repetirla aqui seria redundante pero inocua.
        clause = None if temporary else (where_clause or None)

        count = 0
        try:
            with arcpy.da.SearchCursor(source, cursor_fields, clause) as cursor:
                for row in cursor:
                    wkb = None
                    if geometry_index >= 0:
                        raw = row[geometry_index]
                        wkb = bytes(raw) if raw else None
                    attributes = {}
                    for index, name in enumerate(field_names):
                        attributes[name] = row[index]
                    yield wkb, attributes
                    count += 1
                    if limit and count >= limit:
                        break
        finally:
            if temporary and arcpy.Exists(temporary):
                arcpy.Delete_management(temporary)

    def count_features(self, layer_info, where_clause=None):
        if where_clause:
            layer_name = "qfe_count_%s" % abs(hash(layer_info.path))
            if arcpy.Exists(layer_name):
                arcpy.Delete_management(layer_name)
            if layer_info.is_spatial:
                arcpy.MakeFeatureLayer_management(
                    layer_info.path, layer_name, where_clause
                )
            else:
                arcpy.MakeTableView_management(
                    layer_info.path, layer_name, where_clause
                )
            try:
                return int(arcpy.GetCount_management(layer_name)[0])
            finally:
                arcpy.Delete_management(layer_name)
        return int(arcpy.GetCount_management(layer_info.path)[0])

    def delimit_field(self, layer_info, name):
        return arcpy.AddFieldDelimiters(layer_info.path, name)

    def union_wkt(self, layer_name, where_clause=None):
        """Une los poligonos elegidos en un solo WKT para el recorte."""
        path = layer_name
        layer = None
        if not arcpy.Exists(path):
            layer = self._describe_cache.get(layer_name)
            path = layer.catalogPath if layer is not None else layer_name
        description = arcpy.Describe(path)
        spatial_reference = getattr(description, "spatialReference", None)

        union = None
        with arcpy.da.SearchCursor(path, ["SHAPE@"], where_clause or None) as cursor:
            for (geometry,) in cursor:
                if geometry is None:
                    continue
                union = geometry if union is None else union.union(geometry)
        if union is None:
            return None, None
        code = getattr(spatial_reference, "factoryCode", None) or None
        return union.WKT, code

    # ------------------------------------------------------------------
    # escritura (sincronizacion de vuelta)
    # ------------------------------------------------------------------
    def start_editing(self, versioned=None):
        """Abre una sesion de edicion.

        En una File Geodatabase la sesion es opcional pero conviene: agrupa el
        lote y permite descartarlo entero. En una geodatabase corporativa es
        obligatoria, y **hay que abrirla de la forma correcta**:

        ``arcpy.da.Editor.startEditing(with_undo, multiuser_mode)``

        ``multiuser_mode`` no significa "hay varios usuarios": significa que
        los datos estan **registrados como versionados**. Si se edita una
        clase no versionada de Oracle con ``multiuser_mode=True`` —o al reves—
        ArcGIS no avisa: falla. Por eso se deduce del propio dataset
        (``Describe.isVersioned``) salvo que la llamada lo indique.

        El deshacer se activa siempre que se pueda: si el lote falla a mitad,
        la geodatabase de origen tiene que quedar como estaba.
        """
        editor_class = getattr(arcpy.da, "Editor", None)
        if editor_class is None:  # pragma: no cover - ArcGIS 10.0
            return
        if versioned is None:
            versioned = self._workspace_is_versioned()

        self._editor = editor_class(self.workspace)
        try:
            self._editor.startEditing(True, versioned)
        except TypeError:  # pragma: no cover - ArcGIS 10.0/10.1, firma corta
            self._editor.startEditing(True)
        self._editor.startOperation()

    def _workspace_is_versioned(self):
        """``True`` si lo que se va a editar esta registrado como versionado.

        En una File Geodatabase no existe el versionado, pero ArcGIS espera
        ``multiuser_mode=True`` igualmente: es el modo normal de edicion. El
        caso que hay que detectar es el contrario, la geodatabase corporativa
        con datos **no** versionados, que exige ``False``.
        """
        if self.workspace_type() != WorkspaceInfo.ENTERPRISE:
            return True
        return self._is_versioned()

    def stop_editing(self, save=True):
        if self._editor is None:
            return
        try:
            self._editor.stopOperation()
            self._editor.stopEditing(save)
        finally:
            self._editor = None

    def update_feature(self, layer_info, key_field, key_value, attributes, wkb=None):
        fields = list(attributes.keys())
        cursor_fields = list(fields)
        if wkb is not None and layer_info.is_spatial:
            cursor_fields.append("SHAPE@WKB")
        where = "%s = %s" % (
            arcpy.AddFieldDelimiters(layer_info.path, key_field),
            _sql_literal(key_value),
        )
        updated = 0
        with arcpy.da.UpdateCursor(layer_info.path, cursor_fields, where) as cursor:
            for _row in cursor:
                values = [attributes[name] for name in fields]
                if wkb is not None and layer_info.is_spatial:
                    values.append(bytearray(wkb))
                cursor.updateRow(values)
                updated += 1
        return updated

    def insert_feature(self, layer_info, attributes, wkb=None):
        fields = list(attributes.keys())
        cursor_fields = list(fields)
        values = [attributes[name] for name in fields]
        if wkb is not None and layer_info.is_spatial:
            cursor_fields.append("SHAPE@WKB")
            values.append(bytearray(wkb))
        with arcpy.da.InsertCursor(layer_info.path, cursor_fields) as cursor:
            return cursor.insertRow(values)

    def delete_feature(self, layer_info, key_field, key_value):
        where = "%s = %s" % (
            arcpy.AddFieldDelimiters(layer_info.path, key_field),
            _sql_literal(key_value),
        )
        deleted = 0
        with arcpy.da.UpdateCursor(layer_info.path, [key_field], where) as cursor:
            for _row in cursor:
                cursor.deleteRow()
                deleted += 1
        return deleted


# ----------------------------------------------------------------------
#: Clase de entidad temporal donde se construye el poligono del sector cuando
#: la version de ArcGIS no sabe leer WKT directamente.
_WKT_SCRATCH = "in_memory/qfe_aoi"


def _geometry_from_wkt(wkt, spatial_reference):
    """Construye una geometria a partir de su WKT.

    ``arcpy.FromWKT`` no existe en ArcGIS Desktop 10.x: se anadio en Pro. Como
    el destino principal de qfieldESRI es ArcMap, aqui hay una alternativa que
    si funciona en 10.1 en adelante: escribir el WKT en una clase temporal en
    memoria con un cursor —``SHAPE@WKT`` es un token reconocido— y leer de
    vuelta la geometria ya construida.
    """
    from_wkt = getattr(arcpy, "FromWKT", None)
    if from_wkt is not None:
        return from_wkt(wkt, spatial_reference)

    if arcpy.Exists(_WKT_SCRATCH):
        arcpy.Delete_management(_WKT_SCRATCH)
    workspace, name = _WKT_SCRATCH.split("/")
    arcpy.CreateFeatureclass_management(
        workspace,
        name,
        _wkt_geometry_type(wkt),
        spatial_reference=spatial_reference,
    )
    try:
        with arcpy.da.InsertCursor(_WKT_SCRATCH, ["SHAPE@WKT"]) as cursor:
            cursor.insertRow([wkt])
        with arcpy.da.SearchCursor(_WKT_SCRATCH, ["SHAPE@"]) as cursor:
            for (geometry,) in cursor:
                return geometry
    finally:
        if arcpy.Exists(_WKT_SCRATCH):
            arcpy.Delete_management(_WKT_SCRATCH)
    raise ReaderError("No se pudo construir el area de interes a partir del WKT.")


def _wkt_geometry_type(wkt):
    """Tipo de la clase temporal, deducido del propio WKT."""
    head = (wkt or "").strip().upper()
    if head.startswith(("POINT", "MULTIPOINT")):
        return "POINT"
    if head.startswith(("LINESTRING", "MULTILINESTRING")):
        return "POLYLINE"
    return "POLYGON"


def _keys_of(description, role):
    return [
        key[0] for key in getattr(description, "originClassKeys", []) if key[1] == role
    ]


def _sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'%s'" % str(value).replace("'", "''")


def _sort_key(value):
    return str(value).upper() if value is not None else ""


def _spatial_reference_info(spatial_reference):
    if spatial_reference is None:
        return None
    name = getattr(spatial_reference, "name", "") or ""
    if name in ("Unknown", ""):
        return SpatialReferenceInfo()
    wkt = ""
    try:
        # ``exportToString`` agrega parametros de precision separados por ';';
        # el primer bloque es el WKT que entiende QGIS.
        wkt = spatial_reference.exportToString().split(";")[0]
    except Exception:
        wkt = getattr(spatial_reference, "exportToString", lambda: "")()
    return SpatialReferenceInfo(
        code=getattr(spatial_reference, "factoryCode", None) or None,
        name=name.replace("_", " "),
        wkt=wkt,
        is_geographic=(getattr(spatial_reference, "type", "") == "Geographic"),
    )
