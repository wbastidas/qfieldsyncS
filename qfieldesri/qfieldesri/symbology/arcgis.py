# -*- coding: utf-8 -*-
"""Lee simbologia de un MXD o un ``.lyr`` con arcpy.

Lo que se puede y lo que no
---------------------------
Un MXD de ArcMap y un ``.lyr`` son **binarios**, y ``arcpy.mapping`` expone de
ellos la **clasificacion** —el campo por el que se clasifica, los valores y sus
rotulos— pero **no los colores**. No es una limitacion de qfieldESRI: la API de
ArcGIS no los publica.

Por eso, de esta via sale la *estructura* de la simbologia con una paleta
propia, y se avisa. Si hace falta la simbologia exacta de la oficina, el camino
es exportar los archivos de capa como ``.lyrx`` desde ArcGIS Pro: ese formato es
JSON y trae los colores completos (ver :mod:`qfieldesri.symbology.lyrx`).

En ArcGIS Pro este modulo si recupera los colores, porque ``arcpy.mp`` permite
pedir la definicion CIM de la capa y esa se procesa con el mismo lector del
``.lyrx``.

Ademas del documento guardado, se puede leer el **mapa abierto en ese momento**
(``read_active_document``). Es el caso normal: el tecnico ya tiene su MXD o su
proyecto de Pro cargado con la simbologia puesta, y lo que quiere es llevarsela
tal cual sin exportar nada antes.
"""

import os

from . import defaults
from .model import Category, Label, LayerStyle, Renderer

SOURCE = "arcpy"

#: Ruta simbolica que ArcGIS entiende como "el documento abierto ahora".
CURRENT = "CURRENT"


class ArcGISSymbologyError(Exception):
    pass


