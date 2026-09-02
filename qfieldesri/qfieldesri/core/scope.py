# -*- coding: utf-8 -*-
"""Ambito de exportacion: que trozo de la red se lleva a campo.

Nadie sale a campo con toda la Unidad de Negocio. Se sale con **un
alimentador**, con **una subestacion**, con **un sector delimitado por un
poligono** o con **una parroquia**. Este modulo traduce esa decision —una sola
eleccion del usuario— en el filtro concreto que le toca a cada clase de la
geodatabase, que no es lo mismo para todas.

Por que hace falta resolverlo y no basta con una clausula WHERE
----------------------------------------------------------------
En el modelo de CNEL EP el campo de alimentador existe en 26 de las 47 clases.
Las 21 restantes son casi todas tablas **Unidad** (los transformadores de un
puesto, las estructuras de un poste) y catalogos: no tienen alimentador porque
lo heredan de su **Puesto**. Un filtro escrito a mano por clase seria imposible
de mantener y, peor, dejaria fuera las Unidades del material que si viaja.

La resolucion, por tanto, va por tres caminos segun la clase:

1. **por atributo** cuando la clase tiene el campo del ambito;
2. **por relacion** cuando no lo tiene pero cuelga de una clase que si:
   la Unidad se filtra con las claves del Puesto que realmente se exporto;
3. **completa**, avisando, cuando no hay ni campo ni relacion (catalogos).

La subestacion se resuelve en dos pasos, como manda el modelo: la tabla
``CIRCUITOFUENTE`` (un registro por alimentador, con ``IDSUBESTACION`` y
``CODIGOALIMENTADOR``) da los alimentadores de esa subestacion, y a partir de
ahi el ambito se comporta como un ambito por alimentador.
"""

from .naming import normalize


class ScopeKind(object):
    """Formas de acotar la exportacion."""

    #: Uno o varios alimentadores (lo habitual para una brigada).
    ALIMENTADOR = "alimentador"
    #: Una subestacion: se expande a sus alimentadores via CIRCUITOFUENTE.
    SUBESTACION = "subestacion"
    PROVINCIA = "provincia"
    CANTON = "canton"
    PARROQUIA = "parroquia"
    #: Un poligono de sector: filtro espacial.
    POLIGONO = "poligono"

    ALL = (ALIMENTADOR, SUBESTACION, PROVINCIA, CANTON, PARROQUIA, POLIGONO)

    #: Los que se resuelven comparando un campo con una lista de valores.
    ATTRIBUTE_KINDS = (ALIMENTADOR, SUBESTACION, PROVINCIA, CANTON, PARROQUIA)

    LABELS = {  # noqa: RUF012 - constante de solo lectura
        ALIMENTADOR: "Alimentador",
        SUBESTACION: "Subestacion",
        PROVINCIA: "Provincia",
        CANTON: "Canton",
        PARROQUIA: "Parroquia",
        POLIGONO: "Poligono de sector",
    }


#: Maximo de valores por clausula ``IN``. Oracle corta en 1000 y SQL Server se
#: degrada mucho antes; se trocea y se recorre la clase una vez por trozo.
IN_CHUNK_SIZE = 900


class ScopeError(Exception):
    pass


class Scope(object):
    """La eleccion del usuario, sin resolver todavia."""

    def __init__(
        self,
        kind=None,
        values=None,
        polygon_wkt=None,
        polygon_crs=None,
        polygon_layer=None,
        polygon_where=None,
        follow_relationships=True,
        keep_catalogs=True,
    ):
        self.kind = kind
        #: codigos elegidos (alimentadores, cantones...)
        self.values = list(values or [])
        self.polygon_wkt = polygon_wkt
        self.polygon_crs = polygon_crs
        #: alternativa al WKT: una clase de poligonos de la geodatabase
        self.polygon_layer = polygon_layer
        self.polygon_where = polygon_where
        #: arrastrar las Unidades de los Puestos exportados
        self.follow_relationships = follow_relationships
        #: los catalogos pequenos viajan completos (son listas de valores)
        self.keep_catalogs = keep_catalogs

    @property
    def is_empty(self):
        if self.kind == ScopeKind.POLIGONO:
            return not (self.polygon_wkt or self.polygon_layer)
        return not self.kind or not self.values

    @property
    def is_spatial(self):
        return self.kind == ScopeKind.POLIGONO

    def label(self):
        if self.is_empty:
            return "Geodatabase completa"
        if self.is_spatial:
            return "Poligono de sector"
        return "%s: %s" % (
            ScopeKind.LABELS.get(self.kind, self.kind),
            ", ".join(str(value) for value in self.values[:5])
            + (" (+%d)" % (len(self.values) - 5) if len(self.values) > 5 else ""),
        )

    def to_dict(self):
        return {
            "kind": self.kind,
            "values": self.values,
            "polygon_wkt": self.polygon_wkt,
            "polygon_crs": self.polygon_crs,
            "polygon_layer": self.polygon_layer,
            "polygon_where": self.polygon_where,
            "follow_relationships": self.follow_relationships,
            "keep_catalogs": self.keep_catalogs,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**(data or {}))

    def __repr__(self):  # pragma: no cover
        return "<Scope %s>" % self.label()


