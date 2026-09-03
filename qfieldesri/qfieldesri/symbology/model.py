# -*- coding: utf-8 -*-
"""Modelo neutro de simbologia, etiquetado y visibilidad.

Es el punto de encuentro entre tres origenes que no se parecen en nada —el CIM
de un ``.lyrx`` de ArcGIS Pro, lo poco que expone ``arcpy.mapping`` de un
``.lyr`` o un MXD, y el archivo de estilo que el usuario escribe a mano— y un
unico destino, el proyecto que abre QField.

Ninguna de las tres fuentes habla el idioma del destino, y el destino tiene
limitaciones propias (no todo simbolo de ArcGIS existe en QField). Este modulo
define el vocabulario intermedio para que cada importador solo tenga que
traducir a el, y el escritor solo tenga que traducir desde el.

Unidades
--------
Todo se guarda en **milimetros**, que es la unidad natural del proyecto de
QField. ArcGIS trabaja en **puntos**; los importadores convierten al entrar
(:data:`POINTS_TO_MM`). Los colores se guardan con alfa 0-255; el CIM usa
0-100 y tambien se convierte al entrar.
"""

#: Un punto tipografico en milimetros (1 pt = 1/72 pulgada).
POINTS_TO_MM = 25.4 / 72.0


def points_to_mm(value, default=None):
    """Convierte puntos a milimetros, tolerando valores ausentes."""
    if value is None:
        return default
    try:
        return round(float(value) * POINTS_TO_MM, 4)
    except (TypeError, ValueError):
        return default


class Color(object):
    """Un color RGBA, con alfa 0-255."""

    __slots__ = ("alpha", "blue", "green", "red")

    def __init__(self, red=0, green=0, blue=0, alpha=255):
        self.red = _clamp(red)
        self.green = _clamp(green)
        self.blue = _clamp(blue)
        self.alpha = _clamp(alpha)

    # -- constructores --------------------------------------------------
    @classmethod
    def from_hex(cls, text, alpha=255):
        """``#RGB``, ``#RRGGBB`` o ``#RRGGBBAA``."""
        text = str(text).strip().lstrip("#")
        if len(text) == 3:
            # Forma corta, comoda de escribir a mano: #f00 -> #ff0000.
            text = "".join(char * 2 for char in text)
        if len(text) not in (6, 8):
            raise ValueError(
                "Color hexadecimal invalido: '%s'. Use #RGB, #RRGGBB o "
                "#RRGGBBAA." % text
            )
        values = [int(text[index : index + 2], 16) for index in range(0, len(text), 2)]
        if len(values) == 3:
            values.append(alpha)
        return cls(*values)

    @classmethod
    def parse(cls, value, default=None):
        """Acepta ``#RRGGBB``, ``"r,g,b"``, ``"r,g,b,a"`` o una lista."""
        if value is None:
            return default
        if isinstance(value, Color):
            return value
        if isinstance(value, (list, tuple)):
            return cls(*value)
        text = str(value).strip()
        if text.startswith("#"):
            return cls.from_hex(text)
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) in (3, 4):
            return cls(*[int(float(part)) for part in parts])
        raise ValueError("Color invalido: %s" % value)

    # -- salida ---------------------------------------------------------
    def to_qgis(self):
        """Como lo espera el proyecto: ``"r,g,b,a"``."""
        return "%d,%d,%d,%d" % (self.red, self.green, self.blue, self.alpha)

    def to_hex(self):
        return "#%02x%02x%02x" % (self.red, self.green, self.blue)

    def with_alpha(self, alpha):
        return Color(self.red, self.green, self.blue, alpha)

    def __eq__(self, other):
        return isinstance(other, Color) and self.to_qgis() == other.to_qgis()

    def __ne__(self, other):  # pragma: no cover - Python 2.7 lo necesita
        return not self == other

    def __repr__(self):  # pragma: no cover
        return "<Color %s>" % self.to_qgis()

    def __hash__(self):
        # Definido a mano porque en Python 2.7 declarar ``__eq__`` no quita el
        # ``__hash__`` heredado, y dos colores iguales tienen que caer en el
        # mismo sitio de un diccionario en las dos versiones.
        return hash((self.red, self.green, self.blue, self.alpha))


