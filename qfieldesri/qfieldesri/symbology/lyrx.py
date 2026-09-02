# -*- coding: utf-8 -*-
"""Importa la simbologia de un archivo ``.lyrx`` de ArcGIS Pro.

Por que este es el camino bueno
-------------------------------
Un ``.lyrx`` **es un JSON**: el CIM (*Cartographic Information Model*) de
ArcGIS. Se puede leer con la biblioteca estandar, sin ArcGIS y sin licencia, y
trae la simbologia **completa**: colores exactos, grosores, formas de marcador,
patrones de guiones, clases del renderizador y etiquetas.

Los otros formatos no dan eso. Un ``.lyr`` o un ``.mxd`` de ArcMap son binarios
y lo unico que expone ``arcpy.mapping`` es la *clasificacion* (campo, valores y
rotulos), nunca los colores. De ahi la recomendacion practica: si hay que
llevarse la simbologia de la oficina al campo tal cual, **guardela como
``.lyrx``** (en ArcGIS Pro: clic derecho en la capa > *Compartir* > *Guardar
como archivo de capa*).

Que se traduce
--------------
=========================  ==========================================
CIM                        Modelo de qfieldESRI
=========================  ==========================================
CIMSimpleRenderer          renderizador simple
CIMUniqueValueRenderer     renderizador categorizado
CIMClassBreaksRenderer     renderizador graduado
CIMSolidStroke             capa de linea (con guiones si trae efecto)
CIMSolidFill               capa de relleno
CIMVectorMarker            marcador, con la forma deducida de su geometria
CIMCharacterMarker         marcador (se aproxima por forma)
labelClasses               etiquetado
minScale / maxScale        visibilidad por escala
=========================  ==========================================

Lo que el CIM tenga y QField no pueda representar se aproxima a lo mas cercano
y se anota en las advertencias, en vez de fallar: mas vale una capa con un
simbolo parecido que un empaquetado abortado.
"""

import io
import json
import os

from .model import (
    Category,
    Color,
    FillStyle,
    Label,
    LayerStyle,
    LineStyle,
    MarkerShape,
    Range,
    Renderer,
    Symbol,
    SymbolLayer,
    points_to_mm,
)

SOURCE = "lyrx"

#: Formas de ArcGIS -> formas del destino. El CIM no nombra la forma: hay que
#: deducirla del numero de vertices de la geometria del marcador vectorial.
_VERTEX_SHAPES = {
    3: MarkerShape.EQUILATERAL_TRIANGLE,
    4: MarkerShape.SQUARE,
    5: MarkerShape.PENTAGON,
    6: MarkerShape.HEXAGON,
}

#: Nombres que ArcGIS suele dar a sus marcadores de caracter, por si vienen.
_NAMED_SHAPES = {
    "circle": MarkerShape.CIRCLE,
    "square": MarkerShape.SQUARE,
    "diamond": MarkerShape.DIAMOND,
    "triangle": MarkerShape.TRIANGLE,
    "star": MarkerShape.STAR,
    "cross": MarkerShape.CROSS,
    "x": MarkerShape.CROSS2,
    "pentagon": MarkerShape.PENTAGON,
    "hexagon": MarkerShape.HEXAGON,
}


class LyrxError(Exception):
    pass


