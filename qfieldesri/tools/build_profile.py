# -*- coding: utf-8 -*-
"""Genera ``qfieldesri/profiles/cnel_ep.json`` a partir de ``docs/modelo/*.md``.

El catalogo del modelo electrico de CNEL EP (extraido del reporte de ArcGIS
Diagrammer y cruzado con el manual MN-TEC-OPE-100) es la fuente de la
*curaduria* que qfieldESRI necesita y que la geodatabase no contiene: que campo
es obligatorio segun el manual, cual es de conectividad y cual es de auditoria.
Los dominios, subtipos y relaciones si se leen en caliente de la geodatabase.

Ejecutar tras actualizar la documentacion del modelo::

    python tools/build_profile.py

Se ejecuta a mano, no en cada empaquetado: el resultado se versiona.
"""

from __future__ import unicode_literals

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs", "modelo")
OUTPUT = os.path.join(ROOT, "qfieldesri", "profiles", "cnel_ep.json")

CLASS_FILES = (
    "03_Clases_Redes_y_Soporte.md",
    "04_Clases_Proteccion_y_Potencia.md",
    "05_Clases_Generacion_Subestaciones_Fuentes.md",
    "06_Clases_Consumidores_y_Alumbrado.md",
)

#: Grupo del arbol de capas de QField segun el archivo de origen.
FILE_GROUPS = {
    "03_Clases_Redes_y_Soporte.md": "Redes y soporte",
    "04_Clases_Proteccion_y_Potencia.md": "Proteccion y potencia",
    "05_Clases_Generacion_Subestaciones_Fuentes.md": "Generacion y subestaciones",
    "06_Clases_Consumidores_y_Alumbrado.md": "Consumidores y alumbrado",
}

CATEGORY_MARKERS = (
    ("CORE", "core"),
    ("Conectividad", "conectividad"),
    ("Sistema", "sistema"),
    ("Otro", "otro"),
)

CLASS_HEADER = re.compile(r"^##\s+`([^`]+)`(?:\s+—\s+(.+))?\s*$")
SECTION_HEADER = re.compile(r"^###\s+(.+?)\s*$")
SUBTYPE_HEADER = re.compile(r"^\*\*(.+?)\*\*\s*$")
SUBTYPE_CODE = re.compile(r"^(.*?)\s*\(Subtipo=(-?\d+)\)(\s*\[Default\])?$")
ROW = re.compile(r"^\|(.+)\|\s*$")


def cells(line):
    return [cell.strip() for cell in ROW.match(line).group(1).split("|")]


def unquote(value):
    return value.strip().strip("`").strip()


def category_of(cell):
    for marker, category in CATEGORY_MARKERS:
        if marker.lower() in cell.lower():
            return category
    return "otro"


def parse_class_file(path, filename):  # noqa: PLR0912
    """Devuelve ``{nombre_clase: definicion}`` de un archivo de clases."""
    with io.open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    classes = {}
    current = None
    section = None
    subtype_label = None
    header_table = False

    for line in lines:
        match = CLASS_HEADER.match(line)
        if match:
            name = match.group(1)
            current = {
                "alias": (match.group(2) or name).strip(),
                "dataset_type": "FeatureClass",
                "geometry": None,
                "network_role": None,
                "group": FILE_GROUPS[filename],
                "kind": kind_of(name),
                "fields": {},
                "field_order": [],
                "aliases": {},
                "subtypes": {},
                "subtype_field": None,
                "defaults": {},
                "domains": {},
            }
            classes[name] = current
            section = "header"
            header_table = True
            subtype_label = None
            continue

        if current is None:
            continue

        match = SECTION_HEADER.match(line)
        if match:
            title = match.group(1).lower()
            if title.startswith("campos"):
                section = "fields"
            elif "subtipo" in title:
                section = "subtypes"
            else:
                section = None
            header_table = False
            subtype_label = None
            continue

        match = SUBTYPE_HEADER.match(line)
        if match and section == "subtypes":
            subtype_label = match.group(1).strip()
            continue

        if not ROW.match(line):
            continue
        row = cells(line)
        if not row or set("".join(row)) <= set("-: "):
            continue

        if section == "header" and header_table and len(row) >= 2:
            _read_header_row(current, row)
        elif section == "fields" and len(row) >= 6:
            _read_field_row(current, row)
        elif section == "subtypes" and subtype_label and len(row) >= 3:
            _read_subtype_row(current, subtype_label, row)

    for definition in classes.values():
        definition.pop("field_order", None)
    return classes


