"""Lectura y normalizacion de geometrias WKB.

Este modulo es deliberadamente independiente de arcpy, GDAL y QGIS: solo usa la
biblioteca estandar. Sirve para tres cosas dentro de qfieldESRI:

* normalizar el WKB que entrega ``arcpy.Geometry.WKB`` (que usa la convencion
  "2.5D" de OGC con la bandera ``0x80000000`` para Z) al WKB **ISO** que exige
  la especificacion OGC GeoPackage (tipos con desplazamiento 1000/2000/3000);
* calcular la envolvente de una geometria para poder rellenar
  ``gpkg_contents`` sin depender de una libreria espacial;
* averiguar el tipo de geometria de una capa a partir de un registro real.

Compatible con Python 2.7 (ArcMap) y Python 3.x (ArcGIS Pro / QGIS).
"""

import struct

# Codigos WKB base (OGC SFS 1.1)
WKB_POINT = 1
WKB_LINESTRING = 2
WKB_POLYGON = 3
WKB_MULTIPOINT = 4
WKB_MULTILINESTRING = 5
WKB_MULTIPOLYGON = 6
WKB_GEOMETRYCOLLECTION = 7

# Nombres tal como los espera GeoPackage (`gpkg_geometry_columns.geometry_type_name`)
GEOMETRY_TYPE_NAMES = {
    WKB_POINT: "POINT",
    WKB_LINESTRING: "LINESTRING",
    WKB_POLYGON: "POLYGON",
    WKB_MULTIPOINT: "MULTIPOINT",
    WKB_MULTILINESTRING: "MULTILINESTRING",
    WKB_MULTIPOLYGON: "MULTIPOLYGON",
    WKB_GEOMETRYCOLLECTION: "GEOMETRYCOLLECTION",
}

# Bandera "2.5D" que usan arcpy y las versiones antiguas de PostGIS/OGR.
_WKB_Z_FLAG = 0x80000000
_WKB_M_FLAG = 0x40000000
# Bandera de SRID embebido (EWKB de PostGIS). arcpy no la usa, pero es barato
# tolerarla porque permite reutilizar este modulo con otras fuentes.
_WKB_SRID_FLAG = 0x20000000


class WkbError(ValueError):
    """El buffer recibido no es un WKB que este modulo sepa interpretar."""


def _fmt(byte_order):
    return "<" if byte_order == 1 else ">"


def decode_type(raw_type):
    """Descompone un codigo de tipo WKB en ``(base, has_z, has_m, has_srid)``.

    Acepta tanto la convencion ISO (1001 = PointZ) como la convencion de
    banderas de bits (0x80000001 = PointZ) que produce ``arcpy``.
    """
    has_srid = bool(raw_type & _WKB_SRID_FLAG)
    has_z = bool(raw_type & _WKB_Z_FLAG)
    has_m = bool(raw_type & _WKB_M_FLAG)
    base = raw_type & 0x0FFFFFFF

    if base >= 3000:
        base -= 3000
        has_z = True
        has_m = True
    elif base >= 2000:
        base -= 2000
        has_m = True
    elif base >= 1000:
        base -= 1000
        has_z = True

    if base not in GEOMETRY_TYPE_NAMES:
        raise WkbError("Tipo de geometria WKB no soportado: %r" % (raw_type,))

    return base, has_z, has_m, has_srid


def encode_iso_type(base, has_z, has_m):
    """Codigo de tipo en la forma ISO que exige GeoPackage."""
    if has_z and has_m:
        return base + 3000
    if has_m:
        return base + 2000
    if has_z:
        return base + 1000
    return base


def _coord_size(has_z, has_m):
    return 2 + (1 if has_z else 0) + (1 if has_m else 0)