class LayerFilter(object):
    """Filtro resuelto para una clase concreta."""

    BY_ATTRIBUTE = "atributo"
    BY_RELATIONSHIP = "relacion"
    BY_GEOMETRY = "geometria"
    UNFILTERED = "completa"

    def __init__(
        self,
        layer,
        method,
        field=None,
        values=None,
        parent=None,
        parent_field=None,
        reason="",
    ):
        self.layer = layer
        self.method = method
        self.field = field
        self.values = list(values or [])
        #: clase de la que hereda el filtro (metodo ``relacion``)
        self.parent = parent
        #: campo del padre cuyos valores hay que recoger al exportarlo
        self.parent_field = parent_field
        self.reason = reason

    def where_clauses(self, delimit=None):
        """Clausulas ``WHERE``, troceadas para no reventar el ``IN``.

        ``delimit`` permite pasar ``arcpy.AddFieldDelimiters`` para citar el
        nombre del campo como espere el motor de la geodatabase.
        """
        if self.method in (self.UNFILTERED, self.BY_GEOMETRY) or not self.field:
            return []
        if not self.values:
            # Filtro resuelto pero sin ningun valor: la clase queda vacia.
            # Es preferible a exportarla entera por descuido.
            return ["1 = 0"]
        name = delimit(self.field) if delimit else self.field
        clauses = []
        for start in range(0, len(self.values), IN_CHUNK_SIZE):
            chunk = self.values[start : start + IN_CHUNK_SIZE]
            clauses.append("%s IN (%s)" % (name, ", ".join(_literal(v) for v in chunk)))
        return clauses

    def describe(self):
        if self.method == self.BY_ATTRIBUTE:
            return "%s: por atributo %s (%d valores)" % (
                self.layer,
                self.field,
                len(self.values),
            )
        if self.method == self.BY_RELATIONSHIP:
            return "%s: hereda de %s por %s" % (self.layer, self.parent, self.field)
        if self.method == self.BY_GEOMETRY:
            return "%s: recorte espacial" % self.layer
        return "%s: completa (%s)" % (self.layer, self.reason or "sin campo de ambito")

    def __repr__(self):  # pragma: no cover
        return "<LayerFilter %s>" % self.describe()


class ScopePlan(object):
    """Como queda cada clase tras resolver el ambito."""

    def __init__(self, scope):
        self.scope = scope
        #: ``{nombre_clase: LayerFilter}``
        self.filters = {}
        self.aoi_wkt = scope.polygon_wkt
        self.aoi_crs = scope.polygon_crs
        self.notes = []

    def add(self, layer_filter):
        self.filters[layer_filter.layer] = layer_filter
        return layer_filter

    def filter_for(self, layer_name):
        return self.filters.get(layer_name)

    def of_method(self, method):
        return [f for f in self.filters.values() if f.method == method]

    @property
    def is_empty(self):
        return self.scope.is_empty

    def describe(self):
        """Texto que se le ensena al usuario antes de empaquetar."""
        lines = ["Ambito: %s" % self.scope.label()]
        for method, title in (
            (LayerFilter.BY_ATTRIBUTE, "Filtradas por atributo"),
            (LayerFilter.BY_RELATIONSHIP, "Filtradas por relacion con su Puesto"),
            (LayerFilter.BY_GEOMETRY, "Recortadas por geometria"),
            (LayerFilter.UNFILTERED, "Se exportan completas"),
        ):
            entries = sorted(f.layer for f in self.of_method(method))
            if entries:
                lines.append(
                    "  %s (%d): %s" % (title, len(entries), ", ".join(entries))
                )
        lines.extend("  Nota: %s" % note for note in self.notes)
        return "\n".join(lines)