def _read_header_row(definition, row):
    label = row[0].replace("*", "").strip().lower()
    value = row[1].strip()
    if label.startswith("tipo de dataset"):
        definition["dataset_type"] = value
    elif label.startswith("geometr"):
        definition["geometry"] = value.split("(")[0].strip() or None
    elif label.startswith("red geom"):
        lowered = value.lower()
        if "edge" in lowered:
            definition["network_role"] = "edge"
        elif "junction" in lowered:
            definition["network_role"] = "junction"


def _read_field_row(definition, row):
    field_name = unquote(row[0])
    if not field_name or field_name.lower().startswith("campo"):
        return
    definition["fields"][field_name] = category_of(row[5])
    definition["field_order"].append(field_name)
    alias = row[1].strip()
    if alias and alias != field_name:
        definition["aliases"][field_name] = alias


def _read_subtype_row(definition, subtype_label, row):
    field_name = unquote(row[0])
    if not field_name or field_name.lower() == "campo":
        return
    default_value = row[1].strip()
    domain = row[2].strip()
    domain_name = None
    match = re.match(r"^\[([^\]]+)\]", domain)
    if match:
        domain_name = match.group(1).strip()

    if subtype_label == "ObjectClass":
        if default_value:
            definition["defaults"][field_name] = default_value
        if domain_name:
            definition["domains"][field_name] = domain_name
        return

    match = SUBTYPE_CODE.match(subtype_label)
    if not match:
        return
    name, code = match.group(1).strip(), match.group(2)
    entry = definition["subtypes"].setdefault(
        code, {"name": name, "is_default": bool(match.group(3)), "domains": {}}
    )
    if domain_name:
        entry["domains"][field_name] = domain_name
    if field_name.upper() == "SUBTIPO":
        definition["subtype_field"] = field_name


def kind_of(name):
    """Distingue el par Puesto/Unidad descrito en el manual."""
    upper = name.upper()
    if upper.startswith("PUESTO") or upper.startswith("PUNTO"):
        return "puesto"
    if upper.startswith("UNIDAD") or upper in (
        "ESTRUCTURAENPOSTE",
        "CONEXIONCONSUMIDOR",
        "INSTITUCIONENPOSTE",
        "OPERADORAENPOSTE",
        "ATRIBUTOSCONSUMIDOR",
    ):
        return "unidad"
    if upper.startswith("TRAMO") or upper == "BARRA":
        return "tramo"
    if upper.startswith("CATALOGO") or upper in ("SERVICIOCALLES", "DATOSOPERADORA"):
        return "catalogo"
    return None


def parse_relationships(path):
    """Lee la tabla de indice de ``02_Relaciones.md``."""
    with io.open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    relationships = []
    link = re.compile(r"\[`?([^`\]]+)`?\]")
    in_index = False
    for line in lines:
        if line.startswith("| # | Relación"):
            in_index = True
            continue
        if in_index:
            if not ROW.match(line):
                if relationships:
                    break
                continue
            row = cells(line)
            if len(row) < 7 or set("".join(row)) <= set("-: "):
                continue
            if not row[0].isdigit():
                continue
            name = link.search(row[1])
            origin = link.search(row[2])
            destination = link.search(row[3])
            if not (name and origin and destination):
                continue
            relationships.append(
                {
                    "name": name.group(1),
                    "origin": origin.group(1),
                    "destination": destination.group(1),
                    "cardinality": row[4].replace(" ", ""),
                    "composite": row[5].strip().lower() == "yes",
                }
            )
    return relationships


