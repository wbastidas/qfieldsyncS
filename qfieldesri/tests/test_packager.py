# -*- coding: utf-8 -*-
"""Pruebas del empaquetado completo, con la geodatabase de demostracion."""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET

from qfieldesri.core.checker import Feedback, WorkspaceChecker
from qfieldesri.core.config import LayerAction, LayerConfig, PackagingConfig
from qfieldesri.core.packager import MANIFEST_NAME, Packager
from qfieldesri.demo import build_reader


class PackagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.mkdtemp()
        cls.reader = build_reader()
        cls.config = PackagingConfig(
            workspace="demo.gdb",
            output_dir=cls.directory,
            project_name="demo",
            title="Demostracion",
            profile="cnel_ep",
            big_domain_threshold=40,
        )
        cls.config.layer_config("EstructuraSoporte").attachment_fields = {
            "FOTO": "image"
        }
        cls.result = Packager(cls.reader, cls.config).run()
        cls.connection = sqlite3.connect(cls.result.gpkg_file)
        cls.project = ET.parse(cls.result.project_file).getroot()

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()
        shutil.rmtree(cls.directory, ignore_errors=True)

    # -- estructura de la carpeta --------------------------------------
    def test_estructura_de_la_carpeta(self):
        contents = sorted(os.listdir(self.result.project_dir))
        for expected in (
            "DCIM",
            "audio",
            "data.gpkg",
            "demo.qgs",
            "files",
            MANIFEST_NAME,
            "video",
        ):
            self.assertIn(expected, contents)

    def test_todas_las_clases_empaquetadas(self):
        self.assertEqual(
            sorted(self.result.layer_counts),
            [
                "CIRCUITOFUENTE",
                "EstructuraSoporte",
                "PuestoTransfDistribucion",
                "TramoDistribucionAereo",
                "UNIDADTRANSFDISTRIBUCION",
            ],
        )
        self.assertEqual(self.result.layer_counts["EstructuraSoporte"], 6)
        self.assertEqual(self.result.layer_counts["UNIDADTRANSFDISTRIBUCION"], 3)

    # -- GeoPackage ----------------------------------------------------
    def test_la_tabla_sin_geometria_va_como_atributos(self):
        data_type = self.connection.execute(
            "SELECT data_type FROM gpkg_contents WHERE table_name=?",
            ("UNIDADTRANSFDISTRIBUCION",),
        ).fetchone()[0]
        self.assertEqual(data_type, "attributes")

    def test_la_linea_temporal_de_tramos_es_multiparte(self):
        geometry_type = self.connection.execute(
            "SELECT geometry_type_name FROM gpkg_geometry_columns WHERE table_name=?",
            ("TramoDistribucionAereo",),
        ).fetchone()[0]
        self.assertEqual(geometry_type, "MULTILINESTRING")

    def test_dominio_grande_va_como_tabla_de_catalogo(self):
        count = self.connection.execute(
            "SELECT count(*) FROM dom_Catalogo_Conductores"
        ).fetchone()[0]
        self.assertEqual(count, 60)

    def test_linea_base_para_la_sincronizacion(self):
        rows = dict(
            self.connection.execute(
                "SELECT table_name, count(*) FROM qfe_baseline GROUP BY table_name"
            )
        )
        self.assertEqual(rows["EstructuraSoporte"], 6)
        self.assertEqual(rows["UNIDADTRANSFDISTRIBUCION"], 3)

    def test_la_linea_base_guarda_la_clave_de_origen(self):
        key = self.connection.execute(
            "SELECT source_key FROM qfe_baseline WHERE table_name='EstructuraSoporte' "
            "ORDER BY fid LIMIT 1"
        ).fetchone()[0]
        self.assertTrue(key.startswith("{P"))

    # -- proyecto de QField --------------------------------------------
    def _layer(self, name):
        for layer in self.project.findall("./projectlayers/maplayer"):
            if layer.find("datasource").text.endswith("layername=%s" % name):
                return layer
        self.fail("No se encontro la capa %s" % name)

    def test_alias_de_clase_del_perfil(self):
        # El perfil CNEL EP documenta 'EstructuraSoporte' con alias 'Poste'.
        self.assertEqual(
            self._layer("EstructuraSoporte").find("layername").text, "Poste"
        )

    def test_subtipos_como_renderizado_categorizado(self):
        renderer = self._layer("TramoDistribucionAereo").find("renderer-v2")
        self.assertEqual(renderer.get("type"), "categorizedSymbol")
        self.assertEqual(renderer.get("attr"), "SUBTIPO")

    def test_dominio_de_subtipo_se_une(self):
        # VOLTAJE usa 'Voltaje MT' en el subtipo 1 y 'Voltaje BT' en el 2:
        # el desplegable debe ofrecer los valores de ambos.
        options = self._layer("TramoDistribucionAereo").findall(
            "./fieldConfiguration/field[@name='VOLTAJE']/editWidget/config//Option"
        )
        labels = [option.get("name") for option in options if option.get("value")]
        self.assertIn("13,8 kV", labels)
        self.assertIn("120/240 V", labels)

    def test_dominio_de_rango_produce_widget_range(self):
        widget = self._layer("EstructuraSoporte").find(
            "./fieldConfiguration/field[@name='ALTURA']/editWidget"
        )
        self.assertEqual(widget.get("type"), "Range")

    def test_dominio_grande_produce_value_relation(self):
        widget = self._layer("TramoDistribucionAereo").find(
            "./fieldConfiguration/field[@name='CODIGOCONDUCTORFASE']/editWidget"
        )
        self.assertEqual(widget.get("type"), "ValueRelation")

    def test_campo_de_foto_produce_recurso_externo(self):
        widget = self._layer("EstructuraSoporte").find(
            "./fieldConfiguration/field[@name='FOTO']/editWidget"
        )
        self.assertEqual(widget.get("type"), "ExternalResource")
        expressions = [
            option.get("value")
            for option in widget.findall(".//Option")
            if option.get("name") == "expression"
        ]
        self.assertTrue(any("DCIM/" in (value or "") for value in expressions))

    def test_globalid_va_oculto(self):
        widget = self._layer("EstructuraSoporte").find(
            "./fieldConfiguration/field[@name='GLOBALID']/editWidget"
        )
        self.assertEqual(widget.get("type"), "Hidden")

    def test_relacion_puesto_unidad(self):
        relations = self.project.findall("./relations/relation")
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].get("name"), "Transformadores")

    def test_capas_agrupadas_segun_el_perfil(self):
        groups = [
            group.get("name")
            for group in self.project.findall("./layer-tree-group/layer-tree-group")
        ]
        self.assertIn("Redes y soporte", groups)
        self.assertIn("Proteccion y potencia", groups)
        self.assertIn("Catalogos", groups)

    # -- manifiesto ----------------------------------------------------
    def test_manifiesto(self):
        with open(os.path.join(self.result.project_dir, MANIFEST_NAME)) as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["profile"], "cnel_ep")
        self.assertEqual(manifest["crs"], 32717)
        entry = next(
            layer
            for layer in manifest["layers"]
            if layer["source_class"] == "EstructuraSoporte"
        )
        self.assertEqual(entry["key_field"], "GLOBALID")
        self.assertIn("MATERIAL", entry["writable_fields"])
        # Los campos que ArcGIS gestiona no se pueden reescribir.
        self.assertNotIn("OBJECTID", entry["writable_fields"])
        self.assertNotIn("GLOBALID", entry["writable_fields"])
        self.assertEqual(entry["attachment_fields"], {"FOTO": "image"})


class PackagerOptionsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.reader = build_reader()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _config(self, **kwargs):
        return PackagingConfig(
            workspace="demo.gdb", output_dir=self.directory, project_name="p", **kwargs
        )

    def test_clase_excluida(self):
        config = self._config()
        config.layers["TramoDistribucionAereo"] = LayerConfig(
            "TramoDistribucionAereo", action=LayerAction.REMOVE
        )
        result = Packager(self.reader, config).run()
        self.assertNotIn("TramoDistribucionAereo", result.layer_counts)

    def test_clase_solo_esquema(self):
        config = self._config()
        config.layers["EstructuraSoporte"] = LayerConfig(
            "EstructuraSoporte", action=LayerAction.EMPTY
        )
        result = Packager(self.reader, config).run()
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 0)
        connection = sqlite3.connect(result.gpkg_file)
        self.assertEqual(
            connection.execute("SELECT count(*) FROM EstructuraSoporte").fetchone()[0],
            0,
        )
        connection.close()

    def test_clase_de_solo_lectura(self):
        config = self._config()
        config.layers["EstructuraSoporte"] = LayerConfig(
            "EstructuraSoporte", action=LayerAction.READ_ONLY
        )
        result = Packager(self.reader, config).run()
        root = ET.parse(result.project_file).getroot()
        for layer in root.findall("./projectlayers/maplayer"):
            if layer.find("datasource").text.endswith("layername=EstructuraSoporte"):
                self.assertEqual(layer.get("readOnly"), "1")
                return
        self.fail("No se encontro la capa")

    def test_limite_de_entidades(self):
        config = self._config()
        config.layers["EstructuraSoporte"] = LayerConfig(
            "EstructuraSoporte", max_features=2
        )
        result = Packager(self.reader, config).run()
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 2)

    def test_umbral_de_dominio_bajo_convierte_todo_en_catalogo(self):
        config = self._config(big_domain_threshold=2)
        result = Packager(self.reader, config).run()
        connection = sqlite3.connect(result.gpkg_file)
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM gpkg_contents WHERE table_name LIKE 'dom_%'"
            )
        ]
        connection.close()
        self.assertIn("dom_Provincias", tables)

    def test_configuracion_se_guarda_y_se_relee(self):
        config = self._config()
        config.layer_config("EstructuraSoporte").where_clause = "ALTURA > 9"
        path = os.path.join(self.directory, "config.json")
        config.save(path)
        again = PackagingConfig.load(path)
        self.assertEqual(again.layers["EstructuraSoporte"].where_clause, "ALTURA > 9")


