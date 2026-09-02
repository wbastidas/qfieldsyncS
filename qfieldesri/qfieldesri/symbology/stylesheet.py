# -*- coding: utf-8 -*-
"""Archivo de estilo: como quiere el usuario que se vea cada clase.

Es la vía directa para decir "esto va así" sin pasar por ArcGIS. Un JSON con
claves en español, pensado para editarse a mano en cualquier editor de texto,
que se guarda junto al resto de la configuración y se versiona con ella.

Se puede partir de cero o, más cómodo, **exportar el estilo que qfieldESRI
usaría** (con lo que haya deducido de la geodatabase o importado de un
``.lyrx``), abrirlo y retocar los colores. Eso es lo que hace
``qfieldesri estilo --exportar``.

Forma del archivo
-----------------
::

    {
      "version": 1,
      "capas": {
        "TramoDistribucionAereo": {
          "escala_minima": 25000,
          "simbologia": {
            "tipo": "categorizado",
            "campo": "SUBTIPO",
            "categorias": [
              {"valor": 1, "etiqueta": "MT trifasico",
               "simbolo": {"color": "#e60000", "ancho": 0.8, "flecha": true}},
              {"valor": 2, "etiqueta": "MT monofasico",
               "simbolo": {"color": "#e60000", "ancho": 0.5, "estilo": "dash"}}
            ]
          },
          "etiqueta": {"campo": "CODIGOESTRUCTURA", "escala_minima": 5000}
        }
      }
    }

Todo es opcional: lo que no se declare se resuelve con el resto de fuentes.
"""

import io
import json
import os

from . import defaults
from .model import (
    Category,
    FillStyle,
    Label,
    LayerStyle,
    LineStyle,
    MarkerShape,
    Range,
    Renderer,
    Rule,
    Symbol,
    SymbolLayer,
)

SOURCE = "estilo"
VERSION = 1


class StyleSheetError(Exception):
    pass


class StyleSheet(object):
    """Los estilos declarados por el usuario."""

    def __init__(self, data=None, path=None):
        data = data or {}
        self.path = path
        self.version = data.get("version", VERSION)
        self.description = data.get("descripcion", "")
        #: ``{nombre_clase: definicion}``
        self.layers = data.get("capas") or {}
        #: se aplica a las clases que no tengan entrada propia
        self.default = data.get("por_defecto") or {}
        self._lower = dict((name.lower(), name) for name in self.layers)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path):
        if not os.path.isfile(path):
            raise StyleSheetError("No se encuentra el archivo de estilo: %s" % path)
        with io.open(path, "r", encoding="utf-8-sig") as handle:
            try:
                data = json.load(handle)
            except ValueError as error:
                raise StyleSheetError(
                    "El archivo de estilo no es un JSON valido: %s" % error
                )
        if not isinstance(data, dict):
            raise StyleSheetError(
                "El archivo de estilo debe ser un objeto con la clave 'capas'."
            )
        return cls(data, path)

    def save(self, path):
        payload = {
            "version": self.version,
            "descripcion": self.description
            or "Estilos de qfieldESRI. Edite colores, tamanos y etiquetas a mano.",
            "capas": self.layers,
        }
        if self.default:
            payload["por_defecto"] = self.default
        with io.open(path, "w", encoding="utf-8") as handle:
            text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write(_unicode(text))
        self.path = path
        return path

    # ------------------------------------------------------------------
    def knows(self, layer_name):
        return (layer_name or "").lower() in self._lower

    def definition_for(self, layer_name):
        key = self._lower.get((layer_name or "").lower())
        if key is None:
            return dict(self.default) if self.default else None
        definition = dict(self.default)
        definition.update(self.layers[key])
        return definition

    def style_for(
        self,
        layer_name,
        geometry_type,
        subtype_field=None,
        subtype_categories=None,
    ):
        """Devuelve el :class:`LayerStyle` declarado, o ``None``."""
        definition = self.definition_for(layer_name)
        if not definition:
            return None
        return build_layer_style(
            definition,
            geometry_type,
            layer_name,
            subtype_field=subtype_field,
            subtype_categories=subtype_categories,
        )

    def set_style(self, layer_name, style, geometry_type):
        """Guarda un estilo ya resuelto, para poder exportarlo como plantilla."""
        self.layers[layer_name] = describe_layer_style(style, geometry_type)
        self._lower[layer_name.lower()] = layer_name

    def __len__(self):
        return len(self.layers)

    def __repr__(self):  # pragma: no cover
        return "<StyleSheet %d capas>" % len(self.layers)


