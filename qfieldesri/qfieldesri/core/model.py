"""Modelo de metadatos neutro de qfieldESRI.

Estas clases son el contrato entre los lectores (``qfieldesri.readers``) y los
escritores (``qfieldesri.writers``). No dependen de arcpy, ni de GDAL, ni de
QGIS: un lector rellena estos objetos y un escritor los consume, y ninguno de
los dos necesita nada instalado ademas de ArcGIS. Gracias a eso
el mismo empaquetador sirve para una File Geodatabase local y para una
geodatabase corporativa (SDE) sin cambiar una linea del generador de salida,
y las pruebas pueden usar un lector simulado.

Se escriben como clases sencillas (sin ``dataclasses``) porque ArcMap 10.x
sigue ejecutando Python 2.7.
"""

from .naming import find as _find_class
from .naming import same_class, short_name

# --- categorias de campo del modelo electrico CNEL EP ---------------------
# Provienen del manual MN-TEC-OPE-100 y del catalogo del modelo (docs/modelo).
CATEGORY_CORE = "core"  # obligatorio segun el manual
CATEGORY_CONNECTIVITY = "conectividad"  # red geometrica / trazado electrico
CATEGORY_SYSTEM = "sistema"  # auditoria, identificadores, ubicacion
CATEGORY_OTHER = "otro"  # atributos propios no confirmados como obligatorios

ALL_CATEGORIES = (
    CATEGORY_CORE,
    CATEGORY_CONNECTIVITY,
    CATEGORY_SYSTEM,
    CATEGORY_OTHER,
)

#: Campos que ArcGIS gestiona solo y que no tiene sentido enviar a campo como
#: editables (se envian ocultos cuando se necesitan para la sincronizacion).
MANAGED_FIELD_TYPES = ("oid", "globalid", "geometry")


class DomainInfo(object):
    """Un dominio de geodatabase (lista codificada o rango)."""

    CODED = "codedValue"
    RANGE = "range"

    def __init__(
        self,
        name,
        domain_type=CODED,
        field_type="String",
        coded_values=None,
        range_min=None,
        range_max=None,
        description="",
    ):
        self.name = name
        self.domain_type = domain_type
        self.field_type = field_type
        #: lista de pares ``(codigo, descripcion)`` conservando el orden
        self.coded_values = list(coded_values or [])
        self.range_min = range_min
        self.range_max = range_max
        self.description = description

    @property
    def is_coded(self):
        return self.domain_type == self.CODED

    def __len__(self):
        return len(self.coded_values)

    def __repr__(self):  # pragma: no cover
        return "<DomainInfo %s (%s, %d valores)>" % (
            self.name,
            self.domain_type,
            len(self.coded_values),
        )


class FieldInfo(object):
    """Un campo de una clase de entidad o tabla."""

    def __init__(
        self,
        name,
        field_type="String",
        alias=None,
        length=None,
        nullable=True,
        editable=True,
        domain=None,
        default_value=None,
        category=CATEGORY_OTHER,
    ):
        self.name = name
        self.field_type = field_type
        self.alias = alias or name
        self.length = length
        self.nullable = nullable
        self.editable = editable
        #: nombre del dominio a nivel de clase (los subtipos pueden cambiarlo)
        self.domain = domain or None
        self.default_value = default_value
        self.category = category

    @property
    def is_managed(self):
        return (self.field_type or "").lower() in MANAGED_FIELD_TYPES

    def __repr__(self):  # pragma: no cover
        return "<FieldInfo %s %s>" % (self.name, self.field_type)


class SubtypeInfo(object):
    """Un subtipo de una clase, con sus dominios y valores por defecto."""

    def __init__(self, code, name, is_default=False, domains=None, defaults=None):
        self.code = code
        self.name = name
        self.is_default = is_default
        #: ``{nombre_campo: nombre_dominio}``
        self.domains = dict(domains or {})
        #: ``{nombre_campo: valor}``
        self.defaults = dict(defaults or {})

    def __repr__(self):  # pragma: no cover
        return "<SubtypeInfo %s=%s>" % (self.code, self.name)


class RelationshipInfo(object):
    """Una relationship class de la geodatabase."""

    SIMPLE = "simple"
    COMPOSITE = "composite"

    def __init__(
        self,
        name,
        origin,
        destination,
        origin_key,
        destination_key,
        cardinality="OneToMany",
        relationship_type=SIMPLE,
        forward_label="",
        backward_label="",
        is_attachment=False,
    ):
        self.name = name
        #: clase de origen (el "Puesto" en el modelo CNEL EP)
        self.origin = origin
        #: clase o tabla de destino (la "Unidad")
        self.destination = destination
        self.origin_key = origin_key
        self.destination_key = destination_key
        self.cardinality = cardinality
        self.relationship_type = relationship_type
        self.forward_label = forward_label or destination
        self.backward_label = backward_label or origin
        self.is_attachment = is_attachment

    @property
    def is_composite(self):
        return self.relationship_type == self.COMPOSITE

    def __repr__(self):  # pragma: no cover
        return "<RelationshipInfo %s: %s -> %s>" % (
            self.name,
            self.origin,
            self.destination,
        )


class SpatialReferenceInfo(object):
    """Sistema de referencia espacial de una clase."""

    def __init__(self, code=None, name="", wkt="", is_geographic=False, proj4=""):
        self.code = code  # codigo EPSG (``factoryCode`` en arcpy)
        self.name = name
        self.wkt = wkt
        self.is_geographic = is_geographic
        self.proj4 = proj4

    @property
    def auth_id(self):
        return "EPSG:%d" % self.code if self.code else "USER:0"

    def __repr__(self):  # pragma: no cover
        return "<SpatialReferenceInfo %s %s>" % (self.auth_id, self.name)


