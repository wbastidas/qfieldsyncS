# -*- coding: utf-8 -*-
"""Pruebas del escritor de GeoPackage."""

import datetime
import os
import shutil
import sqlite3
import struct
import tempfile
import unittest

from qfieldesri.utils.sqlite_gpkg import connect as gpkg_connect
from qfieldesri.writers.geopackage import (
    APPLICATION_ID,
    GeoPackageError,
    GeoPackageWriter,
    adapt_value,
    esri_type_to_gpkg,
    parse_gpkg_blob,
)


def point(x, y):
    return struct.pack("<BI", 1, 1) + struct.pack("<dd", x, y)


class TypeMappingTest(unittest.TestCase):
    def test_tipos_de_esri(self):
        self.assertEqual(esri_type_to_gpkg("SmallInteger"), "SMALLINT")
        self.assertEqual(esri_type_to_gpkg("Integer"), "MEDIUMINT")
        self.assertEqual(esri_type_to_gpkg("Double"), "DOUBLE")
        self.assertEqual(esri_type_to_gpkg("Date"), "DATETIME")
        self.assertEqual(esri_type_to_gpkg("GlobalID"), "TEXT(38)")
        self.assertEqual(esri_type_to_gpkg("String", 50), "TEXT(50)")
        self.assertEqual(esri_type_to_gpkg("OID"), "INTEGER")

    def test_fechas_en_iso(self):
        value = datetime.datetime(2025, 5, 12, 8, 30, 15, 123000)
        self.assertEqual(adapt_value(value), "2025-05-12T08:30:15.123Z")
        self.assertEqual(adapt_value(datetime.date(2025, 5, 12)), "2025-05-12")

    def test_valores_simples(self):
        self.assertIsNone(adapt_value(None))
        self.assertEqual(adapt_value(True), 1)
        self.assertEqual(adapt_value("texto"), "texto")


class GeoPackageWriterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "data.gpkg")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _build(self):
        with GeoPackageWriter(self.path) as gpkg:
            srs = gpkg.add_srs(32717, "EPSG", 'PROJCS["UTM 17S"]')
            gpkg.create_feature_table(
                "Poste",
                [("CODIGO", "TEXT(20)"), ("ALTURA", "DOUBLE")],
                "POINT",
                srs,
            )
            for index in range(3):
                gpkg.insert(
                    "Poste",
                    {"CODIGO": "P%d" % index, "ALTURA": 9.0 + index},
                    wkb=point(620000.0 + index, 9750000.0),
                )
        return sqlite3.connect(self.path)

    def test_contenedor_valido(self):
        connection = self._build()
        self.assertEqual(
            connection.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID
        )
        contents = connection.execute(
            "SELECT data_type, srs_id, min_x, max_x FROM gpkg_contents "
            "WHERE table_name='Poste'"
        ).fetchone()
        self.assertEqual(contents[0], "features")
        self.assertEqual(contents[1], 32717)
        self.assertEqual(contents[2], 620000.0)
        self.assertEqual(contents[3], 620002.0)

    def test_filas_obligatorias_del_sistema_de_referencia(self):
        connection = self._build()
        ids = [
            row[0]
            for row in connection.execute(
                "SELECT srs_id FROM gpkg_spatial_ref_sys ORDER BY srs_id"
            )
        ]
        self.assertEqual(ids, [-1, 0, 4326, 32717])

    def test_indice_espacial_poblado(self):
        connection = self._build()
        self.assertEqual(
            connection.execute("SELECT count(*) FROM rtree_Poste_geom").fetchone()[0],
            3,
        )
        extensions = connection.execute(
            "SELECT extension_name FROM gpkg_extensions"
        ).fetchall()
        self.assertIn(("gpkg_rtree_index",), extensions)

    def test_conteo_de_entidades_de_gdal(self):
        connection = self._build()
        self.assertEqual(
            connection.execute(
                "SELECT feature_count FROM gpkg_ogr_contents WHERE table_name='Poste'"
            ).fetchone()[0],
            3,
        )

    def test_cabecera_de_geometria(self):
        connection = self._build()
        blob = connection.execute("SELECT geom FROM Poste LIMIT 1").fetchone()[0]
        srs_id, raw = parse_gpkg_blob(blob)
        self.assertEqual(srs_id, 32717)
        self.assertEqual(raw, point(620000.0, 9750000.0))

    def test_disparadores_del_indice_espacial_funcionan(self):
        self._build().close()
        # Los disparadores usan funciones ST_*, que registra
        # ``qfieldesri.utils.sqlite_gpkg`` para poder editar sin GDAL.
        connection = gpkg_connect(self.path)
        connection.execute(
            "INSERT INTO Poste (geom, CODIGO) SELECT geom, 'NUEVO' FROM Poste LIMIT 1"
        )
        connection.execute("DELETE FROM Poste WHERE CODIGO='P0'")
        connection.commit()
        self.assertEqual(
            connection.execute("SELECT count(*) FROM rtree_Poste_geom").fetchone()[0],
            3,
        )
        self.assertEqual(
            connection.execute(
                "SELECT feature_count FROM gpkg_ogr_contents WHERE table_name='Poste'"
            ).fetchone()[0],
            3,
        )

    def test_tabla_sin_geometria(self):
        with GeoPackageWriter(self.path) as gpkg:
            gpkg.create_attribute_table("Unidad", [("SERIE", "TEXT(30)")])
            gpkg.insert("Unidad", {"SERIE": "SN-1"})
        connection = sqlite3.connect(self.path)
        self.assertEqual(
            connection.execute(
                "SELECT data_type FROM gpkg_contents WHERE table_name='Unidad'"
            ).fetchone()[0],
            "attributes",
        )
        self.assertEqual(
            connection.execute("SELECT count(*) FROM gpkg_geometry_columns").fetchone()[
                0
            ],
            0,
        )

    def test_promocion_a_multiparte(self):
        with GeoPackageWriter(self.path) as gpkg:
            srs = gpkg.add_srs(32717, "EPSG", "wkt")
            gpkg.create_feature_table(
                "Tramo",
                [("A", "TEXT")],
                "MULTILINESTRING",
                srs,
                promote_to_multi=True,
            )
            gpkg.insert(
                "Tramo",
                {"A": "x"},
                wkb=struct.pack("<BII", 1, 2, 2) + struct.pack("<dddd", 0, 0, 1, 1),
            )
        connection = sqlite3.connect(self.path)
        blob = connection.execute("SELECT geom FROM Tramo").fetchone()[0]
        _srs, raw = parse_gpkg_blob(blob)
        self.assertEqual(struct.unpack("<I", raw[1:5])[0], 5)

    def test_columna_reservada_se_rechaza(self):
        with self.assertRaises(GeoPackageError), GeoPackageWriter(self.path) as gpkg:
            srs = gpkg.add_srs(32717, "EPSG", "wkt")
            gpkg.create_feature_table("X", [("fid", "TEXT")], "POINT", srs)

    def test_sin_sistema_de_referencia_usa_el_indefinido(self):
        with GeoPackageWriter(self.path) as gpkg:
            self.assertEqual(gpkg.add_srs(None, "EPSG", None), -1)
            self.assertEqual(gpkg.add_srs(0, "EPSG", None), -1)

    def test_tabla_privada_no_es_visible_para_qgis(self):
        with GeoPackageWriter(self.path) as gpkg:
            gpkg.create_private_table("qfe_prueba", [("a", "TEXT")])
            gpkg.insert_private("qfe_prueba", ["a"], [("uno",)])
        connection = sqlite3.connect(self.path)
        self.assertEqual(
            connection.execute("SELECT count(*) FROM qfe_prueba").fetchone()[0], 1
        )
        self.assertIsNone(
            connection.execute(
                "SELECT 1 FROM gpkg_contents WHERE table_name='qfe_prueba'"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
