# -*- coding: utf-8 -*-
"""Pruebas del perfil de modelo de datos."""

import unittest

from qfieldesri.core.model import (
    CATEGORY_CONNECTIVITY,
    CATEGORY_CORE,
    CATEGORY_OTHER,
    CATEGORY_SYSTEM,
    FieldInfo,
    LayerInfo,
    SubtypeInfo,
)
from qfieldesri.profiles import Profile, available_profiles, load_profile


class CnelProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_profile("cnel_ep")

    def test_esta_disponible(self):
        self.assertIn("cnel_ep", available_profiles())
        self.assertIn("generico", available_profiles())

    def test_cobertura_del_catalogo(self):
        # El catalogo documenta 47 clases y 79 relationship classes.
        self.assertEqual(len(self.profile.classes), 47)
        self.assertEqual(len(self.profile.documented_relationships()), 79)

    def test_red_geometrica(self):
        self.assertEqual(len(self.profile.network["edges"]), 7)
        self.assertEqual(len(self.profile.network["junctions"]), 13)
        self.assertIn("Barra", self.profile.network["edges"])
        self.assertIn("PuntoCarga", self.profile.network["junctions"])

    def test_categorias_de_campo(self):
        self.assertEqual(self.profile.category_of("Barra", "OBJECTID"), CATEGORY_SYSTEM)
        self.assertEqual(
            self.profile.category_of("Barra", "ELECTRICTRACEWEIGHT"),
            CATEGORY_CONNECTIVITY,
        )
        self.assertEqual(
            self.profile.category_of("TramoDistribucionAereo", "VOLTAJE"),
            CATEGORY_CORE,
        )
        self.assertEqual(
            self.profile.category_of("Barra", "FORMABARRA"), CATEGORY_OTHER
        )

    def test_clase_desconocida_usa_heuristica(self):
        self.assertEqual(
            self.profile.category_of("ClaseNueva", "FECHAREGISTRO"), CATEGORY_SYSTEM
        )
        self.assertEqual(
            self.profile.category_of("ClaseNueva", "ANCILLARYROLE"),
            CATEGORY_CONNECTIVITY,
        )
        self.assertEqual(
            self.profile.category_of("ClaseNueva", "POTENCIA"), CATEGORY_OTHER
        )

    def test_alias_y_grupos(self):
        self.assertEqual(self.profile.class_alias("EstructuraSoporte"), "Poste")
        self.assertEqual(
            self.profile.group_of("PuestoTransfDistribucion"),
            "Proteccion y potencia",
        )
        self.assertEqual(
            self.profile.group_of("PuntoCarga"), "Consumidores y alumbrado"
        )

    def test_par_puesto_unidad(self):
        self.assertEqual(self.profile.kind_of("PuestoTransfDistribucion"), "puesto")
        self.assertEqual(self.profile.kind_of("UNIDADTRANSFDISTRIBUCION"), "unidad")
        self.assertEqual(self.profile.kind_of("TramoDistribucionAereo"), "tramo")

    def test_clases_fuente_del_circuito(self):
        self.assertTrue(self.profile.is_source_class("PuestoProteccionDinamico"))
        self.assertFalse(self.profile.is_source_class("PuntoCarga"))

    def test_dominios_variables_por_unidad_de_negocio(self):
        # El catalogo advierte de estos tres: nunca deben fijarse en codigo.
        self.assertEqual(
            sorted(self.profile.variable_domains),
            ["Codigo Alimentador", "Numero Estacion", "Subestacion"],
        )

    def test_subtipos_documentados(self):
        subtypes = self.profile.subtype_names("Barra")
        self.assertEqual(subtypes[1], "Barra Baja Tension")
        self.assertEqual(subtypes[2], "Barra Media Tension")

    def test_pestanas_del_formulario(self):
        self.assertEqual(self.profile.form_group_of("Barra", "PROVINCIA"), "Ubicacion")
        self.assertEqual(self.profile.form_group_of("Barra", "OBJECTID"), "Sistema")
        self.assertEqual(
            self.profile.form_group_of("TramoDistribucionAereo", "VOLTAJE"),
            "Datos obligatorios",
        )


class GenericProfileTest(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("generico")

    def test_no_conoce_ninguna_clase(self):
        self.assertFalse(self.profile.knows("Barra"))
        self.assertIsNone(self.profile.group_of("Barra"))
        self.assertEqual(self.profile.class_alias("Barra"), "Barra")

    def test_clasifica_por_nombre(self):
        self.assertEqual(self.profile.category_of("X", "GLOBALID"), CATEGORY_SYSTEM)
        self.assertEqual(
            self.profile.category_of("X", "ENABLED"), CATEGORY_CONNECTIVITY
        )
        self.assertEqual(self.profile.category_of("X", "COSA"), CATEGORY_OTHER)

    def test_perfil_vacio_es_utilizable(self):
        profile = Profile()
        self.assertEqual(profile.id, "generico")
        self.assertEqual(profile.category_of("A", "B"), CATEGORY_OTHER)

    def test_perfil_inexistente_falla_con_mensaje_util(self):
        with self.assertRaises(ValueError) as context:
            load_profile("no_existe")
        self.assertIn("cnel_ep", str(context.exception))


class LayerInfoTest(unittest.TestCase):
    """El modelo neutro tiene que resolver bien los dominios por subtipo."""

    def setUp(self):
        self.layer = LayerInfo(
            name="Barra",
            geometry_type="Polyline",
            subtype_field="SUBTIPO",
            fields=[
                FieldInfo("SUBTIPO", "Integer"),
                FieldInfo("VOLTAJE", "Integer", domain="Voltaje BT"),
                FieldInfo("CODIGO", "String", length=10),
            ],
            subtypes=[
                SubtypeInfo(1, "BT", domains={"VOLTAJE": "Voltaje BT"}),
                SubtypeInfo(
                    2, "MT", is_default=True, domains={"VOLTAJE": "Voltaje MT"}
                ),
                SubtypeInfo(3, "AT", domains={"VOLTAJE": "Voltaje AT"}),
            ],
        )

    def test_dominio_segun_subtipo(self):
        self.assertEqual(self.layer.domain_for("VOLTAJE", 2), "Voltaje MT")
        self.assertEqual(self.layer.domain_for("VOLTAJE", 3), "Voltaje AT")
        self.assertEqual(self.layer.domain_for("VOLTAJE"), "Voltaje BT")

    def test_union_de_dominios(self):
        self.assertEqual(
            self.layer.all_domains_for("VOLTAJE"),
            ["Voltaje BT", "Voltaje MT", "Voltaje AT"],
        )
        self.assertEqual(self.layer.all_domains_for("CODIGO"), [])

    def test_busqueda_de_campos_insensible_a_mayusculas(self):
        self.assertIsNotNone(self.layer.field("voltaje"))
        self.assertIsNone(self.layer.field("inexistente"))


if __name__ == "__main__":
    unittest.main()