# ----------------------------------------------------------------------
# JSON -> modelo
# ----------------------------------------------------------------------
def build_layer_style(
    definition,
    geometry_type,
    layer_name="",
    subtype_field=None,
    subtype_categories=None,
):
    renderer = _build_renderer(
        definition.get("simbologia"),
        geometry_type,
        layer_name,
        subtype_field,
        subtype_categories,
    )
    label = _build_label(definition.get("etiqueta"))
    return LayerStyle(
        renderer=renderer,
        label=label,
        min_scale=int(definition.get("escala_minima", 0) or 0),
        max_scale=int(definition.get("escala_maxima", 0) or 0),
        opacity=float(definition.get("opacidad", 1.0)),
        visible=bool(definition.get("visible", True)),
    )


#: ``subtipos`` es propio de qfieldESRI: categoriza por el campo de subtipo de
#: la clase usando los subtipos que declare la geodatabase, sin fijar codigos en
#: el archivo. Es lo que permite que el mismo estilo sirva para cualquier
#: Unidad de Negocio.
SUBTYPES = "subtipos"

_RENDERER_KINDS = {
    "simple": Renderer.SINGLE,
    "unico": Renderer.SINGLE,
    "categorizado": Renderer.CATEGORIZED,
    "graduado": Renderer.GRADUATED,
    "reglas": Renderer.RULE_BASED,
    "ninguno": Renderer.NULL,
    SUBTYPES: SUBTYPES,
}


def _build_renderer(
    definition, geometry_type, layer_name, subtype_field=None, subtype_categories=None
):
    if not definition:
        return None
    if isinstance(definition, dict) and not set(definition) & {"tipo", "simbolo"}:
        # Atajo: declarar solo las propiedades del simbolo equivale a un
        # renderizador simple.
        definition = {"tipo": "simple", "simbolo": definition}

    kind_name = str(definition.get("tipo", "simple")).lower()
    kind = _RENDERER_KINDS.get(kind_name)
    if kind is None:
        raise StyleSheetError(
            "Tipo de simbologia desconocido en '%s': '%s'. Use uno de: %s."
            % (layer_name, kind_name, ", ".join(sorted(_RENDERER_KINDS)))
        )

    if kind == SUBTYPES:
        return _build_subtype_renderer(
            definition, geometry_type, layer_name, subtype_field, subtype_categories
        )

    if kind == Renderer.NULL:
        return Renderer(Renderer.NULL, source=SOURCE)

    if kind == Renderer.SINGLE:
        return Renderer(
            Renderer.SINGLE,
            symbol=build_symbol(definition.get("simbolo"), geometry_type),
            source=SOURCE,
        )

    if kind == Renderer.RULE_BASED:
        rules = []
        for entry in definition.get("reglas") or []:
            rules.append(
                Rule(
                    expression=entry.get("expresion") or "",
                    label=entry.get("etiqueta") or "",
                    symbol=build_symbol(entry.get("simbolo"), geometry_type),
                    min_scale=int(entry.get("escala_minima", 0) or 0),
                    max_scale=int(entry.get("escala_maxima", 0) or 0),
                )
            )
        return Renderer(Renderer.RULE_BASED, rules=rules, source=SOURCE)

    # Lo que queda (categorizado y graduado) si clasifica por un campo.
    field = definition.get("campo")
    if not field:
        raise StyleSheetError(
            "La simbologia '%s' de '%s' necesita la clave 'campo'."
            % (kind_name, layer_name)
        )

    if kind == Renderer.GRADUATED:
        ranges = []
        for entry in definition.get("intervalos") or []:
            ranges.append(
                Range(
                    lower=float(entry.get("desde", 0) or 0),
                    upper=float(entry.get("hasta", 0) or 0),
                    label=entry.get("etiqueta")
                    or "%s - %s" % (entry.get("desde"), entry.get("hasta")),
                    symbol=build_symbol(entry.get("simbolo"), geometry_type),
                )
            )
        return Renderer(
            Renderer.GRADUATED, field=field, ranges=ranges, source=SOURCE
        )

    categories = []
    for entry in definition.get("categorias") or []:
        categories.append(
            Category(
                value=entry.get("valor"),
                label=entry.get("etiqueta") or _text(entry.get("valor")),
                symbol=build_symbol(entry.get("simbolo"), geometry_type),
                render=entry.get("visible", True),
            )
        )
    return Renderer(
        Renderer.CATEGORIZED, field=field, categories=categories, source=SOURCE
    )