#: Conjuntos tematicos del modelo electrico. Cada uno responde a un trabajo de
#: campo concreto; lo que cuelga de estas clases se arrastra por relacion.
CLASS_SETS = [
    {
        "id": "clientes",
        "name": "Clientes y consumo",
        "description": "Puntos de carga y lo que se registra de cada consumidor.",
        "classes": ["PuntoCarga", "CONEXIONCONSUMIDOR", "ATRIBUTOSCONSUMIDOR"],
    },
    {
        "id": "postes",
        "name": "Postes y lo que llevan encima",
        "description": (
            "Estructuras de soporte con sus estructuras montadas, "
            "instituciones, operadoras y tensores."
        ),
        "classes": [
            "EstructuraSoporte",
            "EstructuraANivel",
            "ESTRUCTURAENPOSTE",
            "INSTITUCIONENPOSTE",
            "OPERADORAENPOSTE",
            "Tensor",
            "CATALOGOESTRUCTURA",
        ],
    },
    {
        "id": "alumbrado",
        "name": "Alumbrado publico",
        "description": "Luminarias y semaforos.",
        "classes": ["Luminaria", "UNIDADLUMINARIA", "Semaforo"],
    },
    {
        "id": "transformadores",
        "name": "Transformadores",
        "description": "Puestos de transformacion de distribucion y de potencia.",
        "classes": [
            "PuestoTransfDistribucion",
            "UNIDADTRANSFDISTRIBUCION",
            "PuestoTransfPotencia",
            "UNIDADTRANSFPOTENCIA",
        ],
    },
    {
        "id": "protecciones",
        "name": "Protecciones y maniobra",
        "description": (
            "Seccionadores, fusibles, proteccion dinamica, reguladores, "
            "capacitores, pararrayos y puntos de apertura."
        ),
        "classes": [
            "PuestoSeccionador",
            "PuestoSeccionadorFusible",
            "PuestoProteccionDinamico",
            "PuestoProteccionBajaTension",
            "PuestoReguladorTension",
            "PuestoCorrectorFactorPotencia",
            "Pararrayos",
            "PuntoApertura",
        ],
    },
    {
        "id": "red_mt",
        "name": "Red de media tension",
        "description": "Tramos de distribucion, aereos y subterraneos, y su maniobra.",
        "classes": [
            "TramoDistribucionAereo",
            "TramoDistribucionSubterraneo",
            "PuntoApertura",
            "PuestoSeccionador",
            "PuestoSeccionadorFusible",
            "PuestoProteccionDinamico",
        ],
    },
    {
        "id": "red_bt",
        "name": "Red de baja tension",
        "description": "Tramos de baja tension, su proteccion y los puntos de carga.",
        "classes": [
            "TramoBajaTensionAereo",
            "TramoBajaTensionSubterraneo",
            "PuestoProteccionBajaTension",
            "PuntoCarga",
        ],
    },
    {
        "id": "subtransmision",
        "name": "Subtransmision",
        "classes": [
            "TramoSubtransmisionAereo",
            "TramoSubtransmisionSubterraneo",
        ],
    },
    {
        "id": "subestaciones",
        "name": "Subestaciones y generacion",
        "description": "Subestaciones, barras, transformadores de potencia y generadores.",
        "classes": [
            "Subestacion",
            "Barra",
            "PuestoTransfPotencia",
            "CIRCUITOFUENTE",
            "Generador",
            "GeneradorDistribuido",
            "MOTORSINCRONO",
            "MOTORINDUCCION",
        ],
    },
    {
        "id": "red_electrica",
        "name": "Toda la red electrica",
        "description": (
            "Los tramos de todos los niveles de tension con sus puestos, "
            "sin catalogos ni tablas auxiliares."
        ),
        "classes": [
            "TramoSubtransmisionAereo",
            "TramoSubtransmisionSubterraneo",
            "TramoDistribucionAereo",
            "TramoDistribucionSubterraneo",
            "TramoBajaTensionAereo",
            "TramoBajaTensionSubterraneo",
            "Barra",
            "EstructuraSoporte",
            "PuestoTransfDistribucion",
            "PuestoTransfPotencia",
            "PuestoSeccionador",
            "PuestoSeccionadorFusible",
            "PuestoProteccionDinamico",
            "PuestoProteccionBajaTension",
            "PuestoReguladorTension",
            "PuestoCorrectorFactorPotencia",
            "PuntoApertura",
            "Subestacion",
        ],
    },
]


