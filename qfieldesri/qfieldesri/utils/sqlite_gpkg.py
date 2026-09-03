# -*- coding: utf-8 -*-
"""Funciones ``ST_*`` minimas para editar un GeoPackage con ``sqlite3`` puro.

Los disparadores del indice espacial que exige la especificacion GeoPackage
llaman a ``ST_MinX``, ``ST_MaxX``, ``ST_MinY``, ``ST_MaxY`` y ``ST_IsEmpty``.
QGIS, QField y GDAL las registran al abrir el contenedor; una conexion pelada
de ``sqlite3`` no, y cualquier ``INSERT``/``UPDATE`` sobre una capa con indice
espacial falla con *no such function: ST_IsEmpty*.

Registrando estas cinco funciones, un script en Python estandar puede editar un
paquete de qfieldESRI (o simplemente probarlo) sin instalar nada. La envolvente
se lee de la cabecera del blob y, si el blob no la trae, se calcula recorriendo
el WKB.
"""

import struct

from . import wkb as wkb_utils


def _envelope(blob):
    """``(minx, maxx, miny, maxy)`` de un blob de GeoPackage, o ``None``."""
    if blob is None:
        return None
    data = bytes(blob)
    if len(data) < 8 or data[:2] != b"GP":
        return None
    flags = data[3] if isinstance(data[3], int) else ord(data[3])
    endian = "<" if flags & 0x01 else ">"
    indicator = (flags >> 1) & 0x07
    if flags & 0x10:  # geometria vacia
        return None
    sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    if indicator in (1, 2, 3, 4):
        values = struct.unpack(endian + "4d", data[8:40])
        return values
    offset = 8 + sizes.get(indicator, 0)
    try:
        info = wkb_utils.analyze(data[offset:])
    except wkb_utils.WkbError:
        return None
    if info.bbox is None:
        return None
    min_x, min_y, max_x, max_y = info.bbox
    return (min_x, max_x, min_y, max_y)


def _make_accessor(index):
    def accessor(blob):
        envelope = _envelope(blob)
        return None if envelope is None else envelope[index]

    return accessor


def _is_empty(blob):
    return 1 if _envelope(blob) is None else 0


def register_gpkg_functions(connection):
    """Registra las funciones ``ST_*`` en una conexion ``sqlite3``."""
    connection.create_function("ST_MinX", 1, _make_accessor(0))
    connection.create_function("ST_MaxX", 1, _make_accessor(1))
    connection.create_function("ST_MinY", 1, _make_accessor(2))
    connection.create_function("ST_MaxY", 1, _make_accessor(3))
    connection.create_function("ST_IsEmpty", 1, _is_empty)
    return connection


def connect(path):
    """Abre un GeoPackage listo para editarse con ``sqlite3`` puro."""
    import sqlite3

    connection = sqlite3.connect(path)
    return register_gpkg_functions(connection)
