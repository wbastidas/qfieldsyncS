# -*- coding: utf-8 -*-
"""Generador del archivo de proyecto que abre QField.

QField guarda su proyecto en un XML con extension ``.qgs``. Eso es un
**formato de archivo**, no una dependencia: este modulo lo escribe con
``xml.etree`` de la biblioteca estandar, y qfieldESRI no importa, no instala y
no necesita QGIS en ninguna parte. La prueba ``test_dependencias`` lo verifica
en cada ejecucion de la bateria.

El proyecto se construye entero desde el esquema de la geodatabase, que es la
unica fuente: aqui no hay ningun proyecto previo que transformar. Se traduce,
capa por capa:

* dominios de valores codificados -> ``ValueMap`` (o ``ValueRelation`` contra
  una tabla de catalogo, cuando el dominio es demasiado grande);
* dominios de rango                -> ``Range``;
* alias de campo                   -> ``aliases``;
* valores por defecto de subtipo    -> ``defaults``;
* subtipos                         -> simbolo por subtipo + ``ValueMap``;
* relationship classes             -> ``relations`` + pestana de hijos en el
  formulario del padre (el par Puesto/Unidad del modelo CNEL EP);
* campos no anulables              -> ``constraints``;
* categoria del campo              -> pestanas del formulario y visibilidad;
* opciones de campo                 -> propiedades ``QFieldSync/*``, que son
  las claves que lee **QField** en el dispositivo.
"""

import io
import json
import uuid
import xml.etree.ElementTree as ET

PROJECT_FORMAT_VERSION = "3.40.0-Bratislava"

#: Paleta de apoyo para el renderizado por subtipo (colores distinguibles en
#: pantalla de telefono a pleno sol).
PALETTE = (
    "228,26,28",
    "55,126,184",
    "77,175,74",
    "152,78,163",
    "255,127,0",
    "166,86,40",
    "247,129,191",
    "153,153,153",
    "0,150,136",
    "121,85,72",
)


def _color(index, alpha=255):
    return "%s,%d" % (PALETTE[index % len(PALETTE)], alpha)


# ----------------------------------------------------------------------
# serializacion del "Option map" del formato de proyecto
# ----------------------------------------------------------------------
def _option_value(parent, value, name=None):
    """Serializa un valor Python en el formato ``<Option>`` del proyecto."""
    attributes = {}
    if name is not None:
        attributes["name"] = name

    if isinstance(value, dict):
        element = ET.SubElement(parent, "Option", dict(attributes, type="Map"))
        for key in value:
            _option_value(element, value[key], name=key)
    elif isinstance(value, (list, tuple)):
        element = ET.SubElement(parent, "Option", dict(attributes, type="List"))
        for item in value:
            _option_value(element, item)
    elif isinstance(value, bool):
        attributes["type"] = "bool"
        attributes["value"] = "true" if value else "false"
        ET.SubElement(parent, "Option", attributes)
    elif isinstance(value, int):
        attributes["type"] = "int"
        attributes["value"] = str(value)
        ET.SubElement(parent, "Option", attributes)
    elif isinstance(value, float):
        attributes["type"] = "double"
        attributes["value"] = repr(value)
        ET.SubElement(parent, "Option", attributes)
    elif value is None:
        attributes["type"] = "invalid"
        ET.SubElement(parent, "Option", attributes)
    else:
        attributes["type"] = "QString"
        attributes["value"] = _text(value)
        ET.SubElement(parent, "Option", attributes)


def _option_map(parent, tag, mapping):
    element = ET.SubElement(parent, tag)
    _option_value(element, mapping)
    return element


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    try:
        return unicode(value)
    except NameError:
        return str(value)


def indent(element, level=0):
    """Sangrado en sitio (``ET.indent`` solo existe desde Python 3.9)."""
    pad = "\n" + "  " * level
    if len(element):
        if not element.text or not element.text.strip():
            element.text = pad + "  "
        for child in element:
            indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = pad
    if level and (not element.tail or not element.tail.strip()):
        element.tail = pad