def _clamp(value):
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(255, value))


# ----------------------------------------------------------------------
# simbolos
# ----------------------------------------------------------------------
class MarkerShape(object):
    """Formas de marcador que entiende el destino.

    Se listan de forma explicita porque el importador tiene que mapear las
    formas de ArcGIS a estas y no a un nombre cualquiera: un nombre que el
    destino no reconozca se dibuja como un cuadrado por defecto y el tecnico
    no distingue un transformador de un seccionador.
    """

    CIRCLE = "circle"
    SQUARE = "square"
    DIAMOND = "diamond"
    TRIANGLE = "triangle"
    EQUILATERAL_TRIANGLE = "equilateral_triangle"
    PENTAGON = "pentagon"
    HEXAGON = "hexagon"
    STAR = "star"
    CROSS = "cross"
    CROSS2 = "cross2"
    CROSS_FILL = "cross_fill"
    ARROW = "arrow"
    ARROWHEAD = "arrowhead"
    FILLED_ARROWHEAD = "filled_arrowhead"
    LINE = "line"
    HALF_SQUARE = "half_square"
    CIRCLE_WITH_CROSS = "circle"  # se compone con una segunda capa

    ALL = (
        CIRCLE,
        SQUARE,
        DIAMOND,
        TRIANGLE,
        EQUILATERAL_TRIANGLE,
        PENTAGON,
        HEXAGON,
        STAR,
        CROSS,
        CROSS2,
        CROSS_FILL,
        ARROW,
        ARROWHEAD,
        FILLED_ARROWHEAD,
        LINE,
        HALF_SQUARE,
    )


class LineStyle(object):
    SOLID = "solid"
    DASH = "dash"
    DOT = "dot"
    DASH_DOT = "dash dot"
    DASH_DOT_DOT = "dash dot dot"
    NONE = "no"

    ALL = (SOLID, DASH, DOT, DASH_DOT, DASH_DOT_DOT, NONE)


class FillStyle(object):
    SOLID = "solid"
    NONE = "no"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    CROSS = "cross"
    B_DIAGONAL = "b_diagonal"
    F_DIAGONAL = "f_diagonal"
    DIAGONAL_X = "diagonal_x"
    DENSE_4 = "dense4"

    ALL = (
        SOLID,
        NONE,
        HORIZONTAL,
        VERTICAL,
        CROSS,
        B_DIAGONAL,
        F_DIAGONAL,
        DIAGONAL_X,
        DENSE_4,
    )


class SymbolLayer(object):
    """Una capa de un simbolo. Los simbolos se componen apilandolas."""

    MARKER = "marker"
    LINE = "line"
    FILL = "fill"
    #: marcadores repetidos a lo largo de una linea (flechas de sentido)
    MARKER_LINE = "marker_line"

    def __init__(self, kind, **properties):
        self.kind = kind
        self.properties = properties

    def get(self, name, default=None):
        value = self.properties.get(name, default)
        return default if value is None else value

    def __repr__(self):  # pragma: no cover
        return "<SymbolLayer %s %s>" % (self.kind, sorted(self.properties))


