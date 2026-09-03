# -*- coding: utf-8 -*-
"""Perfiles de modelo de datos.

Un perfil aporta lo que la geodatabase no sabe de si misma: que campos son
realmente obligatorios segun la normativa, cuales son de auditoria, como
agrupar las clases en el arbol de capas y que pares Puesto/Unidad existen.
Los dominios, subtipos y relationship classes NO estan en el perfil: se leen
en caliente de cada geodatabase, porque cambian de una Unidad de Negocio a
otra (el propio catalogo advierte de los dominios ``Codigo Alimentador``,
``Numero Estacion`` y ``Subestacion``).

Perfiles incluidos:

``cnel_ep``
    Modelo electrico homologado de CNEL EP, generado desde ``docs/modelo``
    con ``tools/build_profile.py``.
``generico``
    Cualquier otra geodatabase: clasifica los campos por heuristica de nombre.
"""

import io
import json
import os

from ..core.model import (
    CATEGORY_CONNECTIVITY,
    CATEGORY_CORE,
    CATEGORY_OTHER,
    CATEGORY_SYSTEM,
)
from ..core.naming import normalize as _normalize_class

HERE = os.path.dirname(os.path.abspath(__file__))

#: Sufijo del archivo de estilo que acompana a un perfil (ver ``symbology``).
STYLE_SUFFIX = ".estilo.json"

#: Campos de auditoria y metadatos que se repiten en casi todas las clases
#: del modelo (y, en general, en cualquier geodatabase corporativa).
SYSTEM_FIELD_HINTS = (
    "OBJECTID",
    "GLOBALID",
    "MIGUID",
    "MIOID",
    "MISUBTIPO",
    "SHAPE",
    "SHAPE_LENGTH",
    "SHAPE_AREA",
    "USUARIOREGISTRO",
    "USUARIOMODIFICACIONREGISTRO",
    "FECHAREGISTRO",
    "FECHAMODIFICACIONREGISTRO",
    "FECHACONSTRUCCION",
    "PROYECTOCONSTRUCCION",
    "PROYECTOMODIFICACION",
    "ORDENTRABAJO",
    "HIPERVINCULO",
    "OBSERVACIONES",
    "COMENTARIOS",
    "TEXTOETIQUETA",
    "PROVINCIA",
    "CANTON",
    "PARROQUIA",
    "CREATED_USER",
    "CREATED_DATE",
    "LAST_EDITED_USER",
    "LAST_EDITED_DATE",
)

CONNECTIVITY_FIELD_HINTS = (
    "ANCILLARYROLE",
    "ELECTRICTRACEWEIGHT",
    "ENABLED",
    "CIRCUITSOURCEGUID",
    "PARENTCIRCUITSOURCEGUID",
)

#: Pestana del formulario de QField segun la categoria del campo.
FORM_GROUPS = {
    CATEGORY_CORE: "Datos obligatorios",
    CATEGORY_OTHER: "Atributos",
    CATEGORY_CONNECTIVITY: "Conectividad",
    CATEGORY_SYSTEM: "Sistema",
}

#: Orden en que aparecen las pestanas.
FORM_GROUP_ORDER = (
    "Datos obligatorios",
    "Atributos",
    "Conectividad",
    "Ubicacion",
    "Sistema",
)

#: Campos administrativos que conviene agrupar aparte para poder reutilizar el
#: ultimo valor a lo largo de una jornada de campo.
LOCATION_FIELDS = ("PROVINCIA", "CANTON", "PARROQUIA", "CODIGOEMPRESA")

#: Campos con los que se acota la exportacion cuando el perfil no dice otra
#: cosa. Sirven tal cual para el perfil generico y para cualquier geodatabase
#: que siga la nomenclatura del modelo nacional.
DEFAULT_SCOPE_FIELDS = {
    "alimentador": ["ALIMENTADORID", "ALIMENTADOR"],
    "subestacion": ["IDSUBESTACION", "NUMEROSUBESTACION"],
    "provincia": ["PROVINCIA"],
    "canton": ["CANTON"],
    "parroquia": ["PARROQUIA"],
}