# ----------------------------------------------------------------------
# descriptores que consume el escritor
# ----------------------------------------------------------------------
class WidgetSpec(object):
    """Widget de edicion de un campo en el formulario de QField."""

    def __init__(self, widget_type="TextEdit", config=None):
        self.type = widget_type
        self.config = dict(config or {})


class FieldSpec(object):
    """Un campo tal como se publica en el proyecto de QField."""

    def __init__(
        self,
        name,
        alias=None,
        widget=None,
        editable=True,
        not_null=False,
        default_expression=None,
        apply_default_on_update=False,
        group="General",
        hidden=False,
        comment="",
    ):
        self.name = name
        self.alias = alias or name
        self.widget = widget or WidgetSpec()
        self.editable = editable
        self.not_null = not_null
        self.default_expression = default_expression
        self.apply_default_on_update = apply_default_on_update
        #: nombre de la pestana del formulario donde cae el campo
        self.group = group
        self.hidden = hidden
        self.comment = comment


class LayerSpec(object):
    """Una capa del proyecto QField."""

    def __init__(
        self,
        table,
        title=None,
        geometry_type=None,
        wkb_type=None,
        fields=None,
        crs=None,
        extent=None,
        group=None,
        visible=True,
        read_only=False,
        allow_feature_addition=True,
        allow_feature_deletion=False,
        geometry_locked=False,
        display_expression=None,
        subtype_field=None,
        subtype_categories=None,
        color_index=0,
        layer_id=None,
    ):
        self.table = table
        self.title = title or table
        #: ``Point``, ``Line``, ``Polygon`` o ``None`` para tablas
        self.geometry_type = geometry_type
        #: ``Point``, ``MultiLineString``, ... como lo nombra el formato
        self.wkb_type = wkb_type
        self.fields = list(fields or [])
        self.crs = crs
        self.extent = extent
        self.group = group
        self.visible = visible
        self.read_only = read_only
        self.allow_feature_addition = allow_feature_addition
        self.allow_feature_deletion = allow_feature_deletion
        self.geometry_locked = geometry_locked
        self.display_expression = display_expression
        self.subtype_field = subtype_field
        #: lista de ``(codigo, etiqueta)`` para el renderizado categorizado
        self.subtype_categories = list(subtype_categories or [])
        self.color_index = color_index
        self.id = layer_id or "%s_%s" % (
            _sanitize_id(table),
            uuid.uuid4().hex[:16],
        )
        #: relaciones en las que esta capa es el padre; las rellena el escritor
        self.child_relations = []

    @property
    def is_spatial(self):
        return bool(self.geometry_type)

    def field(self, name):
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def field_index(self, name):
        for index, field in enumerate(self.fields):
            if field.name == name:
                return index
        return -1


class RelationSpec(object):
    """Una relationship class trasladada a una relacion del proyecto."""

    def __init__(
        self,
        name,
        parent_table,
        child_table,
        parent_field,
        child_field,
        label=None,
        strength="Association",
    ):
        self.name = name
        self.parent_table = parent_table
        self.child_table = child_table
        self.parent_field = parent_field
        self.child_field = child_field
        self.label = label or child_table
        #: ``Association`` o ``Composition`` (borrado en cascada en QField)
        self.strength = strength
        self.id = "%s_%s" % (_sanitize_id(name), uuid.uuid4().hex[:8])


def _sanitize_id(name):
    return "".join(char if char.isalnum() else "_" for char in name)


