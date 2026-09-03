# -*- coding: utf-8 -*-
"""Geodatabase de demostracion en memoria.

Reproduce un fragmento representativo del modelo electrico de CNEL EP —un
tramo de media tension, su poste, el puesto de transformacion y la unidad
relacionada— con dominios, subtipos y una relationship class. Sirve para:

* probar qfieldESRI de punta a punta sin ArcGIS instalado;
* que quien vaya a usar el complemento vea el resultado antes de conectar la
  geodatabase real;
* alimentar las pruebas automatizadas.
"""

import struct

from .core.model import (
    DomainInfo,
    FieldInfo,
    LayerInfo,
    RelationshipInfo,
    SpatialReferenceInfo,
    SubtypeInfo,
    WorkspaceInfo,
)
from .readers.memory import MemoryReader

#: UTM 17S / WGS84, el sistema del feature dataset ``Electrico``.
UTM17S = SpatialReferenceInfo(
    code=32717,
    name="WGS 84 / UTM zone 17S",
    wkt=(
        'PROJCS["WGS_1984_UTM_Zone_17S",GEOGCS["GCS_WGS_1984",'
        'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],'
        'PARAMETER["False_Northing",10000000.0],'
        'PARAMETER["Central_Meridian",-81.0],PARAMETER["Scale_Factor",0.9996],'
        'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
    ),
)


def _system_fields():
    return [
        FieldInfo("OBJECTID", "OID", "Object ID", nullable=False, editable=False),
        FieldInfo(
            "GLOBALID", "GlobalID", "GLOBALID", 38, nullable=False, editable=False
        ),
        FieldInfo("USUARIOREGISTRO", "String", "Usu Cre", 50),
        FieldInfo("FECHAREGISTRO", "Date", "F Cre Sis"),
        FieldInfo(
            "PROVINCIA",
            "String",
            "Provincia",
            2,
            domain="Provincias",
            default_value="09",
        ),
        FieldInfo(
            "CANTON", "String", "Canton", 4, domain="Cantones", default_value="0901"
        ),
        FieldInfo("OBSERVACIONES", "String", "Observaciones", 255),
    ]


def _feeder_field():
    """Campo de alimentador, presente en casi todas las clases de red."""
    return FieldInfo(
        "ALIMENTADORID", "String", "Alim1", 10, domain="Codigo Alimentador"
    )