class ArcGISImport(object):
    def __init__(self, path):
        self.path = path
        self.styles = {}
        self.warnings = []

    def add_warning(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    def __len__(self):
        return len(self.styles)


def read_active_document():
    """Lee la simbologia del mapa que ArcGIS tiene abierto ahora mismo.

    Es la via que evita el paso previo de exportar: si el MXD o el proyecto de
    Pro ya esta en pantalla con la simbologia de la oficina, se toma de ahi.
    """
    return read_arcgis_document(CURRENT)


def read_arcgis_document(path):
    """Lee un ``.lyr``, un ``.mxd``, un ``.aprx`` o el documento abierto."""
    if path != CURRENT and not os.path.isfile(path):
        raise ArcGISSymbologyError("No se encuentra el documento: %s" % path)

    import arcpy

    result = ArcGISImport(path)
    layers, pro = _list_layers(arcpy, path, result)
    for layer in layers:
        try:
            name, style = _read_layer(arcpy, layer, pro, result)
        except Exception as error:
            result.add_warning("No se pudo leer una capa de '%s': %s" % (path, error))
            continue
        if name and style is not None:
            result.styles[name] = style
    return result


def _list_layers(arcpy, path, result):
    """Devuelve ``(capas, es_pro)`` segun la version de ArcGIS presente."""
    if hasattr(arcpy, "mp"):
        # ArcGIS Pro
        if path == CURRENT:
            layers = []
            for map_ in arcpy.mp.ArcGISProject(CURRENT).listMaps():
                layers.extend(map_.listLayers())
            return layers, True
        if path.lower().endswith(".lyrx"):
            return list(arcpy.mp.LayerFile(path).listLayers()), True
        if path.lower().endswith(".aprx"):
            layers = []
            for map_ in arcpy.mp.ArcGISProject(path).listMaps():
                layers.extend(map_.listLayers())
            return layers, True
        result.add_warning(
            "ArcGIS Pro no abre archivos .mxd ni .lyr: exporte la simbologia "
            "como .lyrx."
        )
        return [], True

    if hasattr(arcpy, "mapping"):
        # ArcMap 10.x
        result.add_warning(
            "De un MXD o un .lyr, ArcGIS solo publica la clasificacion, no los "
            "colores: se conservan las clases y sus rotulos, y los colores se "
            "asignan con la paleta de qfieldESRI. Para trasladar los colores "
            "exactos, exporte las capas como .lyrx desde ArcGIS Pro."
        )
        if path.lower().endswith(".lyr"):
            # ``Layer`` devuelve **una** capa, no una lista; y si el archivo es
            # una capa de grupo, las hijas solo aparecen a traves de
            # ``ListLayers``.
            return list(arcpy.mapping.ListLayers(arcpy.mapping.Layer(path))), False
        # ``CURRENT`` es el documento abierto en ArcMap en este momento.
        document = arcpy.mapping.MapDocument(path)
        return list(arcpy.mapping.ListLayers(document)), False

    raise ArcGISSymbologyError(
        "Esta version de arcpy no expone ni 'arcpy.mp' ni 'arcpy.mapping'."
    )


def _read_layer(arcpy, layer, pro, result):
    name = _layer_name(layer)
    if not getattr(layer, "isFeatureLayer", True):
        return name, None

    if pro:
        style = _read_from_cim(layer, result)
        if style is not None:
            return name, style

    return name, _read_from_mapping(arcpy, layer, name, result)


def _layer_name(layer):
    """Nombre de la clase de origen, no el rotulo del mapa.

    En el mapa la capa puede llamarse "Postes de hormigon" y venir de la clase
    ``EstructuraSoporte``; lo que hay que casar es el nombre de la clase.

    Se conserva la calificacion cuando la hay (``SIGELEC.TRAMOMT``): identifica
    mejor la clase, y quien busca despues sabe comparar con el nombre corto. La
    ruta se parte a mano porque ``os.path.basename`` no reconoce la barra
    invertida cuando el programa no corre en Windows, y una ruta de ArcGIS
    siempre la lleva.
    """
    source = getattr(layer, "dataSource", None) or ""
    if source:
        return _last_segment(source)
    return getattr(layer, "name", "") or ""


def _last_segment(path):
    """Ultimo tramo de una ruta, con cualquiera de las dos barras."""
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def _read_from_cim(layer, result):
    """En ArcGIS Pro la definicion CIM trae los colores completos."""
    try:
        definition = layer.getDefinition("V3")
    except Exception:
        return None

    payload = _cim_to_dict(definition)
    if not payload:
        return None

    from .lyrx import _read_layer as read_cim_layer

    try:
        return read_cim_layer(payload, result)
    except Exception as error:
        result.add_warning("No se pudo interpretar el CIM de una capa: %s" % error)
        return None


def _cim_to_dict(definition):
    """Convierte el objeto CIM de arcpy en el diccionario que espera el lector."""
    import json

    for attribute in ("toJSON", "__str__"):
        method = getattr(definition, attribute, None)
        if method is None:
            continue
        try:
            payload = json.loads(method())
        except Exception:  # noqa: S112 - se prueba la siguiente forma de leerlo
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _read_from_mapping(arcpy, layer, name, result):
    """Camino de ArcMap: solo la clasificacion."""
    if not getattr(layer, "symbologyType", None):
        return None
    try:
        symbology = layer.symbology
    except Exception:
        return None

    geometry_type = _geometry_type(arcpy, layer)
    kind = getattr(layer, "symbologyType", "")

    if kind == "UNIQUE_VALUES":
        field = (getattr(symbology, "valueField", "") or "").strip()
        values = list(getattr(symbology, "classValues", []) or [])
        labels = list(getattr(symbology, "classLabels", []) or [])
        if field and values:
            categories = []
            for index, value in enumerate(values):
                label = labels[index] if index < len(labels) else str(value)
                categories.append(
                    Category(
                        value=value,
                        label=label,
                        symbol=defaults._symbol(
                            geometry_type,
                            None,
                            defaults.color_for(name, index * 3 + 1),
                        ),
                    )
                )
            renderer = Renderer(
                Renderer.CATEGORIZED,
                field=field,
                categories=categories,
                source=SOURCE,
            )
            return LayerStyle(renderer=renderer, label=_label_of(layer))

    if kind == "GRADUATED_COLORS":
        result.add_warning(
            "'%s' usa colores graduados; se traslado la clasificacion con la "
            "paleta de qfieldESRI." % name
        )
        field = (getattr(symbology, "valueField", "") or "").strip()
        breaks = list(getattr(symbology, "classBreakValues", []) or [])
        labels = list(getattr(symbology, "classBreakLabels", []) or [])
        if field and len(breaks) > 1:
            from .model import Range

            ranges = []
            for index in range(len(breaks) - 1):
                label = labels[index] if index < len(labels) else ""
                ranges.append(
                    Range(
                        lower=breaks[index],
                        upper=breaks[index + 1],
                        label=label or "%s - %s" % (breaks[index], breaks[index + 1]),
                        symbol=defaults._symbol(
                            geometry_type,
                            None,
                            defaults.color_for(name, index * 2 + 1),
                        ),
                    )
                )
            renderer = Renderer(
                Renderer.GRADUATED, field=field, ranges=ranges, source=SOURCE
            )
            return LayerStyle(renderer=renderer, label=_label_of(layer))

    return None


def _geometry_type(arcpy, layer):
    try:
        shape = arcpy.Describe(layer.dataSource).shapeType
    except Exception:
        return "Point"
    return {
        "Polyline": "Line",
        "Polygon": "Polygon",
        "Point": "Point",
        "Multipoint": "Point",
    }.get(shape, "Point")


def _label_of(layer):
    """Etiquetado de la capa, en lo que ArcMap deja ver."""
    if not getattr(layer, "supports", lambda _name: False)("LABELCLASSES"):
        return None
    try:
        classes = layer.labelClasses
    except Exception:
        return None
    if not classes:
        return None

    expression = (getattr(classes[0], "expression", "") or "").strip()
    field = expression.strip("[]").replace('"', "").strip()
    if not field or not field.replace("_", "").isalnum():
        return None
    return Label(
        field=field,
        min_scale=defaults.LABEL_MIN_SCALE,
        enabled=bool(getattr(layer, "showLabels", False)),
    )