class Profile(object):
    """Perfil de modelo de datos."""

    def __init__(self, data=None):
        data = data or {}
        self.id = data.get("id", "generico")
        self.name = data.get("name", "Geodatabase generica")
        self.description = data.get("description", "")
        self.crs = data.get("crs")
        self.feature_datasets = data.get("feature_datasets", [])
        self.network = data.get("network", {})
        self.connectivity_fields = tuple(
            data.get("connectivity_fields", CONNECTIVITY_FIELD_HINTS)
        )
        self.source_classes = data.get("source_classes", [])
        self.sink_classes = data.get("sink_classes", [])
        self.variable_domains = data.get("variable_domains", [])
        self.classes = data.get("classes", {})
        #: Conjuntos tematicos: que clases se lleva cada tipo de trabajo.
        self._class_sets = data.get("class_sets", [])
        self.relationships = data.get("relationships", [])
        self._scope_fields = data.get("scope_fields") or DEFAULT_SCOPE_FIELDS
        self._scope_domains = data.get("scope_domains") or {}
        self._scope_indirect = data.get("scope_indirect") or {}
        # Indice por nombre normalizado: en una geodatabase corporativa la
        # clase llega calificada y en mayusculas (``SIGELEC.BARRA``), y el perfil
        # tiene que reconocerla igual.
        self._lower_classes = {}
        for name in self.classes:
            self._lower_classes.setdefault(name.lower(), name)
            self._lower_classes.setdefault(_normalize_class(name), name)

    # ------------------------------------------------------------------
    def class_definition(self, class_name):
        key = self._lower_classes.get((class_name or "").lower())
        if key is None:
            key = self._lower_classes.get(_normalize_class(class_name))
        return self.classes.get(key) if key else None

    def knows(self, class_name):
        return self.class_definition(class_name) is not None

    def category_of(self, class_name, field_name):
        """Categoria de un campo: ``core``, ``conectividad``, ``sistema`` u ``otro``.

        Si la clase esta en el perfil se usa el dato del catalogo; si no (una
        clase nueva, o una geodatabase que no sea la de CNEL EP) se decide por
        heuristica de nombre, que es exactamente el criterio con el que el
        catalogo clasifico los campos comunes.
        """
        definition = self.class_definition(class_name)
        if definition:
            category = definition.get("fields", {}).get(field_name)
            if category:
                return category
            # Comparacion insensible a mayusculas por si la geodatabase de otra
            # Unidad de Negocio escribe el campo distinto.
            upper = field_name.upper()
            for name, category in definition.get("fields", {}).items():
                if name.upper() == upper:
                    return category
        return self.guess_category(field_name)

    @staticmethod
    def guess_category(field_name):
        upper = (field_name or "").upper()
        if upper in CONNECTIVITY_FIELD_HINTS:
            return CATEGORY_CONNECTIVITY
        if upper in SYSTEM_FIELD_HINTS:
            return CATEGORY_SYSTEM
        return CATEGORY_OTHER

    def alias_of(self, class_name, field_name, fallback=None):
        definition = self.class_definition(class_name)
        if definition:
            alias = definition.get("aliases", {}).get(field_name)
            if alias:
                return alias
        return fallback or field_name

    def group_of(self, class_name):
        """Grupo del arbol de capas de QField."""
        definition = self.class_definition(class_name)
        return definition.get("group") if definition else None

    def kind_of(self, class_name):
        """``puesto``, ``unidad``, ``tramo``, ``catalogo`` o ``None``."""
        definition = self.class_definition(class_name)
        return definition.get("kind") if definition else None

    def class_alias(self, class_name, fallback=None):
        definition = self.class_definition(class_name)
        if definition and definition.get("alias"):
            return definition["alias"]
        return fallback or class_name

    def network_role(self, class_name):
        definition = self.class_definition(class_name)
        return definition.get("network_role") if definition else None

    def is_source_class(self, class_name):
        key = _normalize_class(class_name)
        return any(_normalize_class(name) == key for name in self.source_classes)

    def form_group_of(self, class_name, field_name):
        """Pestana del formulario donde cae el campo."""
        if (field_name or "").upper() in LOCATION_FIELDS:
            return "Ubicacion"
        return FORM_GROUPS.get(
            self.category_of(class_name, field_name), FORM_GROUPS[CATEGORY_OTHER]
        )

    def subtype_names(self, class_name):
        """``{codigo: nombre}`` documentado en el catalogo (solo informativo).

        Lo autoritativo es lo que devuelva la geodatabase; esto sirve para
        avisar al usuario cuando el esquema real se ha separado del catalogo.
        """
        definition = self.class_definition(class_name)
        if not definition:
            return {}
        return dict(
            (int(code), entry.get("name", code))
            for code, entry in definition.get("subtypes", {}).items()
        )

    def class_sets(self):
        """Conjuntos tematicos declarados por el perfil.

        Saber que ``CONEXIONCONSUMIDOR`` es cosa de clientes y no de la red es
        conocimiento del modelo, no de la geodatabase: por eso vive aqui y no
        se deduce en caliente.
        """
        return list(self._class_sets)

    def documented_relationships(self):
        return self.relationships

    # -- ambitos de exportacion -----------------------------------------
    def scope_fields(self, kind):
        """Campos candidatos para acotar por ese ambito, en orden."""
        return list(self._scope_fields.get(kind, []))

    def scope_domain(self, kind):
        """Dominio del que se leen los valores elegibles, o ``None``."""
        return self._scope_domains.get(kind)

    def scope_indirect(self, kind):
        """Como traducir un ambito que no es un campo de las clases de red."""
        return self._scope_indirect.get(kind)

    def supported_scopes(self):
        """Ambitos que este perfil sabe resolver."""
        kinds = set(self._scope_fields) | set(self._scope_indirect)
        return sorted(kinds)

    def __repr__(self):  # pragma: no cover
        return "<Profile %s (%d clases)>" % (self.id, len(self.classes))


_CACHE = {}


def load_profile(name="cnel_ep"):
    """Carga un perfil por nombre. ``generico`` no necesita archivo."""
    if not name or name in ("generico", "generic", "none"):
        return Profile()
    if name in _CACHE:
        return _CACHE[name]

    path = name if os.path.isfile(name) else os.path.join(HERE, "%s.json" % name)
    if not os.path.isfile(path):
        raise ValueError(
            "No existe el perfil '%s'. Perfiles disponibles: %s"
            % (name, ", ".join(available_profiles()))
        )
    with io.open(path, "r", encoding="utf-8") as handle:
        profile = Profile(json.load(handle))
    _CACHE[name] = profile
    return profile


def available_profiles():
    """Perfiles instalados, sin contar los archivos de estilo que los acompanan.

    Junto a ``cnel_ep.json`` vive ``cnel_ep.estilo.json``, que es simbologia
    del perfil y no un perfil aparte: ofrecerlo como modelo de datos solo
    confunde a quien elige en la lista.
    """
    names = ["generico"]
    for filename in sorted(os.listdir(HERE)):
        if filename.endswith(STYLE_SUFFIX):
            continue
        if filename.endswith(".json"):
            names.append(filename[: -len(".json")])
    return names