def build_workspace():
    """Devuelve el :class:`WorkspaceInfo` de la demostracion."""
    domains = {
        "Provincias": DomainInfo(
            "Provincias",
            coded_values=[("09", "GUAYAS"), ("13", "MANABI"), ("17", "PICHINCHA")],
        ),
        "Cantones": DomainInfo(
            "Cantones",
            coded_values=[("0901", "GUAYAQUIL"), ("0906", "DAULE")],
        ),
        "Fase Conexion Trifasica": DomainInfo(
            "Fase Conexion Trifasica",
            field_type="Integer",
            coded_values=[(1, "A"), (2, "B"), (4, "C"), (7, "ABC")],
        ),
        "Voltaje MT": DomainInfo(
            "Voltaje MT",
            field_type="Integer",
            coded_values=[(1, "13,8 kV"), (2, "7,96 kV"), (3, "22 kV")],
        ),
        "Voltaje BT": DomainInfo(
            "Voltaje BT",
            field_type="Integer",
            coded_values=[(10, "120/240 V"), (11, "127/220 V")],
        ),
        "Altura Poste": DomainInfo(
            "Altura Poste",
            DomainInfo.RANGE,
            field_type="Double",
            range_min=6.0,
            range_max=20.0,
        ),
        # Dominios de ambito: los codigos de la red fisica de la Unidad de
        # Negocio. Se leen siempre de la geodatabase, nunca de una lista fija.
        "Codigo Alimentador": DomainInfo(
            "Codigo Alimentador",
            coded_values=[
                ("04BH070T11", "S/E BELO HORIZONTE - PORTAL AL SOL"),
                ("04SM320T22", "S/E SAMANES - LOS ALAMOS"),
                ("04OR240T22", "S/E ORQUIDEAS - LIMONCOCHA"),
            ],
        ),
        "Subestacion": DomainInfo(
            "Subestacion",
            coded_values=[
                ("04BH07", "S/E BELO HORIZONTE"),
                ("04SM32", "S/E SAMANES"),
                ("04OR24", "S/E ORQUIDEAS"),
            ],
        ),
        # Dominio grande: se publica como tabla de catalogo + ValueRelation.
        "Catalogo Conductores": DomainInfo(
            "Catalogo Conductores",
            coded_values=[
                ("C%03d" % index, "CONDUCTOR ACSR #%d" % index)
                for index in range(1, 61)
            ],
        ),
    }

    poste = LayerInfo(
        name="EstructuraSoporte",
        path="Electrico/EstructuraSoporte",
        alias="Poste",
        geometry_type="Point",
        spatial_reference=UTM17S,
        feature_dataset="Electrico",
        globalid_field="GLOBALID",
        fields=_system_fields()
        + [
            _feeder_field(),
            FieldInfo(
                "CODIGOESTRUCTURA", "String", "Codigo Estructura", 20, nullable=False
            ),
            FieldInfo("ALTURA", "Double", "Altura (m)", domain="Altura Poste"),
            FieldInfo("MATERIAL", "String", "Material", 20),
            FieldInfo("FOTO", "String", "Fotografia", 255),
        ],
    )

    tramo = LayerInfo(
        name="TramoDistribucionAereo",
        path="Electrico/TramoDistribucionAereo",
        alias="Tramo MT Aereo",
        geometry_type="Polyline",
        spatial_reference=UTM17S,
        feature_dataset="Electrico",
        globalid_field="GLOBALID",
        subtype_field="SUBTIPO",
        fields=_system_fields()
        + [
            _feeder_field(),
            FieldInfo("SUBTIPO", "Integer", "Subtipo", default_value=1),
            FieldInfo(
                "FASECONEXION", "Integer", "Fase", domain="Fase Conexion Trifasica"
            ),
            FieldInfo("VOLTAJE", "Integer", "Voltaje", domain="Voltaje MT"),
            FieldInfo(
                "CODIGOCONDUCTORFASE",
                "String",
                "Conductor Fase",
                10,
                domain="Catalogo Conductores",
            ),
            FieldInfo("ANCILLARYROLE", "SmallInteger", "Ancillary Role"),
            FieldInfo(
                "PARENTCIRCUITSOURCEGUID",
                "GUID",
                "ParentCircuitSourceGUID",
                38,
                editable=False,
            ),
        ],
        subtypes=[
            SubtypeInfo(
                1,
                "Tramo MTA Trifasico",
                is_default=True,
                domains={"VOLTAJE": "Voltaje MT"},
                defaults={"FASECONEXION": 7},
            ),
            SubtypeInfo(
                2,
                "Tramo MTA Monofasico",
                domains={"VOLTAJE": "Voltaje BT"},
                defaults={"FASECONEXION": 1},
            ),
        ],
    )

    puesto = LayerInfo(
        name="PuestoTransfDistribucion",
        path="Electrico/PuestoTransfDistribucion",
        alias="Puesto TransfDistribucion",
        geometry_type="Point",
        spatial_reference=UTM17S,
        feature_dataset="Electrico",
        globalid_field="GLOBALID",
        fields=_system_fields()
        + [
            _feeder_field(),
            FieldInfo("CODIGOPUESTO", "String", "Codigo Puesto", 20, nullable=False),
            FieldInfo("POTENCIATOTAL", "Double", "Potencia Total (kVA)"),
            FieldInfo(
                "CIRCUITSOURCEGUID", "GUID", "CircuitSourceGUID", 38, editable=False
            ),
        ],
    )

    unidad = LayerInfo(
        name="UNIDADTRANSFDISTRIBUCION",
        dataset_type=LayerInfo.TABLE,
        path="UNIDADTRANSFDISTRIBUCION",
        alias="Transformador",
        globalid_field="GLOBALID",
        fields=_system_fields()
        + [
            FieldInfo("PUESTOTRANSFDISTGLOBALID", "GUID", "Puesto", 38),
            FieldInfo("NUMEROSERIE", "String", "Numero de Serie", 30),
            FieldInfo("POTENCIA", "Double", "Potencia (kVA)"),
            FieldInfo("FASE", "Integer", "Fase", domain="Fase Conexion Trifasica"),
        ],
    )

    circuito = LayerInfo(
        name="CIRCUITOFUENTE",
        dataset_type=LayerInfo.TABLE,
        path="CIRCUITOFUENTE",
        alias="Alimentador Cabecera",
        globalid_field="GLOBALID",
        fields=_system_fields()
        + [
            FieldInfo(
                "CODIGOALIMENTADOR",
                "String",
                "Codigo Alimentador",
                10,
                domain="Codigo Alimentador",
            ),
            FieldInfo(
                "IDSUBESTACION", "String", "Subestacion", 10, domain="Subestacion"
            ),
            FieldInfo("VOLTAJENOMINAL", "Double", "Voltaje Nominal"),
        ],
    )

    relationships = [
        RelationshipInfo(
            name="PuestoTransfDist_UnidadTransfDist",
            origin="PuestoTransfDistribucion",
            destination="UNIDADTRANSFDISTRIBUCION",
            origin_key="GLOBALID",
            destination_key="PUESTOTRANSFDISTGLOBALID",
            forward_label="Transformadores",
            backward_label="Puesto",
        )
    ]

    return WorkspaceInfo(
        path="demo.gdb",
        workspace_type=WorkspaceInfo.FILE_GDB,
        layers=[poste, tramo, puesto, unidad, circuito],
        domains=domains,
        relationships=relationships,
        feature_datasets=["Electrico"],
    )