class Symbol(object):
    """Un simbolo completo: una o varias capas apiladas."""

    MARKER = "marker"
    LINE = "line"
    FILL = "fill"

    def __init__(self, symbol_type, layers=None, opacity=1.0):
        self.symbol_type = symbol_type
        self.layers = list(layers or [])
        self.opacity = opacity

    # -- fabricas comodas ------------------------------------------------
    @classmethod
    def marker(
        cls,
        color,
        shape=MarkerShape.CIRCLE,
        size=2.6,
        outline_color="35,35,35,255",
        outline_width=0.2,
        angle=0,
    ):
        return cls(
            cls.MARKER,
            [
                SymbolLayer(
                    SymbolLayer.MARKER,
                    shape=shape,
                    color=Color.parse(color),
                    size=size,
                    outline_color=Color.parse(outline_color),
                    outline_width=outline_width,
                    angle=angle,
                )
            ],
        )

    @classmethod
    def line(cls, color, width=0.66, style=LineStyle.SOLID, custom_dash=None):
        return cls(
            cls.LINE,
            [
                SymbolLayer(
                    SymbolLayer.LINE,
                    color=Color.parse(color),
                    width=width,
                    style=style,
                    custom_dash=custom_dash,
                )
            ],
        )

    @classmethod
    def fill(cls, color, outline_color=None, outline_width=0.4, style=FillStyle.SOLID):
        color = Color.parse(color)
        return cls(
            cls.FILL,
            [
                SymbolLayer(
                    SymbolLayer.FILL,
                    color=color,
                    style=style,
                    outline_color=Color.parse(outline_color) or color.with_alpha(255),
                    outline_width=outline_width,
                )
            ],
        )

    def with_flow_arrow(self, color=None, size=2.2, interval=14.0):
        """Devuelve el simbolo con una flecha de sentido sobre la linea.

        En una red electrica el sentido importa: el manual exige digitalizar de
        la fuente hacia la carga, y en campo se necesita ver de un vistazo si un
        tramo esta al reves.
        """
        if self.symbol_type != self.LINE:
            return self
        base = None
        for layer in self.layers:
            if layer.kind == SymbolLayer.LINE:
                base = layer.get("color")
                break
        arrow = SymbolLayer(
            SymbolLayer.MARKER_LINE,
            interval=interval,
            rotate=True,
            placement="Interval",
            marker=Symbol.marker(
                Color.parse(color) or base or Color(80, 80, 80),
                shape=MarkerShape.FILLED_ARROWHEAD,
                size=size,
                outline_width=0,
                outline_color="0,0,0,0",
            ),
        )
        return Symbol(self.symbol_type, self.layers + [arrow], self.opacity)

    @property
    def primary_color(self):
        for layer in self.layers:
            color = layer.properties.get("color")
            if color is not None:
                return color
        return None

    def __repr__(self):  # pragma: no cover
        return "<Symbol %s (%d capas)>" % (self.symbol_type, len(self.layers))


# ----------------------------------------------------------------------
# renderizadores
# ----------------------------------------------------------------------
class Category(object):
    """Una clase del renderizador categorizado."""

    def __init__(self, value, label, symbol, render=True):
        self.value = value
        self.label = label
        self.symbol = symbol
        self.render = render


class Range(object):
    """Un intervalo del renderizador graduado."""

    def __init__(self, lower, upper, label, symbol, render=True):
        self.lower = lower
        self.upper = upper
        self.label = label
        self.symbol = symbol
        self.render = render


class Rule(object):
    """Una regla del renderizador por reglas."""

    def __init__(self, expression, label, symbol, min_scale=0, max_scale=0):
        self.expression = expression
        self.label = label
        self.symbol = symbol
        #: denominador maximo (mas alejado) en el que la regla dibuja
        self.min_scale = min_scale
        self.max_scale = max_scale


