# -*- coding: utf-8 -*-
"""Simbologia: de donde sale y como se decide.

ArcGIS guarda la simbologia fuera de la geodatabase —en el MXD, en un ``.lyr``
o en un ``.lyrx``—, asi que no basta con leer la geodatabase para saber como se
quiere ver un tramo de media tension. Este paquete reune las fuentes posibles y
las ordena.

Orden de precedencia, de mas explicito a mas automatico:

1. **Archivo de estilo del usuario** (``--estilo``). Manda siempre: es la forma
   de decir "esto va asi" sin depender de nada.
2. **Simbologia importada de ArcGIS**: una carpeta de ``.lyrx`` (fidelidad
   completa, incluidos los colores), o un ``.lyr``/MXD leido con arcpy (solo la
   clasificacion; los colores no los expone ArcGIS).
3. **Estilo del perfil**: el arranque razonable del modelo electrico, que se
   distribuye como un archivo de estilo mas (``cnel_ep.estilo.json``) para que
   se pueda copiar y editar.
4. **Automatico**: forma segun el papel de la clase, color estable derivado del
   nombre, etiqueta del primer campo con sentido y limite de escala si la clase
   es densa.

Cada capa registra de donde salio su estilo, y el empaquetado lo informa: quien
recibe el paquete tiene que poder saber si esta viendo la simbologia de la
oficina o un color inventado.
"""

import os

from ..profiles import STYLE_SUFFIX
from . import defaults
from .model import (
    Category,
    Color,
    FillStyle,
    Label,
    LabelPlacement,
    LayerStyle,
    LineStyle,
    MarkerShape,
    Range,
    Renderer,
    Rule,
    Symbol,
    SymbolLayer,
)
from .stylesheet import StyleSheet, StyleSheetError, describe_layer_style

#: Nombre del archivo de estilo que acompana a un perfil. Lo define el paquete
#: de perfiles, que es quien tiene que distinguirlo de un perfil real al listar
#: los disponibles.
PROFILE_STYLE_SUFFIX = STYLE_SUFFIX

#: Formas de pedir "la simbologia del mapa que tengo abierto".
ACTIVE_DOCUMENT_ALIASES = ("current", "activo", "mapa_activo", "mapa-activo")


class SymbologyResolver(object):
    """Decide el estilo de cada capa y deja constancia de su origen."""

    def __init__(self, profile=None, stylesheet=None, imported=None):
        self.profile = profile
        #: estilo escrito por el usuario (manda sobre todo)
        self.stylesheet = stylesheet
        #: ``{nombre_clase: LayerStyle}`` importado de ArcGIS
        self.imported = dict(imported or {})
        self.profile_stylesheet = _load_profile_stylesheet(profile)
        self.warnings = []
        #: ``{nombre_clase: origen}``, para el informe final
        self.sources = {}

    # ------------------------------------------------------------------
    def style_for(
        self,
        layer_name,
        geometry_type,
        subtype_field=None,
        subtype_categories=None,
        field_names=None,
        feature_count=None,
        color_index=None,
    ):
        """Estilo aplicable a una clase, segun el orden de precedencia."""
        style = self._from_stylesheet(
            self.stylesheet,
            layer_name,
            geometry_type,
            subtype_field,
            subtype_categories,
        )
        if style is None:
            style = self._from_import(layer_name)
        if style is None:
            style = self._from_stylesheet(
                self.profile_stylesheet,
                layer_name,
                geometry_type,
                subtype_field,
                subtype_categories,
            )
        if style is None:
            style = defaults.build_style(
                layer_name,
                geometry_type,
                profile=self.profile,
                subtype_field=subtype_field,
                subtype_categories=subtype_categories,
                field_names=field_names,
                feature_count=feature_count,
                color_index=color_index,
            )

        self._validate(style, layer_name, field_names)
        self.sources[layer_name] = style.source or defaults.SOURCE
        return style

    def _from_stylesheet(
        self,
        stylesheet,
        layer_name,
        geometry_type,
        subtype_field=None,
        subtype_categories=None,
    ):
        if stylesheet is None or not stylesheet.knows(layer_name):
            return None
        try:
            return stylesheet.style_for(
                layer_name,
                geometry_type,
                subtype_field=subtype_field,
                subtype_categories=subtype_categories,
            )
        except StyleSheetError as error:
            self._warn("%s: %s" % (layer_name, error))
            return None

    def _from_import(self, layer_name):
        if not self.imported:
            return None
        style = self.imported.get(layer_name)
        if style is None:
            # Los archivos de capa suelen llamarse como la clase pero con el
            # nombre calificado o con el alias; se prueba sin distinguir
            # mayusculas y quitando el esquema de la geodatabase corporativa.
            lowered = layer_name.lower()
            short = lowered.split(".")[-1]
            for name, candidate in self.imported.items():
                if name.lower() in (lowered, short) or name.lower().split(".")[
                    -1
                ] == short:
                    return candidate
        return style

    # ------------------------------------------------------------------
    def _validate(self, style, layer_name, field_names):
        """Avisa de lo que quedaria mal sin llegar a romper el empaquetado."""
        if not field_names:
            return
        available = set(name.upper() for name in field_names)

        renderer = style.renderer
        if renderer is not None and renderer.field:
            if renderer.field.upper() not in available:
                self._warn(
                    "%s: la simbologia clasifica por '%s', que no viaja en el "
                    "paquete; se dibujara con un simbolo unico."
                    % (layer_name, renderer.field)
                )
                _degrade_to_single(renderer)

        label = style.label
        if label is not None and label.field and label.field.upper() not in available:
            self._warn(
                "%s: la etiqueta usa '%s', que no viaja en el paquete; se "
                "desactiva." % (layer_name, label.field)
            )
            label.enabled = False

    def _warn(self, text):
        if text not in self.warnings:
            self.warnings.append(text)

    # ------------------------------------------------------------------
    def summary(self):
        """Resumen legible de donde salio la simbologia de cada capa."""
        if not self.sources:
            return ""
        counts = {}
        for source in self.sources.values():
            counts[source] = counts.get(source, 0) + 1
        readable = {
            "estilo": "archivo de estilo",
            "lyrx": "archivos de capa de ArcGIS Pro",
            "arcpy": "MXD/LYR de ArcGIS (solo clasificacion)",
            "automatico": "automatica",
        }
        parts = [
            "%d %s" % (count, readable.get(source, source))
            for source, count in sorted(counts.items())
        ]
        return "Simbologia: " + ", ".join(parts) + "."