#: Alimentadores de la demostracion, en el orden del dominio.
FEEDERS = ("04BH070T11", "04SM320T22", "04OR240T22")

#: Subestacion de la que cuelga cada alimentador (lo que guarda CIRCUITOFUENTE).
FEEDER_SUBSTATION = {
    "04BH070T11": "04BH07",
    "04SM320T22": "04SM32",
    "04OR240T22": "04OR24",
}


def _point(x, y):
    return struct.pack("<BI", 1, 1) + struct.pack("<dd", x, y)


def _line(coordinates):
    data = struct.pack("<BII", 1, 2, len(coordinates))
    for x, y in coordinates:
        data += struct.pack("<dd", x, y)
    return data


def build_reader():
    """Lector en memoria con datos de ejemplo cargados."""
    workspace = build_workspace()
    reader = MemoryReader(workspace)

    base_x, base_y = 620000.0, 9755000.0
    postes = []
    for index in range(6):
        postes.append(
            (
                _point(base_x + index * 40, base_y),
                {
                    "OBJECTID": index + 1,
                    "GLOBALID": "{P%08d-0000-0000-0000-000000000000}" % index,
                    "ALIMENTADORID": FEEDERS[index % len(FEEDERS)],
                    "CODIGOESTRUCTURA": "GYE-P-%04d" % (index + 1),
                    "ALTURA": 9.0 + index % 3,
                    "MATERIAL": "HORMIGON",
                    "PROVINCIA": "09",
                    "CANTON": "0901",
                    "USUARIOREGISTRO": "demo",
                    "FECHAREGISTRO": None,
                    "OBSERVACIONES": None,
                    "FOTO": None,
                },
            )
        )
    reader.set_features("EstructuraSoporte", postes)

    reader.set_features(
        "TramoDistribucionAereo",
        [
            (
                _line([(base_x, base_y), (base_x + 200, base_y)]),
                {
                    "OBJECTID": 1,
                    "GLOBALID": "{T0000001-0000-0000-0000-000000000000}",
                    "ALIMENTADORID": FEEDERS[0],
                    "SUBTIPO": 1,
                    "FASECONEXION": 7,
                    "VOLTAJE": 1,
                    "CODIGOCONDUCTORFASE": "C012",
                    "ANCILLARYROLE": 0,
                    "PARENTCIRCUITSOURCEGUID": None,
                    "PROVINCIA": "09",
                    "CANTON": "0901",
                    "USUARIOREGISTRO": "demo",
                    "FECHAREGISTRO": None,
                    "OBSERVACIONES": None,
                },
            )
        ],
    )

    reader.set_features(
        "PuestoTransfDistribucion",
        [
            (
                _point(base_x + 120, base_y + 5),
                {
                    "OBJECTID": 1,
                    "GLOBALID": "{U0000001-0000-0000-0000-000000000000}",
                    "ALIMENTADORID": FEEDERS[0],
                    "CODIGOPUESTO": "GYE-PT-0001",
                    "POTENCIATOTAL": 75.0,
                    "CIRCUITSOURCEGUID": None,
                    "PROVINCIA": "09",
                    "CANTON": "0901",
                    "USUARIOREGISTRO": "demo",
                    "FECHAREGISTRO": None,
                    "OBSERVACIONES": None,
                },
            )
        ],
    )

    reader.set_features(
        "UNIDADTRANSFDISTRIBUCION",
        [
            (
                None,
                {
                    "OBJECTID": index + 1,
                    "GLOBALID": "{V000000%d-0000-0000-0000-000000000000}" % index,
                    "PUESTOTRANSFDISTGLOBALID": "{U0000001-0000-0000-0000-000000000000}",
                    "NUMEROSERIE": "SN-%05d" % (index + 1),
                    "POTENCIA": 25.0,
                    "FASE": (1, 2, 4)[index],
                    "PROVINCIA": "09",
                    "CANTON": "0901",
                    "USUARIOREGISTRO": "demo",
                    "FECHAREGISTRO": None,
                    "OBSERVACIONES": None,
                },
            )
            for index in range(3)
        ],
    )

    reader.set_features(
        "CIRCUITOFUENTE",
        [
            (
                None,
                {
                    "OBJECTID": index + 1,
                    "GLOBALID": "{C000000%d-0000-0000-0000-000000000000}" % index,
                    "CODIGOALIMENTADOR": feeder,
                    "IDSUBESTACION": FEEDER_SUBSTATION[feeder],
                    "VOLTAJENOMINAL": 13.8,
                    "PROVINCIA": "09",
                    "CANTON": "0901",
                    "USUARIOREGISTRO": "demo",
                    "FECHAREGISTRO": None,
                    "OBSERVACIONES": None,
                },
            )
            for index, feeder in enumerate(FEEDERS)
        ],
    )
    return reader