class Renderer(object):
    """Como se pinta una capa."""

    SINGLE = "single"
    CATEGORIZED = "categorized"
    GRADUATED = "graduated"
    RULE_BASED = "rule_based"
    NULL = "null"

    def __init__(
        self,
        kind=SINGLE,
        symbol=None,
        field=None,
        categories=None,
        ranges=None,
        rules=None,
        source=None,
    ):
        self.kind = kind
        self.symbol = symbol
        #: campo (o expresion) que clasifica, en los tipos que lo usan
        self.field = field
        self.categories = list(categories or [])
        self.ranges = list(ranges or [])
        self.rules = list(rules or [])
        #: de donde salio: ``lyrx``, ``arcpy``, ``estilo``, ``subtipos``,
        #: ``automatico``. Se informa al usuario para que sepa que esta viendo.
        self.source = source

    @property
    def symbols(self):
        """Todos los simbolos, en el orden en que se escriben."""
        if self.kind == self.SINGLE:
            return [self.symbol] if self.symbol else []
        if self.kind == self.CATEGORIZED:
            return [category.symbol for category in self.categories]
        if self.kind == self.GRADUATED:
            return [item.symbol for item in self.ranges]
        if self.kind == self.RULE_BASED:
            return [rule.symbol for rule in self.rules]
        return []

    def __repr__(self):  # pragma: no cover
        return "<Renderer %s (%s) %d simbolos>" % (
            self.kind,
            self.source or "?",
            len(self.symbols),
        )


# ----------------------------------------------------------------------
# etiquetado
# ----------------------------------------------------------------------
class LabelPlacement(object):
    """Colocacion de la etiqueta (valores del proyecto de QField)."""

    AROUND_POINT = 0
    OVER_POINT = 1
    LINE = 2
    CURVED = 3
    HORIZONTAL = 4
    FREE = 5


class Label(object):
    """Etiquetado de una capa."""

    def __init__(
        self,
        field=None,
        expression=None,
        font_family="Arial",
        size=8.5,
        color="0,0,0,255",
        bold=False,
        italic=False,
        buffer_size=1.0,
        buffer_color="255,255,255,230",
        placement=None,
        offset=1.5,
        min_scale=0,
        max_scale=0,
        enabled=True,
    ):
        #: campo a mostrar; si se da ``expression`` manda la expresion
        self.field = field
        self.expression = expression
        self.font_family = font_family
        self.size = size
        self.color = Color.parse(color)
        self.bold = bold
        self.italic = italic
        #: halo blanco: sin el, una etiqueta sobre la ortofoto es ilegible
        self.buffer_size = buffer_size
        self.buffer_color = Color.parse(buffer_color)
        self.placement = placement
        self.offset = offset
        #: solo etiquetar por debajo de este denominador (0 = siempre)
        self.min_scale = min_scale
        self.max_scale = max_scale
        self.enabled = enabled

    @property
    def is_expression(self):
        return bool(self.expression)

    @property
    def text(self):
        return self.expression or self.field or ""

    def placement_for(self, geometry_type):
        if self.placement is not None:
            return self.placement
        if geometry_type == "Line":
            return LabelPlacement.CURVED
        if geometry_type == "Polygon":
            return LabelPlacement.FREE
        return LabelPlacement.AROUND_POINT

    def __repr__(self):  # pragma: no cover
        return "<Label %s>" % self.text


# ----------------------------------------------------------------------
class LayerStyle(object):
    """Todo lo visual de una capa: como se pinta, se etiqueta y cuando se ve."""

    def __init__(
        self,
        renderer=None,
        label=None,
        min_scale=0,
        max_scale=0,
        opacity=1.0,
        visible=True,
    ):
        self.renderer = renderer
        self.label = label
        #: denominador maximo con la capa visible (0 = sin limite). Poner un
        #: limite es lo que evita que un telefono intente dibujar 200 000
        #: acometidas a escala de provincia.
        self.min_scale = min_scale
        self.max_scale = max_scale
        self.opacity = opacity
        self.visible = visible

    @property
    def has_scale_limits(self):
        return bool(self.min_scale or self.max_scale)

    @property
    def source(self):
        return self.renderer.source if self.renderer else None

    def __repr__(self):  # pragma: no cover
        return "<LayerStyle %s%s>" % (
            self.renderer,
            " + etiquetas" if self.label else "",
        )
