# -*- coding: utf-8 -*-
"""Simbologia automatica para lo que nadie declaro.

Es la ultima red de seguridad: cuando una clase no tiene estilo declarado por
el usuario, ni archivo de capa de ArcGIS, ni entrada en el estilo del perfil,
hay que dibujarla igualmente. Antes que un color al azar, se aplica un criterio:

* la **forma** del marcador sale del papel de la clase en el modelo (un Puesto
  no se dibuja igual que un poste ni que un punto de carga);
* el **color** sale de una paleta estable, derivada del nombre de la clase, de
  modo que la misma clase salga siempre del mismo color aunque cambie el orden
  de empaquetado;
* la **etiqueta** sale del primer campo con sentido que exista en la clase
  (``TEXTOETIQUETA`` esta en 27 de las 47 clases del modelo);
* las clases **densas** reciben un limite de escala, porque un telefono no
  puede dibujar doscientas mil acometidas a escala de provincia.

Nada de esto pretende ser la simbologia oficial de nadie: es un punto de
partida legible. Para fijar la simbologia de la empresa estan el archivo de
estilo y los archivos de capa de ArcGIS.
"""

from .model import Label, LayerStyle, MarkerShape, Renderer, Symbol

SOURCE = "automatico"

#: Paleta de arranque: tonos distinguibles en una pantalla de telefono a pleno
#: sol, evitando los que se confunden con la ortofoto.
PALETTE = (
    "#e41a1c",
    "#377eb8",
    "#4daf4a",
    "#984ea3",
    "#ff7f00",
    "#a65628",
    "#f781bf",
    "#00968a",
    "#7f5539",
    "#3f6f8e",
)

#: Forma del marcador segun el papel de la clase en el modelo.
SHAPE_BY_KIND = {
    "puesto": MarkerShape.SQUARE,
    "unidad": MarkerShape.CIRCLE,
    "catalogo": MarkerShape.CIRCLE,
    "tramo": MarkerShape.CIRCLE,
}

#: Campos que sirven de etiqueta, por orden de preferencia.
LABEL_FIELDS = (
    "TEXTOETIQUETA",
    "CODIGOESTRUCTURA",
    "CODIGOPUESTO",
    "NUMEROPOSTE",
    "CODIGO",
    "NOMBRE",
    "ALIMENTADORID",
)

#: Por encima de tantas entidades, la capa se considera densa y se le pone
#: limite de escala para que el dispositivo no se ahogue.
DENSE_FEATURE_COUNT = 20000

#: Denominador por debajo del cual se dibuja una capa densa.
DENSE_MIN_SCALE = 10000

#: Denominador por debajo del cual se etiqueta. Etiquetar a escala de ciudad
#: llena la pantalla de texto ilegible.
LABEL_MIN_SCALE = 5000


def color_for(layer_name, index=None):
    """Color estable para una clase.

    Se deriva del nombre y no del orden de empaquetado: si manana se exporta un
    alimentador distinto, el poste sigue siendo del mismo color.
    """
    if index is None:
        # Mezcla posicional: la simple suma de codigos hace que nombres
        # distintos caigan demasiado a menudo en el mismo color.
        index = 0
        for position, char in enumerate(layer_name or ""):
            index = (index * 31 + ord(char) * (position + 1)) % 1000003
    return PALETTE[index % len(PALETTE)]


def build_style(
    layer_name,
    geometry_type,
    profile=None,
    subtype_field=None,
    subtype_categories=None,
    field_names=None,
    feature_count=None,
    color_index=None,
):
    """Estilo automatico de una clase."""
    color = color_for(layer_name, color_index)
    kind = profile.kind_of(layer_name) if profile is not None else None

    renderer = _build_renderer(
        layer_name, geometry_type, kind, color, subtype_field, subtype_categories
    )
    label = _build_label(field_names)

    min_scale = 0
    if feature_count and feature_count > DENSE_FEATURE_COUNT:
        min_scale = DENSE_MIN_SCALE

    return LayerStyle(renderer=renderer, label=label, min_scale=min_scale)


def _build_renderer(
    layer_name, geometry_type, kind, color, subtype_field, subtype_categories
):
    if subtype_field and subtype_categories:
        from .model import Category

        categories = []
        for index, (code, label) in enumerate(subtype_categories):
            categories.append(
                Category(
                    value=code,
                    label=label,
                    symbol=_symbol(
                        geometry_type, kind, color_for(layer_name, _shift(index))
                    ),
                )
            )
        return Renderer(
            Renderer.CATEGORIZED,
            field=subtype_field,
            categories=categories,
            source=SOURCE,
        )

    return Renderer(
        Renderer.SINGLE, symbol=_symbol(geometry_type, kind, color), source=SOURCE
    )


def _shift(index):
    """Reparte los subtipos por la paleta sin repetir el color de al lado."""
    return index * 3 + 1


def _symbol(geometry_type, kind, color):
    if geometry_type == "Line":
        # La flecha de sentido no se pone por defecto: en una capa con muchos
        # tramos ensucia la pantalla. Se activa desde el estilo.
        return Symbol.line(color, width=0.66)
    if geometry_type == "Polygon":
        return Symbol.fill(color + "50", outline_color=color)
    return Symbol.marker(
        color,
        shape=SHAPE_BY_KIND.get(kind, MarkerShape.CIRCLE),
        size=2.8 if kind == "puesto" else 2.2,
    )


def _build_label(field_names):
    if not field_names:
        return None
    available = dict((name.upper(), name) for name in field_names)
    for candidate in LABEL_FIELDS:
        if candidate in available:
            return Label(
                field=available[candidate],
                min_scale=LABEL_MIN_SCALE,
                # Sin etiquetas activas por defecto: se declaran en el estilo o
                # se activan por capa. Asi el proyecto abre limpio y el tecnico
                # las enciende cuando las necesita.
                enabled=False,
            )
    return None
