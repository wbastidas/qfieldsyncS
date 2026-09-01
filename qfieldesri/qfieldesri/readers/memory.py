# -*- coding: utf-8 -*-
"""Lector en memoria: pruebas, demostraciones y validacion sin ArcGIS.

No sustituye a ``arcpy``; existe para que todo el camino
(describir -> empaquetar -> proyecto de QField -> sincronizar de vuelta) se
pueda ejecutar y probar en un equipo sin ArcGIS instalado, incluida la
integracion continua. El generador de la demostracion (``qfieldesri.demo``) lo
usa para construir un fragmento del modelo electrico CNEL EP.
"""

import re

from .base import GeodatabaseReader

#: Solo se interpretan las formas que genera qfieldESRI: comparacion simple,
#: pertenencia a una lista y la conjuncion de ambas. Cualquier otra cosa se
#: considera "no filtra", que es el comportamiento seguro para una simulacion.
_EQUALS = re.compile(r"^\s*([A-Za-z0-9_.\"\[\]]+)\s*=\s*(.+?)\s*$")
_IN = re.compile(r"^\s*([A-Za-z0-9_.\"\[\]]+)\s+IN\s*\((.*)\)\s*$", re.IGNORECASE)
_AND = re.compile(r"^\s*\((.+)\)\s+AND\s+\((.+)\)\s*$", re.IGNORECASE | re.DOTALL)


def _clean_field(name):
    return name.strip().strip('"').strip("[]").split(".")[-1]


def _parse_literal(raw):
    raw = raw.strip()
    if raw.upper() == "NULL":
        return None
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    try:
        return float(raw)
    except ValueError:
        return raw


def _same(value, expected):
    if expected is None:
        return value is None
    if isinstance(expected, float):
        try:
            return float(value) == expected
        except (TypeError, ValueError):
            return False
    return str(value) == str(expected)


def _split_values(text):
    """Separa la lista de un ``IN`` respetando las comillas."""
    values = []
    current = ""
    in_quotes = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            if in_quotes and index + 1 < len(text) and text[index + 1] == "'":
                current += "'"
                index += 2
                continue
            in_quotes = not in_quotes
            current += char
        elif char == "," and not in_quotes:
            values.append(current)
            current = ""
        else:
            current += char
        index += 1
    if current.strip():
        values.append(current)
    return [_parse_literal(value) for value in values]


def _matches(where_clause, attributes):
    if not where_clause:
        return True
    clause = where_clause.strip()

    match = _AND.match(clause)
    if match:
        return _matches(match.group(1), attributes) and _matches(
            match.group(2), attributes
        )

    match = _IN.match(clause)
    if match:
        value = attributes.get(_clean_field(match.group(1)))
        return any(_same(value, expected) for expected in _split_values(match.group(2)))

    match = _EQUALS.match(clause)
    if match:
        field = _clean_field(match.group(1))
        expected = _parse_literal(match.group(2))
        if field == "1":
            # '1 = 0' es como se expresa "ningun registro".
            return _same(1.0, expected)
        return _same(attributes.get(field), expected)

    return True


class MemoryReader(GeodatabaseReader):
    name = "memory"
    supports_write = True

    def __init__(self, workspace_info):
        """``workspace_info`` es un ``WorkspaceInfo`` ya construido."""
        GeodatabaseReader.__init__(self, getattr(workspace_info, "path", "memoria"))
        self.workspace_info = workspace_info
        #: ``{nombre_clase: [(wkb, {campo: valor}), ...]}``
        self.data = {}
        self.inserted = []
        self.updated = []
        self.deleted = []
        self._editing = False

    # -- ciclo de vida --------------------------------------------------
    def open(self):
        return self

    # -- metadatos ------------------------------------------------------
    def describe_workspace(self, layer_names=None):
        if not layer_names:
            return self.workspace_info
        wanted = set(name.lower() for name in layer_names)
        clone = self.workspace_info
        clone.layers = [layer for layer in clone.layers if layer.name.lower() in wanted]
        return clone

    # -- datos ----------------------------------------------------------
    def set_features(self, layer_name, features):
        """``features`` es una lista de ``(wkb, {campo: valor})``."""
        self.data[layer_name] = list(features)

    def iter_features(
        self,
        layer_info,
        field_names,
        where_clause=None,
        aoi_wkt=None,
        aoi_crs=None,
        limit=0,
    ):
        count = 0
        for wkb, attributes in self.data.get(layer_info.name, []):
            if not _matches(where_clause, attributes):
                continue
            yield wkb, dict((name, attributes.get(name)) for name in field_names)
            count += 1
            if limit and count >= limit:
                break

    def count_features(self, layer_info, where_clause=None):
        return len(
            [
                row
                for row in self.data.get(layer_info.name, [])
                if _matches(where_clause, row[1])
            ]
        )

    def union_wkt(self, layer_name, where_clause=None):
        """La demostracion no tiene motor geometrico: se declara sin soporte."""
        return None, None

    # -- escritura ------------------------------------------------------
    def start_editing(self):
        self._editing = True

    def stop_editing(self, save=True):
        self._editing = False

    def update_feature(self, layer_info, key_field, key_value, attributes, wkb=None):
        self.updated.append((layer_info.name, key_field, key_value, attributes, wkb))
        return 1

    def insert_feature(self, layer_info, attributes, wkb=None):
        self.inserted.append((layer_info.name, attributes, wkb))
        return len(self.inserted)

    def delete_feature(self, layer_info, key_field, key_value):
        self.deleted.append((layer_info.name, key_field, key_value))
        return 1
