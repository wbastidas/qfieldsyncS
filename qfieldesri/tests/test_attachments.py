# -*- coding: utf-8 -*-
"""Pruebas del manejo de adjuntos capturados en campo."""

import io
import os
import shutil
import sqlite3
import tempfile
import unittest

from qfieldesri.core import attachments
from qfieldesri.core.config import LayerConfig, PackagingConfig
from qfieldesri.core.packager import Packager, load_manifest
from qfieldesri.demo import build_reader
from qfieldesri.utils.sqlite_gpkg import connect


class AttachmentsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        reader = build_reader()
        config = PackagingConfig(
            workspace="demo.gdb", output_dir=self.directory, project_name="fotos"
        )
        config.layers["EstructuraSoporte"] = LayerConfig(
            "EstructuraSoporte", attachment_fields={"FOTO": "image"}
        )
        self.result = Packager(reader, config).run()
        self.manifest = load_manifest(self.result.project_dir)

        # Simula lo que deja QField: la foto en DCIM/ y la ruta en el campo.
        self.photo = os.path.join(
            self.result.project_dir, "DCIM", "EstructuraSoporte_20250512.jpg"
        )
        with io.open(self.photo, "wb") as handle:
            handle.write(b"jpeg-falso")
        connection = connect(self.result.gpkg_file)
        connection.execute(
            "UPDATE EstructuraSoporte SET FOTO=? WHERE fid=1",
            ("DCIM/EstructuraSoporte_20250512.jpg",),
        )
        # Una segunda foto referenciada pero que no llego al sincronizar.
        connection.execute(
            "UPDATE EstructuraSoporte SET FOTO=? WHERE fid=2", ("DCIM/perdida.jpg",)
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _collect(self):
        connection = sqlite3.connect(self.result.gpkg_file)
        try:
            return attachments.collect(
                self.result.project_dir, self.manifest, connection
            )
        finally:
            connection.close()

    def test_solo_se_recogen_los_campos_marcados_como_adjunto(self):
        items = self._collect()
        self.assertEqual(len(items), 2)
        self.assertEqual(set(item.field for item in items), {"FOTO"})
        self.assertEqual(set(item.layer for item in items), {"EstructuraSoporte"})

    def test_se_distingue_lo_que_llego_de_lo_que_falta(self):
        items = self._collect()
        present = [item for item in items if item.exists]
        self.assertEqual(len(present), 1)
        self.assertEqual(
            present[0].relative_path, "DCIM/EstructuraSoporte_20250512.jpg"
        )
        self.assertTrue(present[0].key_value.startswith("{P"))

    def test_copia_al_repositorio_compartido(self):
        repository = os.path.join(self.directory, "repositorio")
        copied, missing = attachments.copy_to_repository(self._collect(), repository)
        self.assertEqual(len(copied), 1)
        self.assertEqual(len(missing), 1)
        destination = list(copied.values())[0]
        self.assertTrue(os.path.isfile(destination))
        self.assertIn("EstructuraSoporte", destination)

    def test_tabla_de_coincidencias_para_add_attachments(self):
        path = os.path.join(self.directory, "match.csv")
        _path, count = attachments.build_match_table(self._collect(), path)
        self.assertEqual(count, 1)
        with io.open(path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("CLAVE,RUTA", content)
        self.assertIn("EstructuraSoporte_20250512.jpg", content)
        # Las que no llegaron no se registran como adjunto.
        self.assertNotIn("perdida.jpg", content)

    def test_una_clase_sin_campos_de_foto_no_aporta_nada(self):
        for entry in self.manifest["layers"]:
            entry["attachment_fields"] = {}
        self.assertEqual(self._collect(), [])


if __name__ == "__main__":
    unittest.main()