class _Cursor(object):
    """Recorrido secuencial del buffer WKB, reescribiendo tipos a ISO."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.out = bytearray()
        self.min_x = self.min_y = None
        self.max_x = self.max_y = None

    # -- lectura --------------------------------------------------------
    def read(self, size):
        chunk = self.data[self.pos : self.pos + size]
        if len(chunk) != size:
            raise WkbError("WKB truncado en el byte %d" % self.pos)
        self.pos += size
        return chunk

    def track(self, x, y):
        # Un punto vacio se codifica con NaN: no debe contaminar la
        # envolvente. `valor != valor` es la comprobacion de NaN que funciona
        # igual en Python 2.7 y 3.x sin importar `math`.
        if x != x or y != y:  # noqa: PLR0124
            return
        if self.min_x is None or x < self.min_x:
            self.min_x = x
        if self.max_x is None or x > self.max_x:
            self.max_x = x
        if self.min_y is None or y < self.min_y:
            self.min_y = y
        if self.max_y is None or y > self.max_y:
            self.max_y = y

    # -- recorrido ------------------------------------------------------
    def geometry(self):
        byte_order = struct.unpack("B", self.read(1))[0]
        if byte_order not in (0, 1):
            raise WkbError("Orden de bytes WKB invalido: %r" % byte_order)
        fmt = _fmt(byte_order)
        (raw_type,) = struct.unpack(fmt + "I", self.read(4))
        base, has_z, has_m, has_srid = decode_type(raw_type)

        if has_srid:
            self.read(4)  # SRID embebido: se descarta, GeoPackage lo lleva aparte

        self.out += struct.pack("B", byte_order)
        self.out += struct.pack(fmt + "I", encode_iso_type(base, has_z, has_m))

        if base == WKB_POINT:
            self._points(fmt, has_z, has_m, 1)
        elif base == WKB_LINESTRING:
            self._linestring(fmt, has_z, has_m)
        elif base == WKB_POLYGON:
            self._polygon(fmt, has_z, has_m)
        else:  # colecciones: cada parte trae su propia cabecera
            (count,) = struct.unpack(fmt + "I", self.read(4))
            self.out += struct.pack(fmt + "I", count)
            for _ in range(count):
                self.geometry()

        return base, has_z, has_m

    def _points(self, fmt, has_z, has_m, count):
        dims = _coord_size(has_z, has_m)
        size = 8 * dims
        for _ in range(count):
            raw = self.read(size)
            coords = struct.unpack(fmt + "d" * dims, raw)
            self.track(coords[0], coords[1])
            self.out += raw

    def _linestring(self, fmt, has_z, has_m):
        (count,) = struct.unpack(fmt + "I", self.read(4))
        self.out += struct.pack(fmt + "I", count)
        self._points(fmt, has_z, has_m, count)

    def _polygon(self, fmt, has_z, has_m):
        (rings,) = struct.unpack(fmt + "I", self.read(4))
        self.out += struct.pack(fmt + "I", rings)
        for _ in range(rings):
            self._linestring(fmt, has_z, has_m)


class WkbInfo(object):
    """Resultado de analizar un WKB."""

    __slots__ = ("bbox", "geometry_type", "has_m", "has_z", "is_empty", "wkb")

    def __init__(self, wkb, geometry_type, has_z, has_m, bbox, is_empty):
        self.wkb = wkb
        self.geometry_type = geometry_type
        self.has_z = has_z
        self.has_m = has_m
        self.bbox = bbox
        self.is_empty = is_empty

    @property
    def geometry_type_name(self):
        return GEOMETRY_TYPE_NAMES[self.geometry_type]

    def __repr__(self):  # pragma: no cover - ayuda de depuracion
        return "<WkbInfo %s z=%s m=%s bbox=%s>" % (
            self.geometry_type_name,
            self.has_z,
            self.has_m,
            self.bbox,
        )


def analyze(data):
    """Normaliza a ISO y devuelve un :class:`WkbInfo`.

    ``data`` puede ser ``bytes``, ``bytearray`` o ``memoryview``.
    """
    if data is None:
        raise WkbError("WKB vacio")
    data = bytes(data)
    cursor = _Cursor(data)
    base, has_z, has_m = cursor.geometry()
    is_empty = cursor.min_x is None
    bbox = (
        None
        if is_empty
        else (
            cursor.min_x,
            cursor.min_y,
            cursor.max_x,
            cursor.max_y,
        )
    )
    return WkbInfo(bytes(cursor.out), base, has_z, has_m, bbox, is_empty)


def multi_type_of(base):
    """Tipo multiparte equivalente. GeoPackage/QGIS prefieren capas homogeneas
    y las clases de ESRI admiten multiparte en cualquier feature class.
    """
    return {
        WKB_POINT: WKB_MULTIPOINT,
        WKB_LINESTRING: WKB_MULTILINESTRING,
        WKB_POLYGON: WKB_MULTIPOLYGON,
    }.get(base, base)


def promote_to_multi(info):
    """Envuelve una geometria simple en su equivalente multiparte.

    ArcGIS admite entidades multiparte en cualquier feature class, pero el WKB
    de una entidad de una sola parte llega como ``LINESTRING`` y no como
    ``MULTILINESTRING``. GeoPackage exige que la geometria coincida con el tipo
    declarado en ``gpkg_geometry_columns``, asi que se promociona al escribir.
    """
    multi_type = multi_type_of(info.geometry_type)
    if multi_type == info.geometry_type:
        return info
    header = struct.pack("<B", 1)
    header += struct.pack("<I", encode_iso_type(multi_type, info.has_z, info.has_m))
    header += struct.pack("<I", 1)
    return WkbInfo(
        header + info.wkb,
        multi_type,
        info.has_z,
        info.has_m,
        info.bbox,
        info.is_empty,
    )