# ----------------------------------------------------------------------
# escritor
# ----------------------------------------------------------------------
class QFieldProjectWriter(object):
    """Construye el arbol XML de un ``.qgs`` listo para QField."""

    def __init__(
        self,
        title,
        crs,
        datasource="./data.gpkg",
        qfield_options=None,
        project_extent=None,
    ):
        self.title = title
        self.crs = crs
        self.datasource = datasource
        self.layers = []
        self.relations = []
        #: opciones que QField y QFieldSync leen del grupo ``qfieldsync``
        self.qfield_options = dict(qfield_options or {})
        self.project_extent = project_extent

    # -- construccion ---------------------------------------------------
    def add_layer(self, layer_spec):
        self.layers.append(layer_spec)
        return layer_spec

    def add_relation(self, relation_spec):
        self.relations.append(relation_spec)
        return relation_spec

    def layer_by_table(self, table):
        for layer in self.layers:
            if layer.table == table:
                return layer
        return None

    # -- salida ---------------------------------------------------------
    def write(self, path):
        root = self.build()
        indent(root)
        tree = ET.ElementTree(root)
        with io.open(path, "wb") as handle:
            handle.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            handle.write(
                b"<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
            )
            tree.write(handle, encoding="UTF-8")
        return path

    def to_string(self):
        root = self.build()
        indent(root)
        return ET.tostring(root, encoding="utf-8").decode("utf-8")

    def build(self):
        # Resolver las relaciones antes de escribir los formularios, para que
        # el padre sepa que pestanas de hijos tiene que mostrar.
        self._link_relations()

        root = ET.Element(
            "qgis",
            {
                "projectname": self.title,
                "version": PROJECT_FORMAT_VERSION,
                "saveUser": "qfieldesri",
                "saveUserFull": "qfieldESRI",
            },
        )
        ET.SubElement(root, "homePath", {"path": ""})
        title = ET.SubElement(root, "title")
        title.text = self.title
        ET.SubElement(root, "transaction", {"mode": "Disabled"})
        ET.SubElement(root, "projectFlags", {"set": ""})

        project_crs = ET.SubElement(root, "projectCrs")
        self._write_crs(project_crs, self.crs)

        self._write_layer_tree(root)
        self._write_snapping(root)
        self._write_relations(root)
        ET.SubElement(root, "polymorphicRelations")
        self._write_mapcanvas(root)
        ET.SubElement(root, "projectModels")
        ET.SubElement(root, "legend", {"updateDrawingOrder": "true"})
        ET.SubElement(root, "mapViewDocks")

        project_layers = ET.SubElement(root, "projectlayers")
        for layer in self.layers:
            self._write_maplayer(project_layers, layer)

        layer_order = ET.SubElement(root, "layerorder")
        for layer in self.layers:
            ET.SubElement(layer_order, "layer", {"id": layer.id})

        self._write_properties(root)
        ET.SubElement(root, "visibility-presets")
        ET.SubElement(root, "transformContext")
        self._write_metadata(root)
        ET.SubElement(root, "Annotations")
        ET.SubElement(root, "Layouts")
        ET.SubElement(root, "mapViewDocks3D")
        self._write_view_settings(root)
        ET.SubElement(root, "ProjectStyleSettings", {"projectStyleId": ""})
        ET.SubElement(
            root,
            "ProjectTimeSettings",
            {"timeStepUnit": "h", "timeStep": "1", "cumulativeTemporalRange": "0"},
        )
        ET.SubElement(root, "ElevationProperties")
        ET.SubElement(root, "ProjectDisplaySettings")
        ET.SubElement(root, "ProjectGpsSettings", {"autoAddTrackVertices": "0"})
        return root

    # -- piezas ---------------------------------------------------------
    def _link_relations(self):
        for layer in self.layers:
            layer.child_relations = []
        for relation in self.relations:
            parent = self.layer_by_table(relation.parent_table)
            if parent is not None:
                parent.child_relations.append(relation)

    def _write_crs(self, parent, crs):
        element = ET.SubElement(parent, "spatialrefsys", {"nativeFormat": "Wkt"})
        wkt = ET.SubElement(element, "wkt")
        wkt.text = getattr(crs, "wkt", "") or ""
        proj4 = ET.SubElement(element, "proj4")
        proj4.text = getattr(crs, "proj4", "") or ""
        srsid = ET.SubElement(element, "srsid")
        srsid.text = "0"
        srid = ET.SubElement(element, "srid")
        srid.text = str(getattr(crs, "code", 0) or 0)
        authid = ET.SubElement(element, "authid")
        authid.text = getattr(crs, "auth_id", "") or ""
        description = ET.SubElement(element, "description")
        description.text = getattr(crs, "name", "") or ""
        acronym = ET.SubElement(element, "projectionacronym")
        acronym.text = ""
        ellipsoid = ET.SubElement(element, "ellipsoidacronym")
        ellipsoid.text = ""
        geographic = ET.SubElement(element, "geographicflag")
        geographic.text = "true" if getattr(crs, "is_geographic", False) else "false"
        return element

    def _write_layer_tree(self, root):
        tree = ET.SubElement(
            root,
            "layer-tree-group",
            {"checked": "Qt::Checked", "expanded": "1", "name": ""},
        )
        _option_map(tree, "customproperties", {})

        groups = {}
        for layer in self.layers:
            container = tree
            if layer.group:
                if layer.group not in groups:
                    group_element = ET.SubElement(
                        tree,
                        "layer-tree-group",
                        {
                            "checked": "Qt::Checked",
                            "expanded": "1",
                            "name": layer.group,
                        },
                    )
                    _option_map(group_element, "customproperties", {})
                    groups[layer.group] = group_element
                container = groups[layer.group]
            node = ET.SubElement(
                container,
                "layer-tree-layer",
                {
                    "id": layer.id,
                    "name": layer.title,
                    "source": self._layer_source(layer),
                    "providerKey": "ogr",
                    "checked": "Qt::Checked" if layer.visible else "Qt::Unchecked",
                    "expanded": "0",
                    "legend_exp": "",
                    "legend_split_behavior": "0",
                    "patch_size": "-1,-1",
                },
            )
            _option_map(node, "customproperties", {})
        ET.SubElement(tree, "custom-order", {"enabled": "0"})

    def _layer_source(self, layer):
        return "%s|layername=%s" % (self.datasource, layer.table)

    def _write_snapping(self, root):
        snapping = ET.SubElement(
            root,
            "snapping-settings",
            {
                # El trazado de una red electrica exige enganchar a vertices y
                # segmentos: sin esto la conectividad se pierde en campo.
                "enabled": "1",
                "type": "1",
                "mode": "3",
                "unit": "1",
                "tolerance": "12",
                "intersection-snapping": "1",
                "self-snapping": "0",
                "scaleDependencyMode": "0",
                "minScale": "0",
                "maxScale": "0",
            },
        )
        ET.SubElement(snapping, "individual-layer-settings")

    def _write_relations(self, root):
        relations = ET.SubElement(root, "relations")
        for relation in self.relations:
            parent = self.layer_by_table(relation.parent_table)
            child = self.layer_by_table(relation.child_table)
            if parent is None or child is None:
                continue
            element = ET.SubElement(
                relations,
                "relation",
                {
                    "id": relation.id,
                    "name": relation.label,
                    "referencedLayer": parent.id,
                    "referencingLayer": child.id,
                    "strength": relation.strength,
                    "layerName": "",
                    "layerId": "",
                    "dataSource": "",
                    "providerKey": "",
                },
            )
            ET.SubElement(
                element,
                "fieldRef",
                {
                    "referencingField": relation.child_field,
                    "referencedField": relation.parent_field,
                },
            )

    def _write_mapcanvas(self, root):
        canvas = ET.SubElement(
            root, "mapcanvas", {"name": "theMapCanvas", "annotationsVisible": "1"}
        )
        units = ET.SubElement(canvas, "units")
        units.text = (
            "degrees" if getattr(self.crs, "is_geographic", False) else "meters"
        )
        self._write_extent(canvas, "extent", self.project_extent)
        rotation = ET.SubElement(canvas, "rotation")
        rotation.text = "0"
        destination = ET.SubElement(canvas, "destinationsrs")
        self._write_crs(destination, self.crs)
        rendermap = ET.SubElement(canvas, "rendermaptile")
        rendermap.text = "0"

    def _write_extent(self, parent, tag, extent):
        element = ET.SubElement(parent, tag)
        values = extent or (0.0, 0.0, 0.0, 0.0)
        for name, value in zip(("xmin", "ymin", "xmax", "ymax"), values):
            child = ET.SubElement(element, name)
            child.text = repr(float(value))
        return element

    # -- capas ----------------------------------------------------------
    def _write_maplayer(self, parent, layer):
        attributes = {
            "type": "vector",
            "styleCategories": "AllStyleCategories",
            "readOnly": "1" if layer.read_only else "0",
            "hasScaleBasedVisibilityFlag": "0",
            "minScale": "1e+08",
            "maxScale": "0",
            "simplifyDrawingHints": "1" if layer.geometry_type != "Point" else "0",
            "simplifyDrawingTol": "1",
            "simplifyAlgorithm": "0",
            "simplifyLocal": "1",
            "simplifyMaxScale": "1",
            "labelsEnabled": "0",
            "autoRefreshTime": "0",
            "autoRefreshMode": "Disabled",
            "refreshOnNotifyEnabled": "0",
            "refreshOnNotifyMessage": "",
        }
        if layer.geometry_type:
            attributes["geometry"] = layer.geometry_type
            attributes["wkbType"] = layer.wkb_type or layer.geometry_type
        else:
            attributes["geometry"] = "No geometry"
        element = ET.SubElement(parent, "maplayer", attributes)

        self._write_extent(element, "extent", layer.extent)
        identifier = ET.SubElement(element, "id")
        identifier.text = layer.id
        datasource = ET.SubElement(element, "datasource")
        datasource.text = self._layer_source(layer)
        keywords = ET.SubElement(element, "keywordList")
        ET.SubElement(keywords, "value").text = "qfieldesri"
        layername = ET.SubElement(element, "layername")
        layername.text = layer.title
        srs = ET.SubElement(element, "srs")
        self._write_crs(srs, layer.crs or self.crs)
        provider = ET.SubElement(element, "provider", {"encoding": "UTF-8"})
        provider.text = "ogr"

        _option_map(element, "customproperties", self._layer_custom_properties(layer))

        if layer.is_spatial:
            self._write_renderer(element, layer)
            ET.SubElement(element, "labeling", {"type": "simple"})
            blend = ET.SubElement(element, "blendMode")
            blend.text = "0"
            feature_blend = ET.SubElement(element, "featureBlendMode")
            feature_blend.text = "0"
            ET.SubElement(element, "layerOpacity").text = "1"

        self._write_field_configuration(element, layer)
        self._write_aliases(element, layer)
        self._write_defaults(element, layer)
        self._write_constraints(element, layer)
        ET.SubElement(element, "expressionfields")
        actions = ET.SubElement(element, "attributeactions")
        ET.SubElement(
            actions,
            "defaultAction",
            {"key": "Canvas", "value": "{00000000-0000-0000-0000-000000000000}"},
        )
        self._write_attributetableconfig(element, layer)
        self._write_field_flags(element, layer)
        ET.SubElement(element, "dataDefinedFieldProperties")
        ET.SubElement(element, "widgets")
        preview = ET.SubElement(element, "previewExpression")
        preview.text = layer.display_expression or ""
        ET.SubElement(element, "mapTip")
        editorlayout = ET.SubElement(element, "editorlayout")
        editorlayout.text = "tablayout"
        self._write_attribute_editor_form(element, layer)
        return element

    def _layer_custom_properties(self, layer):
        """Propiedades ``QFieldSync/*`` que interpretan QField y QFieldSync.

        Se reutilizan tal cual las claves de ``libqfieldsync`` para que un
        proyecto generado por qfieldESRI pueda seguir mantenido despues desde
        QGIS con QFieldSync, sin traducciones intermedias.
        """
        properties = {
            "QFieldSync/action": "copy",
            "QFieldSync/cloud_action": "offline",
            "QFieldSync/is_feature_addition_locked": not layer.allow_feature_addition,
            "QFieldSync/is_feature_deletion_locked": not layer.allow_feature_deletion,
            "QFieldSync/is_geometry_editing_locked": layer.geometry_locked,
            "QFieldSync/is_attribute_editing_locked": layer.read_only,
            "qfieldesri/source_table": layer.table,
        }
        attachment_naming = {}
        for field in layer.fields:
            if field.widget.type == "ExternalResource":
                attachment_naming[field.name] = field.widget.config.get(
                    "qfieldesri_naming", ""
                )
        if attachment_naming:
            properties["QFieldSync/attachment_naming"] = json.dumps(attachment_naming)
        return properties

    def _write_renderer(self, parent, layer):
        if layer.subtype_field and layer.subtype_categories:
            self._write_categorized_renderer(parent, layer)
        else:
            renderer = ET.SubElement(
                parent,
                "renderer-v2",
                {
                    "type": "singleSymbol",
                    "forceraster": "0",
                    "symbollevels": "0",
                    "enableorderby": "0",
                    "referencescale": "-1",
                },
            )
            symbols = ET.SubElement(renderer, "symbols")
            self._write_symbol(symbols, "0", layer.geometry_type, layer.color_index)

    def _write_categorized_renderer(self, parent, layer):
        renderer = ET.SubElement(
            parent,
            "renderer-v2",
            {
                "type": "categorizedSymbol",
                "attr": layer.subtype_field,
                "forceraster": "0",
                "symbollevels": "0",
                "enableorderby": "0",
                "referencescale": "-1",
            },
        )
        categories = ET.SubElement(renderer, "categories")
        symbols = ET.SubElement(renderer, "symbols")
        for index, (code, label) in enumerate(layer.subtype_categories):
            ET.SubElement(
                categories,
                "category",
                {
                    "render": "true",
                    "value": _text(code),
                    "label": _text(label),
                    "symbol": str(index),
                    "type": "long",
                    "uuid": str(uuid.uuid4()),
                },
            )
            self._write_symbol(
                symbols, str(index), layer.geometry_type, layer.color_index + index
            )
        source_symbol = ET.SubElement(renderer, "source-symbol")
        self._write_symbol(source_symbol, "0", layer.geometry_type, layer.color_index)

    def _write_symbol(self, parent, name, geometry_type, color_index):
        symbol_type = {
            "Point": "marker",
            "Line": "line",
            "Polygon": "fill",
        }.get(geometry_type, "marker")
        symbol = ET.SubElement(
            parent,
            "symbol",
            {
                "name": name,
                "type": symbol_type,
                "alpha": "1",
                "clip_to_extent": "1",
                "force_rhr": "0",
                "frame_rate": "10",
                "is_animated": "0",
            },
        )
        if symbol_type == "marker":
            layer_class, options = (
                "SimpleMarker",
                {
                    "name": "circle",
                    "color": _color(color_index),
                    "outline_color": "35,35,35,255",
                    "outline_style": "solid",
                    "outline_width": "0.2",
                    "outline_width_unit": "MM",
                    "size": "2.6",
                    "size_unit": "MM",
                    "angle": "0",
                    "offset": "0,0",
                    "offset_unit": "MM",
                    "scale_method": "diameter",
                    "horizontal_anchor_point": "1",
                    "vertical_anchor_point": "1",
                    "joinstyle": "bevel",
                },
            )
        elif symbol_type == "line":
            layer_class, options = (
                "SimpleLine",
                {
                    "line_color": _color(color_index),
                    "line_style": "solid",
                    "line_width": "0.66",
                    "line_width_unit": "MM",
                    "capstyle": "square",
                    "joinstyle": "bevel",
                    "offset": "0",
                    "offset_unit": "MM",
                    "use_custom_dash": "0",
                },
            )
        else:
            layer_class, options = (
                "SimpleFill",
                {
                    "color": _color(color_index, 100),
                    "style": "solid",
                    "outline_color": _color(color_index),
                    "outline_style": "solid",
                    "outline_width": "0.4",
                    "outline_width_unit": "MM",
                    "joinstyle": "bevel",
                    "offset": "0,0",
                    "offset_unit": "MM",
                },
            )
        symbol_layer = ET.SubElement(
            symbol,
            "layer",
            {"class": layer_class, "enabled": "1", "locked": "0", "pass": "0"},
        )
        _option_value(symbol_layer, options)
        return symbol

    def _write_field_configuration(self, parent, layer):
        configuration = ET.SubElement(parent, "fieldConfiguration")
        for field in layer.fields:
            element = ET.SubElement(
                configuration,
                "field",
                {
                    "name": field.name,
                    "configurationFlags": "NoFlag",
                },
            )
            widget = ET.SubElement(element, "editWidget", {"type": field.widget.type})
            config = {
                key: value
                for key, value in field.widget.config.items()
                if not key.startswith("qfieldesri_")
            }
            _option_map(widget, "config", config)

    def _write_aliases(self, parent, layer):
        aliases = ET.SubElement(parent, "aliases")
        for index, field in enumerate(layer.fields):
            ET.SubElement(
                aliases,
                "alias",
                {"field": field.name, "index": str(index), "name": field.alias},
            )

    def _write_defaults(self, parent, layer):
        defaults = ET.SubElement(parent, "defaults")
        for field in layer.fields:
            ET.SubElement(
                defaults,
                "default",
                {
                    "field": field.name,
                    "expression": field.default_expression or "",
                    "applyOnUpdate": "1" if field.apply_default_on_update else "0",
                },
            )

    def _write_constraints(self, parent, layer):
        constraints = ET.SubElement(parent, "constraints")
        for field in layer.fields:
            # 1 = NotNull, 2 = Unique, 4 = Expression (banderas de QGIS)
            flags = 1 if field.not_null else 0
            ET.SubElement(
                constraints,
                "constraint",
                {
                    "field": field.name,
                    "constraints": str(flags),
                    "notnull_strength": "1" if field.not_null else "0",
                    "unique_strength": "0",
                    "exp_strength": "0",
                },
            )
        expressions = ET.SubElement(parent, "constraintExpressions")
        for field in layer.fields:
            ET.SubElement(
                expressions, "constraint", {"field": field.name, "desc": "", "exp": ""}
            )

    def _write_attributetableconfig(self, parent, layer):
        config = ET.SubElement(
            parent,
            "attributetableconfig",
            {"actionWidgetStyle": "dropDown", "sortExpression": "", "sortOrder": "0"},
        )
        columns = ET.SubElement(config, "columns")
        for field in layer.fields:
            ET.SubElement(
                columns,
                "column",
                {
                    "name": field.name,
                    "type": "field",
                    "hidden": "1" if field.hidden else "0",
                    "width": "-1",
                },
            )
        ET.SubElement(
            columns, "column", {"type": "actions", "hidden": "1", "width": "-1"}
        )

    def _write_field_flags(self, parent, layer):
        editable = ET.SubElement(parent, "editable")
        label_on_top = ET.SubElement(parent, "labelOnTop")
        reuse = ET.SubElement(parent, "reuseLastValue")
        for field in layer.fields:
            is_editable = field.editable and not layer.read_only
            ET.SubElement(
                editable,
                "field",
                {"name": field.name, "editable": "1" if is_editable else "0"},
            )
            ET.SubElement(
                label_on_top, "field", {"name": field.name, "labelOnTop": "0"}
            )
            # Reutilizar el ultimo valor ahorra muchisimo tecleo en campo para
            # los campos administrativos que se repiten en toda una jornada.
            reuse_value = field.group in ("Sistema", "Ubicacion")
            ET.SubElement(
                reuse,
                "field",
                {"name": field.name, "reuseLastValue": "1" if reuse_value else "0"},
            )

    def _write_attribute_editor_form(self, parent, layer):
        form = ET.SubElement(parent, "attributeEditorForm")
        groups = []
        for field in layer.fields:
            if field.hidden:
                continue
            if field.group not in groups:
                groups.append(field.group)

        for group in groups:
            container = ET.SubElement(
                form,
                "attributeEditorContainer",
                {
                    "name": group,
                    "columnCount": "1",
                    "groupBox": "0",
                    "visibilityExpressionEnabled": "0",
                    "collapsed": "0",
                    "collapsedExpressionEnabled": "0",
                    "showLabel": "1",
                    "type": "Tab",
                },
            )
            for field in layer.fields:
                if field.hidden or field.group != group:
                    continue
                ET.SubElement(
                    container,
                    "attributeEditorField",
                    {
                        "name": field.name,
                        "index": str(layer.field_index(field.name)),
                        "showLabel": "1",
                    },
                )

        for relation in layer.child_relations:
            container = ET.SubElement(
                form,
                "attributeEditorContainer",
                {
                    "name": relation.label,
                    "columnCount": "1",
                    "groupBox": "0",
                    "visibilityExpressionEnabled": "0",
                    "collapsed": "0",
                    "collapsedExpressionEnabled": "0",
                    "showLabel": "1",
                    "type": "Tab",
                },
            )
            ET.SubElement(
                container,
                "attributeEditorRelation",
                {
                    "name": relation.label,
                    "relation": relation.id,
                    "showLabel": "1",
                    "nmRelationId": "",
                    "forceSuppressFormPopup": "0",
                    "relationWidgetTypeId": "relation_editor",
                    "label": relation.label,
                },
            )

    # -- propiedades del proyecto ---------------------------------------
    def _write_properties(self, root):
        properties = ET.SubElement(root, "properties")

        qfieldsync = ET.SubElement(properties, "qfieldsync")
        for key in sorted(self.qfield_options):
            self._write_property(qfieldsync, key, self.qfield_options[key])

        paths = ET.SubElement(properties, "Paths")
        self._write_property(paths, "Absolute", False)

        spatial = ET.SubElement(properties, "SpatialRefSys")
        self._write_property(spatial, "ProjectionsEnabled", 1)

        measure = ET.SubElement(properties, "Measure")
        self._write_property(measure, "Ellipsoid", "EPSG:7030")

        position = ET.SubElement(properties, "PositionPrecision")
        self._write_property(position, "Automatic", True)
        self._write_property(position, "DecimalPlaces", 3)

        variables = ET.SubElement(properties, "Variables")
        self._write_property(variables, "variableNames", ["qfieldesri_generated"])
        self._write_property(variables, "variableValues", ["true"])

    def _write_property(self, parent, name, value):
        if isinstance(value, bool):
            element = ET.SubElement(parent, name, {"type": "bool"})
            element.text = "true" if value else "false"
        elif isinstance(value, int):
            element = ET.SubElement(parent, name, {"type": "int"})
            element.text = str(value)
        elif isinstance(value, float):
            element = ET.SubElement(parent, name, {"type": "double"})
            element.text = repr(value)
        elif isinstance(value, (list, tuple)):
            element = ET.SubElement(parent, name, {"type": "QStringList"})
            for item in value:
                ET.SubElement(element, "value").text = _text(item)
        else:
            element = ET.SubElement(parent, name, {"type": "QString"})
            element.text = _text(value)
        return element

    def _write_metadata(self, root):
        metadata = ET.SubElement(root, "projectMetadata")
        for tag, text in (
            ("identifier", self.title),
            ("parentidentifier", ""),
            ("language", "ES"),
            ("type", "dataset"),
            ("title", self.title),
            (
                "abstract",
                "Proyecto generado por qfieldESRI a partir de una geodatabase "
                "de ESRI para su uso en QField.",
            ),
        ):
            element = ET.SubElement(metadata, tag)
            element.text = text
        ET.SubElement(metadata, "links")
        ET.SubElement(metadata, "history").text = "Generado por qfieldESRI"

    def _write_view_settings(self, root):
        settings = ET.SubElement(
            root,
            "ProjectViewSettings",
            {
                "UseProjectScales": "0",
                "rotation": "0",
                "mapScales": "",
            },
        )
        self._write_extent(settings, "DefaultViewExtent", self.project_extent)