class LyrxImport(object):
    """Resultado de leer un ``.lyrx``."""

    def __init__(self, path):
        self.path = path
        #: ``{nombre_de_capa: LayerStyle}``
        self.styles = {}
        self.warnings = []

    def add_warning(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    def __len__(self):
        return len(self.styles)

    def __repr__(self):  # pragma: no cover
        return "<LyrxImport %s (%d capas)>" % (
            os.path.basename(self.path),
            len(self.styles),
        )


# ----------------------------------------------------------------------
def read_lyrx(path):
    """Lee un ``.lyrx`` y devuelve un :class:`LyrxImport`."""
    if not os.path.isfile(path):
        raise LyrxError("No se encuentra el archivo de capa: %s" % path)
    with io.open(path, "r", encoding="utf-8-sig") as handle:
        try:
            document = json.load(handle)
        except ValueError as error:
            raise LyrxError(
                "'%s' no es un .lyrx valido (se esperaba JSON del CIM): %s"
                % (os.path.basename(path), error)
            )

    result = LyrxImport(path)
    definitions = document.get("layerDefinitions") or []
    if not definitions:
        result.add_warning(
            "El archivo no declara ninguna capa ('layerDefinitions' vacio)."
        )
        return result

    for definition in definitions:
        if definition.get("type") not in ("CIMFeatureLayer", None):
            continue
        name = definition.get("name")
        if not name:
            continue
        style = _read_layer(definition, result)
        if style is not None:
            result.styles[name] = style
    return result


def read_lyrx_folder(folder):
    """Lee todos los ``.lyrx`` de una carpeta y los une en un solo resultado.

    Es el modo comodo: la oficina deja en una carpeta un archivo de capa por
    clase, con el mismo nombre que la clase, y qfieldESRI los recoge todos.
    """
    if not os.path.isdir(folder):
        raise LyrxError("No es una carpeta: %s" % folder)

    result = LyrxImport(folder)
    for filename in sorted(os.listdir(folder)):
        if not filename.lower().endswith(".lyrx"):
            continue
        path = os.path.join(folder, filename)
        try:
            single = read_lyrx(path)
        except LyrxError as error:
            result.add_warning(str(error))
            continue
        for name, style in single.styles.items():
            result.styles.setdefault(name, style)
        # El nombre del archivo tambien vale como nombre de clase, que es lo
        # habitual cuando se exporta "una capa por clase".
        stem = os.path.splitext(filename)[0]
        if stem not in result.styles and single.styles:
            result.styles[stem] = next(iter(single.styles.values()))
        for warning in single.warnings:
            result.add_warning(warning)
    return result


# ----------------------------------------------------------------------
def _read_layer(definition, result):
    renderer = _read_renderer(definition.get("renderer"), result)
    if renderer is None:
        return None
    label = _read_label(definition, result)

    opacity = 1.0
    transparency = definition.get("layerTransparency")
    if transparency:
        opacity = max(0.0, 1.0 - float(transparency) / 100.0)

    return LayerStyle(
        renderer=renderer,
        label=label,
        # En el CIM, ``minScale`` es el denominador mas alejado y ``maxScale``
        # el mas cercano, igual que en el destino.
        min_scale=_scale(definition.get("minScale")),
        max_scale=_scale(definition.get("maxScale")),
        opacity=opacity,
        visible=definition.get("visibility", True),
    )


def _scale(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _read_renderer(cim, result):
    if not cim:
        return None
    kind = cim.get("type")

    if kind == "CIMSimpleRenderer":
        symbol = _read_symbol_reference(cim.get("symbol"), result)
        if symbol is None:
            return None
        return Renderer(Renderer.SINGLE, symbol=symbol, source=SOURCE)

    if kind == "CIMUniqueValueRenderer":
        return _read_unique_value(cim, result)

    if kind == "CIMClassBreaksRenderer":
        return _read_class_breaks(cim, result)

    result.add_warning(
        "Renderizador '%s' no soportado: se usara un simbolo unico." % kind
    )
    return None


def _read_unique_value(cim, result):
    fields = cim.get("fields") or []
    if not fields:
        result.add_warning(
            "Un renderizador por valores unicos no declara campo; se omite."
        )
        return None
    if len(fields) > 1:
        result.add_warning(
            "El renderizador clasifica por varios campos (%s); QField solo "
            "admite uno, se usara '%s'." % (", ".join(fields), fields[0])
        )

    categories = []
    for group in cim.get("groups") or []:
        for entry in group.get("classes") or []:
            symbol = _read_symbol_reference(entry.get("symbol"), result)
            if symbol is None:
                continue
            for value in _class_values(entry):
                categories.append(
                    Category(
                        value=value,
                        label=entry.get("label") or value,
                        symbol=symbol,
                        render=entry.get("visible", True),
                    )
                )

    if not categories:
        return None

    if cim.get("useDefaultSymbol") and cim.get("defaultSymbol"):
        default = _read_symbol_reference(cim.get("defaultSymbol"), result)
        if default is not None:
            # En el destino, la categoria de valor vacio hace de "resto".
            categories.append(
                Category("", cim.get("defaultLabel") or "Otros", default)
            )

    return Renderer(
        Renderer.CATEGORIZED,
        field=fields[0],
        categories=categories,
        source=SOURCE,
    )


def _class_values(entry):
    """Valores de una clase del renderizador, aplanando la forma del CIM."""
    values = []
    for value in entry.get("values") or []:
        field_values = value.get("fieldValues") if isinstance(value, dict) else None
        if field_values:
            values.append(field_values[0])
        elif isinstance(value, (str, int, float)):
            values.append(value)
    return values


def _read_class_breaks(cim, result):
    field = cim.get("field")
    if not field:
        result.add_warning("Un renderizador graduado no declara campo; se omite.")
        return None

    ranges = []
    lower = cim.get("minimumBreak", 0) or 0
    for entry in cim.get("breaks") or []:
        symbol = _read_symbol_reference(entry.get("symbol"), result)
        if symbol is None:
            continue
        upper = entry.get("upperBound", 0)
        ranges.append(
            Range(
                lower=lower,
                upper=upper,
                label=entry.get("label") or "%s - %s" % (lower, upper),
                symbol=symbol,
            )
        )
        lower = upper

    if not ranges:
        return None
    return Renderer(
        Renderer.GRADUATED, field=field, ranges=ranges, source=SOURCE
    )


# ----------------------------------------------------------------------
# simbolos
# ----------------------------------------------------------------------
def _read_symbol_reference(reference, result):
    """Desenvuelve un ``CIMSymbolReference`` y traduce el simbolo."""
    if not reference:
        return None
    symbol = reference.get("symbol") if isinstance(reference, dict) else None
    if symbol is None:
        symbol = reference
    return _read_symbol(symbol, result)


def _read_symbol(cim, result):
    if not isinstance(cim, dict):
        return None
    kind = cim.get("type")
    layers = []

    if kind == "CIMPointSymbol":
        symbol_type = Symbol.MARKER
    elif kind == "CIMLineSymbol":
        symbol_type = Symbol.LINE
    elif kind == "CIMPolygonSymbol":
        symbol_type = Symbol.FILL
    elif kind == "CIMTextSymbol":
        return None  # el texto se trata como etiqueta, no como simbolo
    else:
        result.add_warning("Tipo de simbolo no soportado: %s" % kind)
        return None

    for entry in cim.get("symbolLayers") or []:
        if entry.get("enable") is False:
            continue
        layer = _read_symbol_layer(entry, result)
        if layer is not None:
            layers.append(layer)

    if not layers:
        return None

    # El CIM apila de arriba hacia abajo; el destino, al reves.
    layers.reverse()
    return Symbol(symbol_type, layers)


def _read_symbol_layer(cim, result):
    kind = cim.get("type")

    if kind == "CIMSolidStroke":
        style, custom_dash = _dash_of(cim)
        # Dentro de un simbolo de relleno el trazo es el borde, pero en este
        # modelo se representa igual: una capa de linea.
        return SymbolLayer(
            SymbolLayer.LINE,
            color=_read_color(cim.get("color")) or Color(0, 0, 0),
            width=points_to_mm(cim.get("width"), 0.26),
            style=style,
            custom_dash=custom_dash,
        )

    if kind == "CIMSolidFill":
        return SymbolLayer(
            SymbolLayer.FILL,
            color=_read_color(cim.get("color")) or Color(200, 200, 200),
            style=FillStyle.SOLID,
        )

    if kind == "CIMHatchFill":
        return SymbolLayer(
            SymbolLayer.FILL,
            color=_read_color(_hatch_color(cim)) or Color(120, 120, 120, 120),
            style=_hatch_style(cim.get("rotation", 0)),
        )

    if kind == "CIMVectorMarker":
        return _read_vector_marker(cim, result)

    if kind == "CIMCharacterMarker":
        return _read_character_marker(cim)

    if kind == "CIMPictureMarker":
        result.add_warning(
            "Un marcador de imagen se aproximo con un circulo: las imagenes "
            "incrustadas del CIM no se trasladan."
        )
        return SymbolLayer(
            SymbolLayer.MARKER,
            shape=MarkerShape.CIRCLE,
            color=Color(120, 120, 120),
            size=points_to_mm(cim.get("size"), 2.6),
            outline_color=Color(35, 35, 35),
            outline_width=0.2,
        )

    result.add_warning("Capa de simbolo no soportada: %s" % kind)
    return None


def _dash_of(cim):
    """Traduce el efecto de guiones de un trazo."""
    for effect in cim.get("effects") or []:
        if effect.get("type") != "CIMGeometricEffectDashes":
            continue
        template = effect.get("dashTemplate") or []
        if not template:
            continue
        millimeters = [points_to_mm(value, 1.0) for value in template]
        # Dos valores iguales y cortos son un punteado; el resto, guiones.
        if len(millimeters) == 2 and millimeters[0] <= 0.6:
            return LineStyle.DOT, None
        return LineStyle.DASH, ";".join("%.3g" % value for value in millimeters)
    return LineStyle.SOLID, None


def _hatch_color(cim):
    for layer in (cim.get("lineSymbol") or {}).get("symbolLayers") or []:
        if layer.get("color"):
            return layer["color"]
    return None


def _hatch_style(rotation):
    try:
        rotation = float(rotation) % 180
    except (TypeError, ValueError):
        rotation = 0
    if 22.5 <= rotation < 67.5:
        return FillStyle.F_DIAGONAL
    if 67.5 <= rotation < 112.5:
        return FillStyle.VERTICAL
    if 112.5 <= rotation < 157.5:
        return FillStyle.B_DIAGONAL
    return FillStyle.HORIZONTAL


def _read_vector_marker(cim, result):
    """Un marcador vectorial: la forma se deduce de su geometria."""
    graphics = cim.get("markerGraphics") or []
    shape = MarkerShape.CIRCLE
    color = None
    outline_color = None
    outline_width = 0.2

    if graphics:
        geometry = graphics[0].get("geometry") or {}
        shape = _shape_of_geometry(geometry)
        inner = graphics[0].get("symbol") or {}
        for entry in inner.get("symbolLayers") or []:
            if entry.get("type") == "CIMSolidFill" and color is None:
                color = _read_color(entry.get("color"))
            elif entry.get("type") == "CIMSolidStroke" and outline_color is None:
                outline_color = _read_color(entry.get("color"))
                outline_width = points_to_mm(entry.get("width"), 0.2)
    else:
        result.add_warning(
            "Un marcador vectorial no trae geometria; se aproximo con un circulo."
        )

    return SymbolLayer(
        SymbolLayer.MARKER,
        shape=shape,
        color=color or Color(200, 60, 60),
        size=points_to_mm(cim.get("size"), 2.6),
        outline_color=outline_color or Color(35, 35, 35),
        outline_width=outline_width,
        angle=cim.get("rotation", 0) or 0,
    )


def _shape_of_geometry(geometry):
    """Deduce la forma a partir de la geometria del marcador."""
    if not isinstance(geometry, dict):
        return MarkerShape.CIRCLE
    if "curveRings" in geometry or "x" in geometry:
        return MarkerShape.CIRCLE
    rings = geometry.get("rings") or []
    if rings:
        # El primer anillo cierra repitiendo el vertice inicial.
        vertices = len(rings[0]) - 1
        if vertices == 4:
            return _square_or_diamond(rings[0])
        return _VERTEX_SHAPES.get(vertices, MarkerShape.CIRCLE)
    if geometry.get("paths"):
        return MarkerShape.CROSS
    return MarkerShape.CIRCLE


def _square_or_diamond(ring):
    """Un cuadrado girado 45 grados es, en la practica, un rombo."""
    try:
        xs = [point[0] for point in ring[:4]]
        ys = [point[1] for point in ring[:4]]
    except (IndexError, TypeError):
        return MarkerShape.SQUARE
    # En un rombo, cada vertice esta alineado con el centro en un solo eje.
    center_x = sum(xs) / 4.0
    center_y = sum(ys) / 4.0
    aligned = sum(
        1
        for x, y in zip(xs, ys)
        if abs(x - center_x) < 1e-6 or abs(y - center_y) < 1e-6
    )
    return MarkerShape.DIAMOND if aligned >= 4 else MarkerShape.SQUARE


def _read_character_marker(cim):
    name = (cim.get("fontFamilyName") or "").lower()
    shape = MarkerShape.CIRCLE
    for key, value in _NAMED_SHAPES.items():
        if key in name:
            shape = value
            break
    color = None
    for entry in (cim.get("symbol") or {}).get("symbolLayers") or []:
        if entry.get("type") == "CIMSolidFill":
            color = _read_color(entry.get("color"))
            break
    return SymbolLayer(
        SymbolLayer.MARKER,
        shape=shape,
        color=color or Color(60, 60, 60),
        size=points_to_mm(cim.get("size"), 2.6),
        outline_color=Color(35, 35, 35),
        outline_width=0.0,
        angle=cim.get("rotation", 0) or 0,
    )


# ----------------------------------------------------------------------
# colores
# ----------------------------------------------------------------------
def _read_color(cim):
    """Traduce cualquier color del CIM. El alfa del CIM va de 0 a 100."""
    if not isinstance(cim, dict):
        return None
    values = cim.get("values") or []
    kind = cim.get("type")

    if kind == "CIMRGBColor" and len(values) >= 3:
        alpha = values[3] if len(values) > 3 else 100
        return Color(values[0], values[1], values[2], _alpha(alpha))
    if kind == "CIMCMYKColor" and len(values) >= 4:
        cyan, magenta, yellow, black = (value / 100.0 for value in values[:4])
        alpha = values[4] if len(values) > 4 else 100
        return Color(
            255 * (1 - cyan) * (1 - black),
            255 * (1 - magenta) * (1 - black),
            255 * (1 - yellow) * (1 - black),
            _alpha(alpha),
        )
    if kind == "CIMGrayColor" and values:
        level = 255 * (1 - values[0] / 100.0)
        alpha = values[1] if len(values) > 1 else 100
        return Color(level, level, level, _alpha(alpha))
    if kind == "CIMHSVColor" and len(values) >= 3:
        red, green, blue = _hsv_to_rgb(values[0], values[1], values[2])
        alpha = values[3] if len(values) > 3 else 100
        return Color(red, green, blue, _alpha(alpha))
    return None


def _alpha(value):
    try:
        return int(round(float(value) * 255.0 / 100.0))
    except (TypeError, ValueError):
        return 255


def _hsv_to_rgb(hue, saturation, value):
    hue = float(hue) % 360.0
    saturation = max(0.0, min(1.0, float(saturation) / 100.0))
    value = max(0.0, min(1.0, float(value) / 100.0))

    chroma = value * saturation
    secondary = chroma * (1 - abs((hue / 60.0) % 2 - 1))
    match = value - chroma
    sector = int(hue // 60) % 6
    table = (
        (chroma, secondary, 0),
        (secondary, chroma, 0),
        (0, chroma, secondary),
        (0, secondary, chroma),
        (secondary, 0, chroma),
        (chroma, 0, secondary),
    )
    red, green, blue = table[sector]
    return (
        (red + match) * 255,
        (green + match) * 255,
        (blue + match) * 255,
    )


# ----------------------------------------------------------------------
# etiquetas
# ----------------------------------------------------------------------
def _read_label(definition, result):
    classes = definition.get("labelClasses") or []
    if not classes:
        return None
    if not definition.get("labelVisibility", True):
        return None

    entry = classes[0]
    if len(classes) > 1:
        result.add_warning(
            "La capa tiene %d clases de etiqueta; se traslado solo la primera."
            % len(classes)
        )

    expression = entry.get("expression") or ""
    field, is_expression = _label_field(expression, entry.get("expressionEngine"))

    text_symbol = (entry.get("textSymbol") or {}).get("symbol") or {}
    color = None
    for layer in text_symbol.get("symbolLayers") or []:
        if layer.get("type") == "CIMSolidFill":
            color = _read_color(layer.get("color"))
            break

    halo_size = points_to_mm(text_symbol.get("haloSize"), 0) or 0
    halo_color = None
    halo = text_symbol.get("haloSymbol") or {}
    for layer in halo.get("symbolLayers") or []:
        if layer.get("type") == "CIMSolidFill":
            halo_color = _read_color(layer.get("color"))
            break

    return Label(
        field=None if is_expression else field,
        expression=field if is_expression else None,
        font_family=text_symbol.get("fontFamilyName") or "Arial",
        size=points_to_mm(text_symbol.get("height"), 3.0) / 0.352778,
        color=color or Color(0, 0, 0),
        bold="bold" in (text_symbol.get("fontStyleName") or "").lower(),
        italic="italic" in (text_symbol.get("fontStyleName") or "").lower(),
        buffer_size=halo_size or 1.0,
        buffer_color=halo_color or Color(255, 255, 255, 230),
        min_scale=_scale(entry.get("minimumScale")),
        max_scale=_scale(entry.get("maximumScale")),
        enabled=entry.get("visibility", True),
    )


def _label_field(expression, engine):
    """Del texto de la expresion de ArcGIS al campo o expresion del destino.

    Lo habitual con diferencia es una expresion de un solo campo
    (``$feature.CODIGOESTRUCTURA`` en Arcade, ``[CODIGO]`` en VBScript). Ese
    caso se traduce a un campo simple; cualquier cosa mas compleja se deja
    anotada como expresion para que la revise una persona.
    """
    text = (expression or "").strip()
    if not text:
        return "", False

    if text.startswith("$feature."):
        candidate = text[len("$feature.") :]
        if candidate.replace("_", "").isalnum():
            return candidate, False

    if text.startswith("[") and text.endswith("]") and text.count("[") == 1:
        candidate = text[1:-1]
        if candidate.replace("_", "").isalnum():
            return candidate, False

    if text.replace("_", "").isalnum():
        return text, False

    # No es un campo simple: se deja como expresion, que en el destino usa
    # comillas dobles para los campos.
    converted = text.replace("$feature.", "")
    _ = engine
    return converted, True