class CheckerTest(unittest.TestCase):
    def setUp(self):
        self.reader = build_reader()
        self.workspace = self.reader.describe_workspace()
        self.config = PackagingConfig(workspace="demo.gdb", output_dir=".")

    def _checks(self, result):
        return set(feedback.check for feedback in result.feedbacks)

    def test_la_demo_no_tiene_errores(self):
        result = WorkspaceChecker(self.workspace, self.config).check()
        self.assertFalse(result.has_errors, result.format())

    def test_avisa_de_dominio_dependiente_del_subtipo(self):
        result = WorkspaceChecker(self.workspace, self.config).check()
        self.assertIn("dominio_por_subtipo", self._checks(result))

    def test_avisa_de_clase_sin_globalid(self):
        self.workspace.layer("EstructuraSoporte").globalid_field = None
        result = WorkspaceChecker(self.workspace, self.config).check()
        self.assertIn("sin_globalid", self._checks(result))

    def test_error_por_campo_reservado(self):
        from qfieldesri.core.model import FieldInfo

        self.workspace.layer("EstructuraSoporte").fields.append(
            FieldInfo("fid", "Integer")
        )
        result = WorkspaceChecker(self.workspace, self.config).check()
        self.assertTrue(result.has_errors)
        self.assertIn("campo_reservado", self._checks(result))

    def test_error_por_colision_de_nombres(self):
        from qfieldesri.core.model import LayerInfo

        self.workspace.layers.append(
            LayerInfo(name="SDE.GYE.EstructuraSoporte", geometry_type="Point")
        )
        result = WorkspaceChecker(self.workspace, self.config).check()
        self.assertIn("colision_nombres", self._checks(result))

    def test_error_si_no_queda_ninguna_capa(self):
        for layer in self.workspace.layers:
            self.config.layer_config(layer.name).action = LayerAction.REMOVE
        result = WorkspaceChecker(self.workspace, self.config).check()
        self.assertTrue(result.has_errors)
        self.assertEqual(result.errors[0].check, "sin_capas")

    def test_niveles_de_aviso(self):
        result = WorkspaceChecker(self.workspace, self.config).check()
        for feedback in result.feedbacks:
            self.assertIn(
                feedback.level,
                (Feedback.ERROR, Feedback.WARNING, Feedback.INFO),
            )
            self.assertTrue(feedback.format())


if __name__ == "__main__":
    unittest.main()