def _build_subtype_renderer(
    definition, geometry_type, layer_name, subtype_field, subtype_categories
):
    """Categoriza por los subtipos que traiga la geodatabase.

    El archivo de estilo declara los colores y la forma; los codigos y los
    nombres de los subtipos se leen de la geodatabase, que es donde son
    autoritativos y donde pueden cambiar de una Unidad de Negocio a otra.
    """
    base = dict(definition.get("simbolo") or {})
    colors = definition.get("colores") or []

    if not subtype_field or not subtype_categories:
        # La clase no tiene subtipos: se degrada a un simbolo unico en vez de
        # dejar la capa sin dibujar. El color base es el primero de la lista,
        # que es el que el usuario penso para el caso principal.
        if colors and "color" not in base:
            base["color"] = colors[0]
        return Renderer(
            Renderer.SINGLE,
            symbol=build_symbol(base, geometry_type),
            source=SOURCE,
        )
    overrides = definition.get("por_subtipo") or {}

    categories = []
    for index, (code, name) in enumerate(subtype_categories):
        symbol_definition = dict(base)
        if colors:
            symbol_definition["color"] = colors[index % len(colors)]
        elif "color" not in base:
            # Se pidio clasificar por subtipo pero no se declaro ningun color:
            # dibujarlos todos iguales seria lo mismo que no clasificar, asi
            # que se reparten los de la paleta automatica.
            symbol_definition["color"] = defaults.color_for(
                layer_name, index * 3 + 1
            )
        symbol_definition.update(overrides.get(_text(code), {}))
        categories.append(
            Category(
                value=code,
                label=name,
                symbol=build_symbol(symbol_definition, geometry_type),
            )
        )
    return Renderer(
        Renderer.CATEGORIZED,
        field=subtype_field,
        categories=categories,
        source=SOURCE,
    )


def build_symbol(definition, geometry_type):
    """Traduce la declaración de un símbolo según la geometría de la capa."""
    definition = definition or {}
    if geometry_type == "Line":
        symbol = Symbol.line(
            color=definition.get("color", "#c8322d"),
            width=float(definition.get("ancho", 0.66)),
            style=_line_style(definition.get("estilo")),
            custom_dash=definition.get("guiones"),
        )
        if definition.get("flecha"):
            symbol = symbol.with_flow_arrow(
                color=definition.get("flecha_color"),
                size=float(definition.get("flecha_tamano", 2.2)),
                interval=float(definition.get("flecha_intervalo", 14.0)),
            )
        return symbol

    if geometry_type == "Polygon":
        return Symbol.fill(
            color=definition.get("color", "#c8322d64"),
            outline_color=definition.get("borde_color"),
            outline_width=float(definition.get("borde_ancho", 0.4)),
            style=_fill_style(definition.get("estilo")),
        )

    return Symbol.marker(
        color=definition.get("color", "#c8322d"),
        shape=_marker_shape(definition.get("forma")),
        size=float(definition.get("tamano", 2.6)),
        outline_color=definition.get("borde_color", "#232323"),
        outline_width=float(definition.get("borde_ancho", 0.2)),
        angle=float(definition.get("angulo", 0)),
    )


