# -*- coding: utf-8 -*-
"""Pruebas del generador del archivo de proyecto que abre QField."""

import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from qfieldesri.core.model import SpatialReferenceInfo
from qfieldesri.writers.qfield_project import (
    FieldSpec,
    LayerSpec,
    QFieldProjectWriter,
    RelationSpec,
    WidgetSpec,
)

CRS = SpatialReferenceInfo(
    code=32717, name="WGS 84 / UTM zone 17S", wkt='PROJCS["UTM 17S"]'
)


def build_writer():
    writer = QFieldProjectWriter(
        "Proyecto de prueba",
        CRS,
        qfield_options={"initialMapMode": "digitize", "maximumImageWidthHeight": 1600},
        project_extent=(0.0, 0.0, 100.0, 100.0),
    )
    poste = LayerSpec(
        table="EstructuraSoporte",
        title="Poste",
        geometry_type="Point",
        wkb_type="Point",
        crs=CRS,
        extent=(0.0, 0.0, 10.0, 10.0),
        group="Redes",
        fields=[
            FieldSpec(
                "GLOBALID",
                "GLOBALID",
                WidgetSpec("Hidden"),
                editable=False,
                hidden=True,
                group="Sistema",
            ),
            FieldSpec(
                "CODIGO",
                "Codigo",
                WidgetSpec("TextEdit"),
                not_null=True,
                group="Datos obligatorios",
            ),
            FieldSpec(
                "MATERIAL",
                "Material",
                WidgetSpec("ValueMap", {"map": [{"Hormigon": "H"}, {"Madera": "M"}]}),
                group="Atributos",
                default_expression="'H'",
            ),
        ],
        subtype_field=None,
        display_expression='"CODIGO"',
    )
    unidad = LayerSpec(
        table="UNIDAD",
        title="Unidad",
        fields=[FieldSpec("POSTEID", "Poste", WidgetSpec("TextEdit"))],
        group="Redes",
    )
    writer.add_layer(poste)
    writer.add_layer(unidad)
    writer.add_relation(
        RelationSpec(
            "Poste_Unidad",
            "EstructuraSoporte",
            "UNIDAD",
            "GLOBALID",
            "POSTEID",
            label="Unidades",
        )
    )
    return writer, poste, unidad


class QFieldProjectWriterTest(unittest.TestCase):
    def setUp(self):
        self.writer, self.poste, self.unidad = build_writer()
        self.root = self.writer.build()

    def test_raiz_y_titulo(self):
        self.assertEqual(self.root.tag, "qgis")
        self.assertEqual(self.root.find("title").text, "Proyecto de prueba")

    def test_crs_del_proyecto(self):
        authid = self.root.find("./projectCrs/spatialrefsys/authid")
        self.assertEqual(authid.text, "EPSG:32717")

    def test_una_capa_por_tabla(self):
        layers = self.root.findall("./projectlayers/maplayer")
        self.assertEqual(len(layers), 2)
        sources = [layer.find("datasource").text for layer in layers]
        self.assertIn("./data.gpkg|layername=EstructuraSoporte", sources)

    def test_tabla_sin_geometria(self):
        for layer in self.root.findall("./projectlayers/maplayer"):
            if layer.find("layername").text == "Unidad":
                self.assertEqual(layer.get("geometry"), "No geometry")
                return
        self.fail("No se escribio la capa sin geometria")

    def test_arbol_de_capas_agrupado(self):
        group = self.root.find("./layer-tree-group/layer-tree-group")
        self.assertEqual(group.get("name"), "Redes")
        self.assertEqual(len(group.findall("layer-tree-layer")), 2)

    def test_widget_value_map(self):
        options = self.root.findall(
            "./projectlayers/maplayer/fieldConfiguration/field"
            "[@name='MATERIAL']/editWidget/config//Option"
        )
        values = [option.get("value") for option in options if option.get("name")]
        self.assertIn("H", values)
        names = [option.get("name") for option in options if option.get("value")]
        self.assertIn("Hormigon", names)

    def test_alias_y_valores_por_defecto(self):
        alias = self.root.find(
            "./projectlayers/maplayer/aliases/alias[@field='MATERIAL']"
        )
        self.assertEqual(alias.get("name"), "Material")
        default = self.root.find(
            "./projectlayers/maplayer/defaults/default[@field='MATERIAL']"
        )
        self.assertEqual(default.get("expression"), "'H'")

    def test_restriccion_de_no_nulo(self):
        constraint = self.root.find(
            "./projectlayers/maplayer/constraints/constraint[@field='CODIGO']"
        )
        self.assertEqual(constraint.get("constraints"), "1")
        self.assertEqual(constraint.get("notnull_strength"), "1")

    def test_campo_oculto_no_editable(self):
        editable = self.root.find(
            "./projectlayers/maplayer/editable/field[@name='GLOBALID']"
        )
        self.assertEqual(editable.get("editable"), "0")

    def test_formulario_por_pestanas(self):
        containers = self.root.findall(
            "./projectlayers/maplayer/attributeEditorForm/attributeEditorContainer"
        )
        names = [container.get("name") for container in containers]
        self.assertIn("Datos obligatorios", names)
        self.assertIn("Atributos", names)
        # El campo oculto no debe aparecer en ninguna pestana.
        self.assertNotIn("Sistema", names)
        self.assertIn("Unidades", names)  # pestana de la relacion

    def test_relacion_apunta_a_los_identificadores_de_capa(self):
        relation = self.root.find("./relations/relation")
        self.assertEqual(relation.get("referencedLayer"), self.poste.id)
        self.assertEqual(relation.get("referencingLayer"), self.unidad.id)
        field_ref = relation.find("fieldRef")
        self.assertEqual(field_ref.get("referencedField"), "GLOBALID")
        self.assertEqual(field_ref.get("referencingField"), "POSTEID")

    def test_opciones_de_qfield(self):
        mode = self.root.find("./properties/qfieldsync/initialMapMode")
        self.assertEqual(mode.text, "digitize")
        size = self.root.find("./properties/qfieldsync/maximumImageWidthHeight")
        self.assertEqual(size.get("type"), "int")
        self.assertEqual(size.text, "1600")

    def test_rutas_relativas(self):
        absolute = self.root.find("./properties/Paths/Absolute")
        self.assertEqual(absolute.text, "false")

    def test_ajuste_activado_para_trazar_red(self):
        snapping = self.root.find("./snapping-settings")
        self.assertEqual(snapping.get("enabled"), "1")

    def test_renderizado_categorizado_por_subtipo(self):
        writer, _poste, _unidad = build_writer()
        capa = writer.layers[0]
        capa.subtype_field = "SUBTIPO"
        capa.subtype_categories = [(1, "Baja tension"), (2, "Media tension")]
        root = writer.build()
        renderer = root.find("./projectlayers/maplayer/renderer-v2")
        self.assertEqual(renderer.get("type"), "categorizedSymbol")
        self.assertEqual(renderer.get("attr"), "SUBTIPO")
        self.assertEqual(len(renderer.findall("./categories/category")), 2)

    def test_escritura_a_disco_es_xml_valido(self):
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "proyecto.qgs")
            self.writer.write(path)
            with open(path, "rb") as handle:
                head = handle.read(60)
            self.assertTrue(head.startswith(b'<?xml version="1.0" encoding="UTF-8"?>'))
            self.assertIn(b"<!DOCTYPE qgis", head)
            tree = ET.parse(path)
            self.assertEqual(tree.getroot().tag, "qgis")
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
