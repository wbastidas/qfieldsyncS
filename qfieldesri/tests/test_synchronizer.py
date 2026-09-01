# -*- coding: utf-8 -*-
"""Pruebas del regreso de QField a la geodatabase."""

import os
import shutil
import tempfile
import unittest

from qfieldesri.core.config import PackagingConfig
from qfieldesri.core.packager import Packager
from qfieldesri.core.synchronizer import (
    Change,
    ConflictPolicy,
    SyncError,
    Synchronizer,
    is_qfieldesri_package,
)
from qfieldesri.demo import build_reader
from qfieldesri.utils.sqlite_gpkg import connect


class SynchronizerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.reader = build_reader()
        config = PackagingConfig(
            workspace="demo.gdb", output_dir=self.directory, project_name="demo"
        )
        self.result = Packager(self.reader, config).run()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _edit(self, statements):
        connection = connect(self.result.gpkg_file)
        for statement in statements:
            connection.execute(statement)
        connection.commit()
        connection.close()

    def _sync(self, **kwargs):
        return Synchronizer(self.result.project_dir, self.reader, **kwargs)

    # ------------------------------------------------------------------
    def test_es_un_paquete_de_qfieldesri(self):
        self.assertTrue(is_qfieldesri_package(self.result.project_dir))
        self.assertFalse(is_qfieldesri_package(self.directory))

    def test_sin_cambios_no_detecta_nada(self):
        report = self._sync().detect()
        self.assertEqual(report.changes, [])
        self.assertIn("Modificaciones: 0", report.format())

    def test_detecta_modificacion(self):
        self._edit(["UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1"])
        report = self._sync().detect()
        changes = report.of_kind(Change.UPDATE)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].layer, "EstructuraSoporte")
        self.assertEqual(changes[0].attributes["MATERIAL"], "ACERO")
        self.assertFalse(changes[0].conflict)

    def test_detecta_alta(self):
        self._edit(
            [
                "INSERT INTO EstructuraSoporte (geom, CODIGOESTRUCTURA, MATERIAL) "
                "SELECT geom, 'NUEVO-1', 'MADERA' FROM EstructuraSoporte WHERE fid=1"
            ]
        )
        report = self._sync().detect()
        changes = report.of_kind(Change.INSERT)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].attributes["CODIGOESTRUCTURA"], "NUEVO-1")
        self.assertIsNotNone(changes[0].wkb)

    def test_detecta_baja(self):
        self._edit(["DELETE FROM EstructuraSoporte WHERE fid=3"])
        report = self._sync().detect()
        self.assertEqual(len(report.of_kind(Change.DELETE)), 1)

    def test_cambio_de_geometria_se_detecta(self):
        self._edit(
            [
                "UPDATE EstructuraSoporte SET geom = "
                "(SELECT geom FROM EstructuraSoporte WHERE fid=4) WHERE fid=1"
            ]
        )
        report = self._sync().detect()
        self.assertEqual(len(report.of_kind(Change.UPDATE)), 1)

    def test_cambio_en_campo_de_solo_lectura_no_cuenta(self):
        # OBJECTID no es reescribible: aunque cambie en el paquete, no genera
        # una modificacion para la geodatabase.
        self._edit(["UPDATE EstructuraSoporte SET OBJECTID=999 WHERE fid=1"])
        report = self._sync().detect()
        self.assertEqual(report.of_kind(Change.UPDATE), [])

    # ------------------------------------------------------------------
    def test_aplica_modificacion_y_alta(self):
        self._edit(
            [
                "UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1",
                "INSERT INTO EstructuraSoporte (geom, CODIGOESTRUCTURA) "
                "SELECT geom, 'NUEVO-1' FROM EstructuraSoporte WHERE fid=1",
            ]
        )
        report = self._sync().apply()
        self.assertEqual(report.summary()["aplicados"], 2)
        self.assertEqual(len(self.reader.updated), 1)
        self.assertEqual(len(self.reader.inserted), 1)
        # Los campos gestionados por ArcGIS no se envian de vuelta.
        _layer, attributes, _wkb = self.reader.inserted[0]
        self.assertNotIn("GLOBALID", attributes)
        self.assertNotIn("OBJECTID", attributes)

    def test_las_bajas_no_se_aplican_por_omision(self):
        self._edit(["DELETE FROM EstructuraSoporte WHERE fid=3"])
        report = self._sync().apply()
        self.assertEqual(self.reader.deleted, [])
        self.assertFalse(report.of_kind(Change.DELETE)[0].applied)

    def test_las_bajas_se_aplican_si_se_piden(self):
        self._edit(["DELETE FROM EstructuraSoporte WHERE fid=3"])
        report = self._sync(apply_deletes=True).apply()
        self.assertEqual(len(self.reader.deleted), 1)
        self.assertTrue(report.of_kind(Change.DELETE)[0].applied)

    # ------------------------------------------------------------------
    def test_conflicto_cuando_la_geodatabase_tambien_cambio(self):
        self._edit(["UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1"])
        # Alguien edito el mismo poste en la oficina mientras tanto.
        self.reader.data["EstructuraSoporte"][0][1]["MATERIAL"] = "METALICO"

        report = self._sync().apply()
        change = report.of_kind(Change.UPDATE)[0]
        self.assertTrue(change.conflict)
        self.assertFalse(change.applied)
        self.assertEqual(self.reader.updated, [])

    def test_conflicto_resuelto_a_favor_del_campo(self):
        self._edit(["UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1"])
        self.reader.data["EstructuraSoporte"][0][1]["MATERIAL"] = "METALICO"

        report = self._sync(conflict_policy=ConflictPolicy.FIELD_WINS).apply()
        change = report.of_kind(Change.UPDATE)[0]
        self.assertTrue(change.conflict)
        self.assertTrue(change.applied)
        self.assertEqual(len(self.reader.updated), 1)

    def test_conflicto_si_el_registro_ya_no_existe_en_la_geodatabase(self):
        self._edit(["UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1"])
        del self.reader.data["EstructuraSoporte"][0]

        report = self._sync().detect()
        change = report.of_kind(Change.UPDATE)[0]
        self.assertTrue(change.conflict)
        self.assertIn("ya no existe", change.message)

    # ------------------------------------------------------------------
    def test_sin_linea_base_no_se_puede_sincronizar(self):
        connection = connect(self.result.gpkg_file)
        connection.execute("DROP TABLE qfe_baseline")
        connection.commit()
        connection.close()
        with self.assertRaises(SyncError):
            self._sync().detect()

    def test_informe_en_json(self):
        self._edit(["UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1"])
        report = self._sync().detect()
        path = os.path.join(self.directory, "informe.json")
        report.write(path)
        import json

        with open(path) as handle:
            payload = json.load(handle)
        self.assertEqual(payload["summary"]["modificaciones"], 1)
        self.assertTrue(payload["dry_run"])

    def test_la_deteccion_no_toca_la_geodatabase(self):
        self._edit(["UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1"])
        self._sync().detect()
        self.assertEqual(self.reader.updated, [])
        self.assertEqual(self.reader.inserted, [])
        self.assertEqual(self.reader.deleted, [])


if __name__ == "__main__":
    unittest.main()