def _line_style(name):
    if not name:
        return LineStyle.SOLID
    name = str(name).strip().lower().replace("_", " ")
    aliases = {
        "solida": LineStyle.SOLID,
        "continua": LineStyle.SOLID,
        "guiones": LineStyle.DASH,
        "discontinua": LineStyle.DASH,
        "puntos": LineStyle.DOT,
        "punteada": LineStyle.DOT,
        "ninguna": LineStyle.NONE,
    }
    if name in aliases:
        return aliases[name]
    if name in LineStyle.ALL:
        return name
    raise StyleSheetError(
        "Estilo de linea desconocido: '%s'. Use uno de: %s."
        % (name, ", ".join(LineStyle.ALL))
    )


def _fill_style(name):
    if not name:
        return FillStyle.SOLID
    name = str(name).strip().lower()
    aliases = {"solido": FillStyle.SOLID, "ninguno": FillStyle.NONE}
    if name in aliases:
        return aliases[name]
    if name in FillStyle.ALL:
        return name
    raise StyleSheetError(
        "Estilo de relleno desconocido: '%s'. Use uno de: %s."
        % (name, ", ".join(FillStyle.ALL))
    )


def _marker_shape(name):
    if not name:
        return MarkerShape.CIRCLE
    name = str(name).strip().lower()
    aliases = {
        "circulo": MarkerShape.CIRCLE,
        "cuadrado": MarkerShape.SQUARE,
        "rombo": MarkerShape.DIAMOND,
        "triangulo": MarkerShape.TRIANGLE,
        "estrella": MarkerShape.STAR,
        "cruz": MarkerShape.CROSS,
        "equis": MarkerShape.CROSS2,
        "pentagono": MarkerShape.PENTAGON,
        "hexagono": MarkerShape.HEXAGON,
        "flecha": MarkerShape.ARROW,
    }
    if name in aliases:
        return aliases[name]
    if name in MarkerShape.ALL:
        return name
    raise StyleSheetError(
        "Forma de marcador desconocida: '%s'. Use una de: %s."
        % (name, ", ".join(sorted(set(MarkerShape.ALL))))
    )


def _build_label(definition):
    if not definition:
        return None
    if definition is True:
        return Label()
    return Label(
        field=definition.get("campo"),
        expression=definition.get("expresion"),
        font_family=definition.get("fuente", "Arial"),
        size=float(definition.get("tamano", 8.5)),
        color=definition.get("color", "#000000"),
        bold=bool(definition.get("negrita", False)),
        italic=bool(definition.get("cursiva", False)),
        buffer_size=float(definition.get("halo", 1.0)),
        buffer_color=definition.get("halo_color", "255,255,255,230"),
        offset=float(definition.get("separacion", 1.5)),
        min_scale=int(definition.get("escala_minima", 0) or 0),
        max_scale=int(definition.get("escala_maxima", 0) or 0),
        enabled=bool(definition.get("visible", True)),
    )


# ----------------------------------------------------------------------
# modelo -> JSON (para exportar la plantilla)
# ----------------------------------------------------------------------
def describe_layer_style(style, geometry_type):
    """Vuelca un estilo resuelto a la forma del archivo, para editarlo."""
    definition = {}
    if style.min_scale:
        definition["escala_minima"] = style.min_scale
    if style.max_scale:
        definition["escala_maxima"] = style.max_scale
    if style.opacity != 1.0:
        definition["opacidad"] = round(style.opacity, 3)
    if style.renderer is not None:
        definition["simbologia"] = _describe_renderer(style.renderer, geometry_type)
    if style.label is not None:
        definition["etiqueta"] = _describe_label(style.label)
    return definition