def build():
    classes = {}
    for filename in CLASS_FILES:
        classes.update(parse_class_file(os.path.join(DOCS, filename), filename))

    relationships = parse_relationships(os.path.join(DOCS, "02_Relaciones.md"))

    network = {"name": "Electrico_RedGeom", "edges": [], "junctions": []}
    for name, definition in sorted(classes.items()):
        if definition["network_role"] == "edge":
            network["edges"].append(name)
        elif definition["network_role"] == "junction":
            network["junctions"].append(name)

    profile = {
        "id": "cnel_ep",
        "name": "Modelo de datos electrico CNEL EP (MN-TEC-OPE-100)",
        "description": (
            "Perfil derivado del catalogo del modelo electrico homologado de "
            "CNEL EP (reporte de ArcGIS Diagrammer de la Unidad de Negocio "
            "Guayaquil, corte 2025-05-12, cruzado con el manual "
            "MN-TEC-OPE-100 v01). Aporta la categoria de cada campo y la "
            "estructura Puesto/Unidad; los dominios, subtipos y relaciones se "
            "leen en caliente de la geodatabase de cada Unidad de Negocio."
        ),
        "crs": 32717,
        "feature_datasets": ["Electrico", "Electrico_Complementos"],
        "network": network,
        "connectivity_fields": [
            "ANCILLARYROLE",
            "ELECTRICTRACEWEIGHT",
            "ENABLED",
            "CIRCUITSOURCEGUID",
            "PARENTCIRCUITSOURCEGUID",
        ],
        "source_classes": [
            "PuestoProteccionDinamico",
            "PuestoTransfDistribucion",
            "PuestoTransfPotencia",
        ],
        "sink_classes": ["PuntoCarga", "Luminaria"],
        "variable_domains": ["Codigo Alimentador", "Numero Estacion", "Subestacion"],
        # --- ambitos de exportacion -----------------------------------
        # Campos con los que se acota la exportacion, en orden de
        # preferencia: se usa el primero que exista en cada clase. Los
        # recuentos son sobre las 47 clases del catalogo.
        "scope_fields": {
            "alimentador": [
                "ALIMENTADORID",
                "ALIMENTADOR",
                "ALIMENTADORID2",
                "CODIGOALIMENTADOR",
            ],
            "subestacion": ["IDSUBESTACION", "NUMEROSUBESTACION"],
            "provincia": ["PROVINCIA"],
            "canton": ["CANTON"],
            "parroquia": ["PARROQUIA"],
        },
        # Dominio del que se leen los valores elegibles de cada ambito. Los
        # valores NO se guardan aqui: cambian por Unidad de Negocio y se leen
        # de la geodatabase activa.
        "scope_domains": {
            "alimentador": "Codigo Alimentador",
            "subestacion": "Subestacion",
            "provincia": "Provincias",
            "canton": "Cantones",
            "parroquia": "Parroquias",
        },
        # La subestacion no es un campo de las clases de red: se traduce a sus
        # alimentadores con la tabla de alimentador cabecera.
        "scope_indirect": {
            "subestacion": {
                "table": "CIRCUITOFUENTE",
                "key_field": "IDSUBESTACION",
                "value_field": "CODIGOALIMENTADOR",
                "target_kind": "alimentador",
            }
        },
        # --- conjuntos tematicos --------------------------------------
        # Que clases se lleva cada tipo de trabajo de campo. No se deducen de
        # la geodatabase: saber que CONEXIONCONSUMIDOR es cosa de clientes y
        # no de la red es conocimiento del modelo. Las clases que dependen de
        # las listadas se arrastran solas por las relationship classes, asi
        # que aqui va la cabecera de cada tema, no el inventario completo.
        "class_sets": CLASS_SETS,
        "classes": classes,
        "relationships": relationships,
    }

    with io.open(OUTPUT, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(profile, indent=1, sort_keys=True, ensure_ascii=False))
        handle.write("\n")
    return profile


if __name__ == "__main__":
    result = build()
    sys.stdout.write(
        "Perfil escrito en %s: %d clases, %d relaciones\n"
        % (OUTPUT, len(result["classes"]), len(result["relationships"]))
    )
