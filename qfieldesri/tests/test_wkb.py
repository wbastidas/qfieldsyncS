# -*- coding: utf-8 -*-
"""Pruebas del analizador de WKB."""

import struct
import unittest

from qfieldesri.utils import wkb


def point(x, y):
    return struct.pack("<BI", 1, 1) + struct.pack("<dd", x, y)


def line(coordinates):
    data = struct.pack("<BII", 1, 2, len(coordinates))
    for x, y in coordinates:
        data += struct.pack("<dd", x, y)
    return data


class WkbTest(unittest.TestCase):
    def test_punto_simple(self):
        info = wkb.analyze(point(620000.0, 9750000.0))
        self.assertEqual(info.geometry_type, wkb.WKB_POINT)
        self.assertEqual(info.geometry_type_name, "POINT")
        self.assertEqual(info.bbox, (620000.0, 9750000.0, 620000.0, 9750000.0))
        self.assertFalse(info.has_z)

    def test_linea_calcula_envolvente(self):
        info = wkb.analyze(line([(0, 0), (10, 5), (3, 20)]))
        self.assertEqual(info.bbox, (0.0, 0.0, 10.0, 20.0))

    def test_poligono_con_anillo_interior(self):
        data = struct.pack("<BII", 1, 3, 2)
        for ring in (
            [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
            [(2, 2), (4, 2), (4, 4), (2, 2)],
        ):
            data += struct.pack("<I", len(ring))
            for x, y in ring:
                data += struct.pack("<dd", x, y)
        info = wkb.analyze(data)
        self.assertEqual(info.geometry_type, wkb.WKB_POLYGON)
        self.assertEqual(info.bbox, (0.0, 0.0, 10.0, 10.0))

    def test_multilinea(self):
        parts = [line([(0, 0), (1, 1)]), line([(5, 5), (6, 9)])]
        data = struct.pack("<BII", 1, 5, 2) + b"".join(parts)
        info = wkb.analyze(data)
        self.assertEqual(info.geometry_type, wkb.WKB_MULTILINESTRING)
        self.assertEqual(info.bbox, (0.0, 0.0, 6.0, 9.0))

    def test_z_de_arcpy_se_normaliza_a_iso(self):
        # arcpy usa la convencion de banderas de bits (0x80000001 = PointZ);
        # GeoPackage exige la forma ISO (1001).
        data = struct.pack("<BI", 1, 0x80000001) + struct.pack("<ddd", 1, 2, 3)
        info = wkb.analyze(data)
        self.assertTrue(info.has_z)
        self.assertEqual(struct.unpack("<I", info.wkb[1:5])[0], 1001)

    def test_wkb_iso_se_conserva_igual(self):
        data = line([(1, 2), (3, 4)])
        self.assertEqual(wkb.analyze(data).wkb, data)

    def test_big_endian(self):
        data = struct.pack(">BI", 0, 1) + struct.pack(">dd", 7.0, 8.0)
        info = wkb.analyze(data)
        self.assertEqual(info.bbox, (7.0, 8.0, 7.0, 8.0))

    def test_punto_vacio_no_produce_envolvente(self):
        nan = float("nan")
        info = wkb.analyze(struct.pack("<BI", 1, 1) + struct.pack("<dd", nan, nan))
        self.assertTrue(info.is_empty)
        self.assertIsNone(info.bbox)

    def test_promocion_a_multiparte(self):
        info = wkb.promote_to_multi(wkb.analyze(line([(0, 0), (1, 1)])))
        self.assertEqual(info.geometry_type, wkb.WKB_MULTILINESTRING)
        byte_order, geometry_type, count = struct.unpack("<BII", info.wkb[:9])
        self.assertEqual((byte_order, geometry_type, count), (1, 5, 1))
        self.assertEqual(info.bbox, (0.0, 0.0, 1.0, 1.0))

    def test_promocion_de_multiparte_es_idempotente(self):
        original = wkb.analyze(struct.pack("<BII", 1, 5, 1) + line([(0, 0), (1, 1)]))
        self.assertIs(wkb.promote_to_multi(original), original)

    def test_wkb_truncado_falla_con_mensaje_claro(self):
        with self.assertRaises(wkb.WkbError):
            wkb.analyze(struct.pack("<BI", 1, 2))

    def test_tipo_desconocido_falla(self):
        with self.assertRaises(wkb.WkbError):
            wkb.analyze(struct.pack("<BI", 1, 99))


if __name__ == "__main__":
    unittest.main()