_RENDERER_NAMES = dict((value, key) for key, value in _RENDERER_KINDS.items())


def _describe_renderer(renderer, geometry_type):
    definition = {"tipo": _RENDERER_NAMES.get(renderer.kind, renderer.kind)}
    if renderer.kind == Renderer.SINGLE:
        definition["simbolo"] = describe_symbol(renderer.symbol, geometry_type)
    elif renderer.kind == Renderer.CATEGORIZED:
        definition["campo"] = renderer.field
        definition["categorias"] = [
            {
                "valor": category.value,
                "etiqueta": category.label,
                "simbolo": describe_symbol(category.symbol, geometry_type),
            }
            for category in renderer.categories
        ]
    elif renderer.kind == Renderer.GRADUATED:
        definition["campo"] = renderer.field
        definition["intervalos"] = [
            {
                "desde": item.lower,
                "hasta": item.upper,
                "etiqueta": item.label,
                "simbolo": describe_symbol(item.symbol, geometry_type),
            }
            for item in renderer.ranges
        ]
    elif renderer.kind == Renderer.RULE_BASED:
        definition["reglas"] = [
            {
                "expresion": rule.expression,
                "etiqueta": rule.label,
                "simbolo": describe_symbol(rule.symbol, geometry_type),
            }
            for rule in renderer.rules
        ]
    return definition


def describe_symbol(symbol, geometry_type):
    if symbol is None:
        return {}
    definition = {}
    for layer in symbol.layers:
        if layer.kind == SymbolLayer.MARKER and geometry_type != "Line":
            definition["forma"] = layer.get("shape", MarkerShape.CIRCLE)
            definition["color"] = _hex(layer.get("color"))
            definition["tamano"] = layer.get("size", 2.6)
            definition["borde_color"] = _hex(layer.get("outline_color"))
            definition["borde_ancho"] = layer.get("outline_width", 0.2)
        elif layer.kind == SymbolLayer.LINE:
            definition["color"] = _hex(layer.get("color"))
            definition["ancho"] = layer.get("width", 0.66)
            style = layer.get("style", LineStyle.SOLID)
            if style != LineStyle.SOLID:
                definition["estilo"] = style
            if layer.get("custom_dash"):
                definition["guiones"] = layer.get("custom_dash")
        elif layer.kind == SymbolLayer.FILL:
            definition["color"] = _hex(layer.get("color"))
            definition["borde_color"] = _hex(layer.get("outline_color"))
            definition["borde_ancho"] = layer.get("outline_width", 0.4)
            style = layer.get("style", FillStyle.SOLID)
            if style != FillStyle.SOLID:
                definition["estilo"] = style
        elif layer.kind == SymbolLayer.MARKER_LINE:
            definition["flecha"] = True
            definition["flecha_intervalo"] = layer.get("interval", 14.0)
    return definition


def _describe_label(label):
    definition = {"campo": label.field} if label.field else {}
    if label.expression:
        definition["expresion"] = label.expression
    if label.font_family != "Arial":
        definition["fuente"] = label.font_family
    definition["tamano"] = round(label.size, 2)
    definition["color"] = _hex(label.color)
    if label.bold:
        definition["negrita"] = True
    if label.buffer_size:
        definition["halo"] = label.buffer_size
    if label.min_scale:
        definition["escala_minima"] = label.min_scale
    if not label.enabled:
        definition["visible"] = False
    return definition


def _hex(color):
    if color is None:
        return None
    if color.alpha >= 255:
        return color.to_hex()
    return "%s%02x" % (color.to_hex(), color.alpha)


def _text(value):
    return "" if value is None else _unicode(value)


def _unicode(value):
    try:
        return unicode(value)
    except NameError:
        return str(value)