def _degrade_to_single(renderer):
    """Convierte un renderizador roto en uno simple, conservando el color."""
    symbols = renderer.symbols
    renderer.kind = Renderer.SINGLE
    renderer.symbol = symbols[0] if symbols else None
    renderer.field = None
    renderer.categories = []
    renderer.ranges = []
    renderer.rules = []


def _load_profile_stylesheet(profile):
    """Carga el archivo de estilo que acompana al perfil, si existe."""
    if profile is None or not getattr(profile, "id", None):
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(
        os.path.dirname(here), "profiles", profile.id + PROFILE_STYLE_SUFFIX
    )
    if not os.path.isfile(path):
        return None
    try:
        return StyleSheet.load(path)
    except StyleSheetError:
        return None


# ----------------------------------------------------------------------
def load_symbology(source):
    """Importa simbologia de ArcGIS desde una ruta.

    Acepta una carpeta de ``.lyrx``, un ``.lyrx`` suelto, un ``.lyr``/``.mxd``
    (que requieren arcpy), o la palabra ``CURRENT`` para tomar la simbologia del
    mapa que ArcGIS tenga abierto en ese momento. Devuelve ``(estilos, avisos)``.
    """
    if not source:
        return {}, []

    lowered = source.lower()
    if lowered in ACTIVE_DOCUMENT_ALIASES:
        from .arcgis import read_active_document

        result = read_active_document()
        return result.styles, result.warnings

    if os.path.isdir(source):
        from .lyrx import read_lyrx_folder

        result = read_lyrx_folder(source)
        return result.styles, result.warnings

    if lowered.endswith(".lyrx"):
        from .lyrx import read_lyrx

        result = read_lyrx(source)
        return result.styles, result.warnings

    if lowered.endswith((".lyr", ".mxd", ".aprx")):
        from .arcgis import read_arcgis_document

        result = read_arcgis_document(source)
        return result.styles, result.warnings

    raise StyleSheetError(
        "No se reconoce '%s' como origen de simbologia. Use una carpeta con "
        "archivos .lyrx, un .lyrx, un .lyr/.mxd de ArcGIS, o 'CURRENT' para "
        "tomarla del mapa abierto." % source
    )


__all__ = [
    "ACTIVE_DOCUMENT_ALIASES",
    "Category",
    "Color",
    "FillStyle",
    "Label",
    "LabelPlacement",
    "LayerStyle",
    "LineStyle",
    "MarkerShape",
    "Range",
    "Renderer",
    "Rule",
    "StyleSheet",
    "StyleSheetError",
    "Symbol",
    "SymbolLayer",
    "SymbologyResolver",
    "describe_layer_style",
    "load_symbology",
]
