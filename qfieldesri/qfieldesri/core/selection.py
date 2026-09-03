# -*- coding: utf-8 -*-
"""Que clases se llevan a campo.

El ambito (:mod:`qfieldesri.core.scope`) decide **que trozo de la red** viaja:
un alimentador, una subestacion, un sector. Esto decide otra cosa distinta y
igual de necesaria: **que clases** viajan.

No es lo mismo salir a inventariar clientes que a revisar postes o a levantar
toda la red. Un paquete con las 47 clases del modelo abre lento en un telefono
y llena la leyenda de capas que esa brigada no va a tocar. Las dos preguntas
son independientes y se combinan: "los clientes **del** alimentador 04BH070T11".

Tres formas de responder, de la mas comoda a la mas precisa:

1. **Conjuntos tematicos**: "solo clientes", "postes y lo que cuelga de ellos",
   "red de media tension"... Los declara el perfil, porque saber que
   ``CONEXIONCONSUMIDOR`` es cosa de clientes es conocimiento del modelo, no de
   la geodatabase.
2. **Conjuntos por geometria**, que salen de la propia geodatabase y sirven
   para cualquier modelo: solo puntos, solo lineas, solo poligonos, solo tablas.
3. **Clase por clase**, marcando y desmarcando a mano.

Las tres se mezclan: se parte de un conjunto, se anade lo que falte y se quita
lo que sobre.

Y hay una cuarta pieza, que es la que hace util "solo postes": **arrastrar lo
relacionado**. Un poste sin sus estructuras montadas, sin las instituciones ni
las operadoras que cuelgan de el, no sirve para revisarlo en campo. Al pedir
una clase se ofrecen tambien las que dependen de ella, siguiendo las
relationship classes de la geodatabase.
"""

from .naming import find as find_class
from .naming import normalize

#: Identificador del conjunto que lo incluye todo.
EVERYTHING = "todo"

#: Conjuntos que se deducen de la propia geodatabase, sin perfil. Sirven para
#: cualquier modelo, tambien uno que no sea el de CNEL EP.
GEOMETRY_SETS = (
    ("puntos", "Solo puntos", "Point"),
    ("lineas", "Solo lineas", "Polyline"),
    ("poligonos", "Solo poligonos", "Polygon"),
)


class SelectionError(Exception):
    pass


class ClassSet(object):
    """Un conjunto de clases con nombre propio."""

    #: De donde sale el conjunto.
    PROFILE = "perfil"
    GEOMETRY = "geometria"
    BUILTIN = "programa"

    def __init__(self, id, name, classes=None, description="", source=PROFILE):  # noqa: A002
        self.id = id
        self.name = name
        self.classes = list(classes or [])
        self.description = description
        self.source = source

    def __len__(self):
        return len(self.classes)

    def __repr__(self):  # pragma: no cover
        return "<ClassSet %s (%d clases)>" % (self.id, len(self.classes))


class Selection(object):
    """Lo que el usuario eligio exportar."""

    def __init__(self, sets=None, classes=None, exclude=None, include_related=True):
        #: Conjuntos tematicos elegidos, por identificador.
        self.sets = list(sets or [])
        #: Clases anadidas a mano, ademas de las de los conjuntos.
        self.classes = list(classes or [])
        #: Clases que se quitan explicitamente, aunque vengan en un conjunto.
        self.exclude = list(exclude or [])
        #: Arrastrar las clases que dependen de las elegidas.
        self.include_related = include_related

    @property
    def is_empty(self):
        """Sin nada elegido se exporta todo, que es lo que espera el usuario."""
        return not self.sets and not self.classes and not self.exclude

    def to_dict(self):
        return {
            "sets": list(self.sets),
            "classes": list(self.classes),
            "exclude": list(self.exclude),
            "include_related": self.include_related,
        }

    @classmethod
    def from_dict(cls, data):
        if not data:
            return cls()
        if isinstance(data, cls):
            return data
        return cls(
            sets=data.get("sets"),
            classes=data.get("classes"),
            exclude=data.get("exclude"),
            include_related=data.get("include_related", True),
        )

    def __repr__(self):  # pragma: no cover
        return "<Selection %s + %d clases - %d>" % (
            ",".join(self.sets) or "todo",
            len(self.classes),
            len(self.exclude),
        )


class SelectionPlan(object):
    """Resultado de resolver una seleccion: que entra, que no y por que."""

    def __init__(self, selection):
        self.selection = selection
        #: ``{nombre_clase: motivo}`` de lo que se exporta.
        self.included = {}
        #: ``{nombre_clase: motivo}`` de lo que se queda fuera.
        self.excluded = {}
        self.notes = []

    @property
    def is_empty(self):
        return not self.included and not self.excluded

    def include(self, name, reason):
        self.excluded.pop(name, None)
        self.included.setdefault(name, reason)

    def exclude(self, name, reason):
        """Deja una clase fuera, conservando el primer motivo registrado.

        El barrido final marca todo lo que no entro; si esa clase ya se habia
        quitado a mano, el motivo bueno es ese y no el generico.
        """
        if name in self.included:
            return
        self.excluded.setdefault(name, reason)

    def keeps(self, name):
        return find_class(self.included, name) is not None

    def describe(self):
        """Explicacion legible de lo que se va a exportar y lo que no."""
        if self.is_empty:
            return ""
        lines = []
        if self.selection.sets:
            lines.append("Se exporta: %s" % ", ".join(self.selection.sets))
        by_reason = {}
        for name, reason in self.included.items():
            by_reason.setdefault(reason, []).append(name)
        for reason in sorted(by_reason):
            names = sorted(by_reason[reason])
            lines.append("  %s (%d): %s" % (reason, len(names), ", ".join(names)))
        if self.excluded:
            lines.append(
                "  Fuera del paquete (%d): %s"
                % (
                    len(self.excluded),
                    ", ".join(sorted(self.excluded)),
                )
            )
        for note in self.notes:
            lines.append("  %s" % note)
        return "\n".join(lines)