class ScopeResolver(object):
    """Traduce un :class:`Scope` en el filtro de cada clase."""

    def __init__(self, workspace, profile, reader=None):
        self.workspace = workspace
        self.profile = profile
        self.reader = reader

    # ------------------------------------------------------------------
    # valores disponibles para elegir
    # ------------------------------------------------------------------
    def available_values(self, kind, only_present_in=None):
        """Valores elegibles para un ambito, como ``[(codigo, etiqueta)]``.

        Se leen del **dominio de la geodatabase activa**, nunca de una lista
        fija: el catalogo del modelo advierte que los alimentadores y las
        subestaciones cambian en cada Unidad de Negocio.

        ``only_present_in`` es el nombre de una clase; si se indica, se
        devuelven solo los valores que de verdad aparecen en sus datos, que es
        lo util para no ofrecer 246 alimentadores cuando la geodatabase de
        trabajo solo tiene 12.
        """
        domain_name = self.profile.scope_domain(kind)
        domain = self.workspace.domains.get(domain_name) if domain_name else None
        values = list(domain.coded_values) if domain and domain.is_coded else []

        present = None
        if only_present_in and self.reader is not None:
            present = self._distinct(only_present_in, kind)

        if present is not None:
            known = dict(values)
            values = [(code, known.get(code, code)) for code in sorted(present)]
        return values

    def _distinct(self, layer_name, kind):
        layer = self.workspace.layer(layer_name)
        if layer is None:
            return None
        field = self.field_for(layer, kind)
        if not field:
            return None
        seen = set()
        for _wkb, attributes in self.reader.iter_features(layer, [field]):
            value = attributes.get(field)
            if value not in (None, ""):
                seen.add(value)
        return seen

    # ------------------------------------------------------------------
    # resolucion
    # ------------------------------------------------------------------
    def field_for(self, layer, kind):
        """Primer campo del ambito que exista en la clase."""
        for candidate in self.profile.scope_fields(kind):
            field = layer.field(candidate)
            if field is not None:
                return field.name
        return None

    def expand_values(self, scope):
        """Convierte el ambito en la lista de valores que se compara.

        Una subestacion no aparece como campo en las clases de red: se traduce
        a los alimentadores que cuelgan de ella usando ``CIRCUITOFUENTE``, que
        es exactamente para lo que existe esa tabla en el modelo.
        """
        if scope.kind != ScopeKind.SUBESTACION:
            return scope.kind, list(scope.values)

        indirect = self.profile.scope_indirect(ScopeKind.SUBESTACION)
        if not indirect:
            return scope.kind, list(scope.values)

        table = self.workspace.layer(indirect["table"])
        if table is None or self.reader is None:
            raise ScopeError(
                "Para exportar por subestacion hace falta la tabla '%s' de la "
                "geodatabase, que es la que relaciona subestacion y "
                "alimentador." % indirect["table"]
            )

        wanted = set(str(value) for value in scope.values)
        feeders = []
        for _wkb, attributes in self.reader.iter_features(
            table, [indirect["key_field"], indirect["value_field"]]
        ):
            if str(attributes.get(indirect["key_field"])) in wanted:
                feeder = attributes.get(indirect["value_field"])
                if feeder not in (None, "") and feeder not in feeders:
                    feeders.append(feeder)
        return ScopeKind.ALIMENTADOR, feeders

    def resolve(self, scope, layers=None):
        """Devuelve el :class:`ScopePlan` de un ambito."""
        plan = ScopePlan(scope)
        layers = layers if layers is not None else self.workspace.layers
        if scope.is_empty:
            return plan

        if scope.is_spatial:
            self._resolve_spatial(scope, plan, layers)
            return plan

        kind, values = self.expand_values(scope)
        if scope.kind == ScopeKind.SUBESTACION:
            plan.notes.append(
                "La subestacion se resolvio a %d alimentador(es) via %s."
                % (
                    len(values),
                    self.profile.scope_indirect(ScopeKind.SUBESTACION)["table"],
                )
            )
            if not values:
                plan.notes.append(
                    "Ninguna subestacion elegida tiene alimentadores "
                    "registrados: el paquete saldria vacio."
                )

        for layer in layers:
            field = self.field_for(layer, kind)
            if field:
                plan.add(
                    LayerFilter(
                        layer.name,
                        LayerFilter.BY_ATTRIBUTE,
                        field=field,
                        values=values,
                    )
                )

        self._resolve_by_relationship(plan, layers, scope)
        self._resolve_leftovers(plan, layers, scope)
        return plan

    def _resolve_spatial(self, scope, plan, layers):
        if not plan.aoi_wkt and scope.polygon_layer:
            if self.reader is None:
                raise ScopeError(
                    "Para acotar por un poligono de la geodatabase hace falta "
                    "leerla; indique el poligono en WKT o ejecute desde ArcGIS."
                )
            plan.aoi_wkt, plan.aoi_crs = self.reader.union_wkt(
                scope.polygon_layer, scope.polygon_where
            )
            if not plan.aoi_wkt:
                raise ScopeError(
                    "La capa '%s' no devolvio ningun poligono%s."
                    % (
                        scope.polygon_layer,
                        " con el filtro indicado" if scope.polygon_where else "",
                    )
                )
            plan.notes.append(
                "Area de interes tomada de '%s'%s."
                % (
                    scope.polygon_layer,
                    " (%s)" % scope.polygon_where if scope.polygon_where else "",
                )
            )
        for layer in layers:
            if layer.is_spatial:
                plan.add(LayerFilter(layer.name, LayerFilter.BY_GEOMETRY))
        self._resolve_by_relationship(plan, layers, scope)
        self._resolve_leftovers(plan, layers, scope)

    def _resolve_by_relationship(self, plan, layers, scope):
        """Hace que las Unidades sigan a su Puesto.

        Se repite hasta que no cambie nada, porque una tabla puede colgar de
        otra que a su vez cuelga de una clase filtrada.
        """
        if not scope.follow_relationships:
            return
        # Se indexa por nombre normalizado porque en una geodatabase
        # corporativa la relacion puede nombrar las clases con una
        # calificacion distinta de la que devuelve el recorrido del workspace.
        names = {}
        for layer in layers:
            names.setdefault(layer.name.lower(), layer)
            names.setdefault(normalize(layer.name), layer)

        for _pass in range(3):
            changed = False
            for relationship in self.workspace.relationships:
                if relationship.is_attachment:
                    continue
                child = names.get(relationship.destination.lower()) or names.get(
                    normalize(relationship.destination)
                )
                parent = names.get(relationship.origin.lower()) or names.get(
                    normalize(relationship.origin)
                )
                if child is None or parent is None:
                    continue
                if child.name in plan.filters or parent.name not in plan.filters:
                    continue
                parent_filter = plan.filters[parent.name]
                if parent_filter.method == LayerFilter.UNFILTERED:
                    continue
                if child.field(relationship.destination_key) is None:
                    continue
                if parent.field(relationship.origin_key) is None:
                    continue
                plan.add(
                    LayerFilter(
                        child.name,
                        LayerFilter.BY_RELATIONSHIP,
                        field=relationship.destination_key,
                        parent=parent.name,
                        parent_field=relationship.origin_key,
                    )
                )
                changed = True
            if not changed:
                break

    def _resolve_leftovers(self, plan, layers, scope):
        for layer in layers:
            if layer.name in plan.filters:
                continue
            kind_of = self.profile.kind_of(layer.name)
            if kind_of == "catalogo" and scope.keep_catalogs:
                reason = "es un catalogo: se lleva completo"
            elif not layer.is_spatial:
                reason = "tabla sin campo de ambito ni relacion con una clase filtrada"
            else:
                reason = "sin campo de ambito"
            plan.add(LayerFilter(layer.name, LayerFilter.UNFILTERED, reason=reason))


def _literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'%s'" % str(value).replace("'", "''")


def combine(where_clause, scope_clause):
    """Une el filtro del usuario con el del ambito."""
    if where_clause and scope_clause:
        return "(%s) AND (%s)" % (where_clause, scope_clause)
    return where_clause or scope_clause or None
