# -*- coding: utf-8 -*-
"""Lector en memoria: pruebas, demostraciones y validacion sin ArcGIS.

No sustituye a ``arcpy``; existe para que todo el camino
(describir -> empaquetar -> proyecto QField -> sincronizar de vuelta) se pueda
ejecutar y probar en un equipo sin ArcGIS instalado, incluida la integracion
continua. El generador de la demostracion (``qfieldesri.demo``) lo usa para
construir un fragmento del modelo electrico CNEL EP.
"""

import re

from .base import GeodatabaseReader

#: Solo se entiende la forma ``CAMPO = valor``, que es la unica que genera
#: qfieldESRI internamente (busqueda de un registro por su clave).
_SIMPLE_WHERE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s*=\s*(.+?)\s*$")


def _matches(where_clause, attributes):
    if not where_clause:
        return True
    match = _SIMPLE_WHERE.match(where_clause)
    if not match:
        return True
    field, raw = match.group(1).split(".")[-1], match.group(2).strip()
    if raw.startswith("'") and raw.endswith("'"):
        expected = raw[1:-1].replace("''", "'")
    else:
        try:
            expected = float(raw)
        except ValueError:
            expected = raw
    value = attributes.get(field)
    if isinstance(expected, float):
        try:
            return float(value) == expected
        except (TypeError, ValueError):
            return False
    return str(value) == str(expected)


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
        return len(self.data.get(layer_info.name, []))

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