class SelectionResolver(object):
    """Traduce la eleccion del usuario en la lista de clases a exportar."""

    def __init__(self, workspace, profile=None):
        self.workspace = workspace
        self.profile = profile

    # ------------------------------------------------------------------
    def available_sets(self):
        """Conjuntos ofrecibles para **esta** geodatabase.

        Se descartan los que no tengan ninguna clase presente: ofrecer "solo
        alumbrado" en una geodatabase sin luminarias solo sirve para generar un
        paquete vacio y una llamada de telefono.
        """
        present = [layer.name for layer in self.workspace.layers]
        sets = [
            ClassSet(
                EVERYTHING,
                "Toda la geodatabase",
                classes=list(present),
                description="Las %d clases del origen." % len(present),
                source=ClassSet.BUILTIN,
            )
        ]

        for definition in self._profile_sets():
            classes = [
                match
                for match in (
                    find_class(present, name) for name in definition.get("classes", [])
                )
                if match
            ]
            if not classes:
                continue
            sets.append(
                ClassSet(
                    definition["id"],
                    definition.get("name", definition["id"]),
                    classes=classes,
                    description=definition.get("description", ""),
                    source=ClassSet.PROFILE,
                )
            )

        for identifier, name, geometry in GEOMETRY_SETS:
            classes = [
                layer.name
                for layer in self.workspace.layers
                if layer.geometry_type == geometry
            ]
            if classes:
                sets.append(
                    ClassSet(
                        identifier,
                        name,
                        classes=classes,
                        source=ClassSet.GEOMETRY,
                    )
                )

        tables = [layer.name for layer in self.workspace.layers if not layer.is_spatial]
        if tables:
            sets.append(
                ClassSet(
                    "tablas",
                    "Solo tablas (sin geometria)",
                    classes=tables,
                    source=ClassSet.GEOMETRY,
                )
            )
        return sets

    def set_by_id(self, identifier):
        for candidate in self.available_sets():
            if candidate.id == identifier:
                return candidate
        return None

    def _profile_sets(self):
        if self.profile is None:
            return []
        return self.profile.class_sets()

    # ------------------------------------------------------------------
    def resolve(self, selection):
        """Devuelve el :class:`SelectionPlan` de una seleccion."""
        selection = Selection.from_dict(selection)
        plan = SelectionPlan(selection)
        if selection.is_empty:
            return plan

        available = dict(
            (candidate.id, candidate) for candidate in self.available_sets()
        )
        for identifier in selection.sets:
            candidate = available.get(identifier)
            if candidate is None:
                raise SelectionError(
                    "No existe el conjunto '%s'. Disponibles: %s"
                    % (identifier, ", ".join(sorted(available)))
                )
            for name in candidate.classes:
                plan.include(name, "Del conjunto '%s'" % candidate.name)

        present = [layer.name for layer in self.workspace.layers]
        for name in selection.classes:
            match = find_class(present, name)
            if match is None:
                plan.notes.append(
                    "'%s' no existe en la geodatabase y se ignora." % name
                )
                continue
            plan.include(match, "Elegida a mano")

        if selection.include_related:
            self._add_related(plan)

        for name in selection.exclude:
            match = find_class(present, name)
            if match is not None:
                plan.included.pop(match, None)
                plan.excluded[match] = "Quitada a mano"
                continue
            plan.notes.append("'%s' no existe en la geodatabase y se ignora." % name)

        for layer in self.workspace.layers:
            plan.exclude(layer.name, "No entra en lo elegido")
        return plan

    def _add_related(self, plan):
        """Arrastra lo que cuelga de lo elegido.

        Sin esto, "solo postes" da un poste pelado: sin las estructuras
        montadas en el, sin las instituciones ni las operadoras. Se repite
        hasta que no cambie nada, porque una tabla puede colgar de otra que a
        su vez cuelga de una clase elegida.
        """
        names = dict(
            (normalize(layer.name), layer.name) for layer in self.workspace.layers
        )
        for _pass in range(3):
            changed = False
            for relationship in self.workspace.relationships:
                if relationship.is_attachment:
                    continue
                origin = names.get(normalize(relationship.origin))
                destination = names.get(normalize(relationship.destination))
                if origin is None or destination is None:
                    continue
                if origin not in plan.included or destination in plan.included:
                    continue
                plan.include(
                    destination,
                    "Relacionada con una clase elegida",
                )
                changed = True
            if not changed:
                break