class LayerInfo(object):
    """Una clase de entidad o tabla de la geodatabase."""

    FEATURE_CLASS = "FeatureClass"
    TABLE = "Table"

    def __init__(
        self,
        name,
        dataset_type=FEATURE_CLASS,
        path=None,
        alias=None,
        geometry_type=None,
        has_z=False,
        has_m=False,
        spatial_reference=None,
        fields=None,
        subtypes=None,
        subtype_field=None,
        oid_field="OBJECTID",
        globalid_field=None,
        feature_dataset=None,
        feature_count=None,
        is_versioned=False,
    ):
        self.name = name
        self.dataset_type = dataset_type
        #: ruta completa dentro del workspace (lo que consume arcpy)
        self.path = path or name
        self.alias = alias or name
        #: ``Point``, ``Polyline``, ``Polygon``, ``Multipoint`` o ``None``
        self.geometry_type = geometry_type
        self.has_z = has_z
        self.has_m = has_m
        self.spatial_reference = spatial_reference
        self.fields = list(fields or [])
        self.subtypes = list(subtypes or [])
        self.subtype_field = subtype_field
        self.oid_field = oid_field
        self.globalid_field = globalid_field
        self.feature_dataset = feature_dataset
        self.feature_count = feature_count
        #: Registrada como versionada en la geodatabase corporativa. Decide
        #: como hay que abrir la sesion de edicion al sincronizar de vuelta.
        self.is_versioned = is_versioned

    # -- consultas de conveniencia -------------------------------------
    @property
    def short_name(self):
        """Nombre sin el esquema: ``SIGELEC.BARRA`` -> ``BARRA``."""
        return short_name(self.name)

    @property
    def is_spatial(self):
        return self.dataset_type == self.FEATURE_CLASS and bool(self.geometry_type)

    def field(self, name):
        lowered = name.lower()
        for field in self.fields:
            if field.name.lower() == lowered:
                return field
        return None

    def field_names(self):
        return [field.name for field in self.fields]

    def subtype(self, code):
        for subtype in self.subtypes:
            if subtype.code == code:
                return subtype
        return None

    def domain_for(self, field_name, subtype_code=None):
        """Dominio efectivo de un campo, considerando el subtipo."""
        if subtype_code is not None:
            subtype = self.subtype(subtype_code)
            if subtype and field_name in subtype.domains:
                return subtype.domains[field_name]
        field = self.field(field_name)
        return field.domain if field else None

    def all_domains_for(self, field_name):
        """Todos los dominios que un campo puede tener en cualquier subtipo.

        En el modelo CNEL EP es habitual que un mismo campo cambie de dominio
        segun el subtipo (``VOLTAJE`` usa *Voltaje BT*, *MT* o *AT*). Como el
        formulario de QField no puede cambiar la lista al vuelo, el
        empaquetador une los dominios de todos los subtipos presentes.
        """
        names = []
        field = self.field(field_name)
        if field and field.domain:
            names.append(field.domain)
        for subtype in self.subtypes:
            domain = subtype.domains.get(field_name)
            if domain and domain not in names:
                names.append(domain)
        return names

    def __repr__(self):  # pragma: no cover
        return "<LayerInfo %s (%s, %d campos)>" % (
            self.name,
            self.geometry_type or self.dataset_type,
            len(self.fields),
        )


class WorkspaceInfo(object):
    """Todo lo que qfieldESRI necesita saber de una geodatabase."""

    FILE_GDB = "FileGDB"
    ENTERPRISE = "Enterprise"
    PERSONAL = "PersonalGDB"
    OTHER = "Other"

    def __init__(
        self,
        path,
        workspace_type=FILE_GDB,
        layers=None,
        domains=None,
        relationships=None,
        feature_datasets=None,
        is_versioned=False,
    ):
        self.path = path
        self.workspace_type = workspace_type
        self.layers = list(layers or [])
        #: ``{nombre_dominio: DomainInfo}``
        self.domains = dict(domains or {})
        self.relationships = list(relationships or [])
        self.feature_datasets = list(feature_datasets or [])
        #: solo tiene sentido en geodatabases corporativas
        self.is_versioned = is_versioned

    @property
    def is_enterprise(self):
        return self.workspace_type == self.ENTERPRISE

    def layer(self, name):
        """La capa que se llama asi, venga o no calificada por el esquema.

        En una geodatabase corporativa la misma clase puede llegar como
        ``Barra``, ``SIGELEC.BARRA`` o ``SDE.BARRA`` segun con que usuario se
        conecte; buscar solo por el nombre literal dejaria el paquete sin
        sincronizar por un detalle del servidor.
        """
        match = _find_class(self.layer_names(), name)
        if match is None:
            return None
        for layer in self.layers:
            if layer.name == match:
                return layer
        return None

    def layer_names(self):
        return [layer.name for layer in self.layers]

    def relationships_of(self, layer_name):
        return [
            relationship
            for relationship in self.relationships
            if same_class(relationship.origin, layer_name)
            or same_class(relationship.destination, layer_name)
        ]

    def __repr__(self):  # pragma: no cover
        return "<WorkspaceInfo %s (%d capas, %d dominios)>" % (
            self.path,
            len(self.layers),
            len(self.domains),
        )