# ----------------------------------------------------------------------
# La misma geodatabase, vista desde una corporativa de Oracle con ArcSDE
# ----------------------------------------------------------------------
#: Propietario del esquema con el que Oracle califica las clases. En una
#: geodatabase corporativa la conexion determina como se llama cada clase:
#: ``SIGELEC.POSTE`` con un usuario, ``SDE.POSTE`` con otro. Es un ejemplo,
#: no una constante del programa: el propietario se lee de la geodatabase.
DEFAULT_OWNER = "SIGELEC"


def qualify(name, owner=DEFAULT_OWNER):
    """Nombre de la clase tal como lo devuelve Oracle: calificado y en mayusculas."""
    return "%s.%s" % (owner.upper(), name.upper())


def build_enterprise_reader(owner=DEFAULT_OWNER, versioned=True):
    """La demostracion, pero nombrada como una geodatabase corporativa.

    Es exactamente el mismo modelo y los mismos datos: lo unico que cambia es
    la etiqueta con la que el servidor nombra cada clase. Sirve para
    comprobar, sin ArcGIS ni Oracle delante, que el perfil, el ambito, la
    simbologia y —sobre todo— la sincronizacion de vuelta siguen reconociendo
    las clases cuando llegan calificadas y en mayusculas.
    """
    reader = build_reader()
    workspace = reader.workspace_info

    renamed = {}
    for layer in workspace.layers:
        renamed[layer.name] = qualify(layer.name, owner)

    for layer in workspace.layers:
        original = layer.name
        layer.name = renamed[original]
        if layer.path:
            layer.path = renamed[original]
        if original in reader.data:
            reader.data[layer.name] = reader.data.pop(original)

    for relationship in workspace.relationships:
        relationship.name = qualify(relationship.name, owner)
        relationship.origin = renamed.get(relationship.origin, relationship.origin)
        relationship.destination = renamed.get(
            relationship.destination, relationship.destination
        )

    workspace.path = "Database Connections/SIGELEC_PRODUCCION.sde"
    workspace.workspace_type = WorkspaceInfo.ENTERPRISE
    workspace.is_versioned = versioned
    for layer in workspace.layers:
        layer.is_versioned = versioned
    return reader
