# -*- coding: utf-8 -*-
"""Pruebas de la linea de comandos.

Se ejerce con el lector en memoria, asi que no hace falta ArcGIS. Para poder
apuntar el CLI a la geodatabase de demostracion se sustituye la fabrica de
lectores, que es exactamente el punto de extension que existe para eso.
"""

import os
import shutil
import tempfile
import unittest

from qfieldesri import cli
from qfieldesri.core.synchronizer import Change
from qfieldesri.demo import build_reader


class CliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.reader = build_reader()
        self._original = cli.get_reader
        cli.get_reader = lambda workspace, prefer=None: self.reader
        # La salida del CLI se captura para no ensuciar el informe de pruebas
        # y para poder comprobarla.
        self.output = []
        self._original_out = cli._out
        cli._out = self.output.append

    def tearDown(self):
        cli.get_reader = self._original
        cli._out = self._original_out
        shutil.rmtree(self.directory, ignore_errors=True)

    @property
    def text(self):
        return "\n".join(self.output)

    def _run(self, *arguments):
        return cli.main(list(arguments))

    def test_analizar(self):
        report = os.path.join(self.directory, "analisis.json")
        code = self._run("analizar", "--gdb", "demo.gdb", "--json", report)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(os.path.isfile(report))

        import json

        with open(report) as handle:
            payload = json.load(handle)
        self.assertEqual(len(payload["layers"]), 4)
        self.assertIn("Provincias", payload["domains"])

    def test_configurar_y_empaquetar_con_configuracion(self):
        config_path = os.path.join(self.directory, "config.json")
        self.assertEqual(
            self._run(
                "configurar",
                "--gdb",
                "demo.gdb",
                "--salida",
                config_path,
                "--solo",
                "EstructuraSoporte",
            ),
            cli.EXIT_OK,
        )
        self.assertTrue(os.path.isfile(config_path))

        from qfieldesri.core.config import PackagingConfig

        config = PackagingConfig.load(config_path)
        config.output_dir = self.directory
        config.project_name = "desde_config"
        config.save(config_path)

        self.assertEqual(
            self._run("empaquetar", "--gdb", "demo.gdb", "--config", config_path),
            cli.EXIT_OK,
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.directory, "desde_config", "data.gpkg"))
        )

    def test_empaquetar_solo_una_clase(self):
        code = self._run(
            "empaquetar",
            "--gdb",
            "demo.gdb",
            "--salida",
            self.directory,
            "--nombre",
            "solo_postes",
            "--solo",
            "EstructuraSoporte",
            "--foto",
            "EstructuraSoporte:FOTO",
        )
        self.assertEqual(code, cli.EXIT_OK)

        import sqlite3

        connection = sqlite3.connect(
            os.path.join(self.directory, "solo_postes", "data.gpkg")
        )
        tables = [
            row[0] for row in connection.execute("SELECT table_name FROM gpkg_contents")
        ]
        connection.close()
        self.assertIn("EstructuraSoporte", tables)
        self.assertNotIn("TramoDistribucionAereo", tables)

    def test_empaquetar_con_filtro(self):
        code = self._run(
            "empaquetar",
            "--gdb",
            "demo.gdb",
            "--salida",
            self.directory,
            "--nombre",
            "filtrado",
            "--filtro",
            "EstructuraSoporte=MATERIAL = 'HORMIGON'",
        )
        self.assertEqual(code, cli.EXIT_OK)

    def test_sincronizar_simula_por_omision(self):
        self._run(
            "empaquetar",
            "--gdb",
            "demo.gdb",
            "--salida",
            self.directory,
            "--nombre",
            "ida",
        )
        package = os.path.join(self.directory, "ida")

        from qfieldesri.utils.sqlite_gpkg import connect

        connection = connect(os.path.join(package, "data.gpkg"))
        connection.execute("UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1")
        connection.commit()
        connection.close()

        informe = os.path.join(self.directory, "informe.json")
        code = self._run(
            "sincronizar", package, "--gdb", "demo.gdb", "--informe", informe
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(self.reader.updated, [])

        import json

        with open(informe) as handle:
            payload = json.load(handle)
        self.assertEqual(payload["summary"]["modificaciones"], 1)

    def test_sincronizar_aplica_con_la_bandera(self):
        self._run(
            "empaquetar",
            "--gdb",
            "demo.gdb",
            "--salida",
            self.directory,
            "--nombre",
            "ida2",
        )
        package = os.path.join(self.directory, "ida2")

        from qfieldesri.utils.sqlite_gpkg import connect

        connection = connect(os.path.join(package, "data.gpkg"))
        connection.execute("UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1")
        connection.commit()
        connection.close()

        code = self._run("sincronizar", package, "--gdb", "demo.gdb", "--aplicar")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(len(self.reader.updated), 1)

    def test_demo_no_necesita_geodatabase(self):
        code = self._run("demo", "--salida", self.directory, "--nombre", "ejemplo")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(
            os.path.isfile(os.path.join(self.directory, "ejemplo", "ejemplo.qgs"))
        )

    def test_el_resumen_lista_las_entidades(self):
        self._run(
            "empaquetar",
            "--gdb",
            "demo.gdb",
            "--salida",
            self.directory,
            "--nombre",
            "resumen",
        )
        self.assertIn("EstructuraSoporte", self.text)
        self.assertIn("TOTAL", self.text)

    def test_sin_subcomando_muestra_la_ayuda(self):
        self.assertEqual(self._run(), cli.EXIT_ERROR)

    def test_error_controlado(self):
        # Una carpeta que no es un paquete debe fallar con mensaje, no con traza.
        self.assertEqual(
            self._run("sincronizar", self.directory, "--gdb", "demo.gdb"),
            cli.EXIT_ERROR,
        )


class ChangeTest(unittest.TestCase):
    def test_serializacion_de_un_cambio(self):
        change = Change(
            Change.UPDATE,
            "Barra",
            "Barra",
            3,
            key_value="{x}",
            attributes={"A": 1},
        )
        payload = change.to_dict()
        self.assertEqual(payload["kind"], "modificacion")
        self.assertEqual(payload["fields"], ["A"])


if __name__ == "__main__":
    unittest.main()
