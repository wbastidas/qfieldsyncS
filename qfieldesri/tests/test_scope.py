# -*- coding: utf-8 -*-
"""Pruebas del ambito de exportacion.

Lo que se comprueba aqui no es solo que el filtro salga bien escrito, sino que
**no se quede fuera del paquete material que si debe viajar**: las Unidades de
un Puesto exportado son el caso critico, porque no tienen campo de alimentador.
"""

import shutil
import sqlite3
import tempfile
import unittest

from qfieldesri.core.config import PackagingConfig
from qfieldesri.core.packager import Packager
from qfieldesri.core.scope import (
    IN_CHUNK_SIZE,
    LayerFilter,
    Scope,
    ScopeError,
    ScopeKind,
    ScopeResolver,
    combine,
)
from qfieldesri.demo import build_reader
from qfieldesri.profiles import load_profile


class ScopeBasicsTest(unittest.TestCase):
    def test_ambito_vacio(self):
        self.assertTrue(Scope().is_empty)
        self.assertTrue(Scope(ScopeKind.ALIMENTADOR).is_empty)
        self.assertFalse(Scope(ScopeKind.ALIMENTADOR, ["A"]).is_empty)
        self.assertEqual(Scope().label(), "Geodatabase completa")

    def test_ambito_espacial(self):
        scope = Scope(ScopeKind.POLIGONO)
        self.assertTrue(scope.is_empty)
        self.assertTrue(
            Scope(ScopeKind.POLIGONO, polygon_wkt="POLYGON(...)").is_spatial
        )
        self.assertFalse(Scope(ScopeKind.POLIGONO, polygon_layer="SECTORES").is_empty)

    def test_etiqueta_resume_muchos_valores(self):
        scope = Scope(ScopeKind.ALIMENTADOR, ["A%d" % i for i in range(8)])
        self.assertIn("(+3)", scope.label())

    def test_serializacion(self):
        scope = Scope(ScopeKind.CANTON, ["0901"], follow_relationships=False)
        again = Scope.from_dict(scope.to_dict())
        self.assertEqual(again.kind, ScopeKind.CANTON)
        self.assertEqual(again.values, ["0901"])
        self.assertFalse(again.follow_relationships)


class LayerFilterTest(unittest.TestCase):
    def test_clausula_in(self):
        clauses = LayerFilter(
            "Barra", LayerFilter.BY_ATTRIBUTE, field="ALIMENTADORID", values=["A", "B"]
        ).where_clauses()
        self.assertEqual(clauses, ["ALIMENTADORID IN ('A', 'B')"])

    def test_comillas_escapadas(self):
        clauses = LayerFilter(
            "X", LayerFilter.BY_ATTRIBUTE, field="F", values=["O'BRIEN"]
        ).where_clauses()
        self.assertEqual(clauses, ["F IN ('O''BRIEN')"])

    def test_troceado_de_listas_largas(self):
        values = ["V%04d" % index for index in range(IN_CHUNK_SIZE + 10)]
        clauses = LayerFilter(
            "X", LayerFilter.BY_ATTRIBUTE, field="F", values=values
        ).where_clauses()
        self.assertEqual(len(clauses), 2)
        self.assertIn("V0000", clauses[0])
        self.assertIn("V0909", clauses[1])

    def test_sin_valores_no_devuelve_nada(self):
        # Un filtro resuelto pero vacio debe vaciar la clase, nunca exportarla
        # entera por descuido.
        clauses = LayerFilter(
            "X", LayerFilter.BY_ATTRIBUTE, field="F", values=[]
        ).where_clauses()
        self.assertEqual(clauses, ["1 = 0"])

    def test_metodos_sin_clausula(self):
        self.assertEqual(LayerFilter("X", LayerFilter.UNFILTERED).where_clauses(), [])
        self.assertEqual(LayerFilter("X", LayerFilter.BY_GEOMETRY).where_clauses(), [])

    def test_delimitador_de_campo(self):
        clauses = LayerFilter(
            "X", LayerFilter.BY_ATTRIBUTE, field="F", values=["A"]
        ).where_clauses(delimit=lambda name: '"%s"' % name)
        self.assertEqual(clauses, ["\"F\" IN ('A')"])

    def test_combinacion_con_el_filtro_del_usuario(self):
        self.assertEqual(combine("A = 1", "B = 2"), "(A = 1) AND (B = 2)")
        self.assertEqual(combine(None, "B = 2"), "B = 2")
        self.assertEqual(combine("A = 1", None), "A = 1")
        self.assertIsNone(combine(None, None))


class ScopeResolverTest(unittest.TestCase):
    def setUp(self):
        self.reader = build_reader()
        self.workspace = self.reader.describe_workspace()
        self.profile = load_profile("cnel_ep")
        self.resolver = ScopeResolver(self.workspace, self.profile, self.reader)

    def _plan(self, scope):
        return self.resolver.resolve(scope, self.workspace.layers)

    def test_sin_ambito_no_filtra_nada(self):
        plan = self._plan(Scope())
        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.filters, {})

    def test_por_alimentador_usa_el_campo_de_la_clase(self):
        plan = self._plan(Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]))
        poste = plan.filter_for("EstructuraSoporte")
        self.assertEqual(poste.method, LayerFilter.BY_ATTRIBUTE)
        self.assertEqual(poste.field, "ALIMENTADORID")
        self.assertEqual(poste.values, ["04BH070T11"])

    def test_la_unidad_hereda_del_puesto(self):
        # UNIDADTRANSFDISTRIBUCION no tiene campo de alimentador: tiene que
        # seguir a su Puesto o el tecnico se queda sin los transformadores.
        plan = self._plan(Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]))
        unidad = plan.filter_for("UNIDADTRANSFDISTRIBUCION")
        self.assertEqual(unidad.method, LayerFilter.BY_RELATIONSHIP)
        self.assertEqual(unidad.parent, "PuestoTransfDistribucion")
        self.assertEqual(unidad.field, "PUESTOTRANSFDISTGLOBALID")
        self.assertEqual(unidad.parent_field, "GLOBALID")

    def test_se_puede_desactivar_el_arrastre_de_unidades(self):
        plan = self._plan(
            Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"], follow_relationships=False)
        )
        self.assertEqual(
            plan.filter_for("UNIDADTRANSFDISTRIBUCION").method,
            LayerFilter.UNFILTERED,
        )

    def test_subestacion_se_expande_a_sus_alimentadores(self):
        kind, values = self.resolver.expand_values(
            Scope(ScopeKind.SUBESTACION, ["04SM32"])
        )
        self.assertEqual(kind, ScopeKind.ALIMENTADOR)
        self.assertEqual(values, ["04SM320T22"])

    def test_subestacion_deja_constancia_de_la_expansion(self):
        plan = self._plan(Scope(ScopeKind.SUBESTACION, ["04BH07"]))
        self.assertTrue(any("CIRCUITOFUENTE" in note for note in plan.notes))
        self.assertEqual(plan.filter_for("EstructuraSoporte").values, ["04BH070T11"])

    def test_subestacion_sin_alimentadores_avisa(self):
        plan = self._plan(Scope(ScopeKind.SUBESTACION, ["NO_EXISTE"]))
        self.assertTrue(any("vacio" in note for note in plan.notes))
        self.assertEqual(plan.filter_for("EstructuraSoporte").values, [])

    def test_subestacion_sin_la_tabla_falla_con_mensaje_util(self):
        self.workspace.layers = [
            layer for layer in self.workspace.layers if layer.name != "CIRCUITOFUENTE"
        ]
        with self.assertRaises(ScopeError) as context:
            self.resolver.expand_values(Scope(ScopeKind.SUBESTACION, ["04BH07"]))
        self.assertIn("CIRCUITOFUENTE", str(context.exception))

    def test_division_politica(self):
        plan = self._plan(Scope(ScopeKind.CANTON, ["0901"]))
        for name in ("EstructuraSoporte", "UNIDADTRANSFDISTRIBUCION", "CIRCUITOFUENTE"):
            filtro = plan.filter_for(name)
            self.assertEqual(filtro.method, LayerFilter.BY_ATTRIBUTE, name)
            self.assertEqual(filtro.field, "CANTON")

    def test_poligono_recorta_solo_lo_espacial(self):
        plan = self._plan(Scope(ScopeKind.POLIGONO, polygon_wkt="POLYGON EMPTY"))
        self.assertEqual(
            plan.filter_for("EstructuraSoporte").method, LayerFilter.BY_GEOMETRY
        )
        # La tabla no tiene geometria: sigue a su Puesto.
        self.assertEqual(
            plan.filter_for("UNIDADTRANSFDISTRIBUCION").method,
            LayerFilter.BY_RELATIONSHIP,
        )

    def test_poligono_de_una_capa_sin_motor_geometrico_falla(self):
        with self.assertRaises(ScopeError):
            self._plan(Scope(ScopeKind.POLIGONO, polygon_layer="SECTORES"))

    def test_la_descripcion_explica_cada_clase(self):
        plan = self._plan(Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]))
        text = plan.describe()
        self.assertIn("Alimentador", text)
        self.assertIn("Filtradas por atributo", text)
        self.assertIn("Filtradas por relacion con su Puesto", text)

    def test_valores_disponibles_salen_del_dominio(self):
        values = self.resolver.available_values(ScopeKind.ALIMENTADOR)
        self.assertEqual(len(values), 3)
        self.assertIn("04BH070T11", [code for code, _label in values])

    def test_valores_disponibles_solo_los_presentes(self):
        # El dominio trae tres alimentadores; el tramo solo usa uno.
        values = self.resolver.available_values(
            ScopeKind.ALIMENTADOR, only_present_in="TramoDistribucionAereo"
        )
        self.assertEqual([code for code, _label in values], ["04BH070T11"])

    def test_ambito_no_soportado_por_el_perfil(self):
        self.assertEqual(self.resolver.available_values("inexistente"), [])


class ScopePackagingTest(unittest.TestCase):
    """El ambito aplicado de punta a punta sobre el paquete generado."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.reader = build_reader()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _package(self, scope, **kwargs):
        config = PackagingConfig(
            workspace="demo.gdb",
            output_dir=self.directory,
            project_name="p_%s" % abs(hash(str(scope.to_dict()))),
            scope=scope,
            **kwargs,
        )
        return Packager(self.reader, config).run()

    def test_por_alimentador(self):
        result = self._package(Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]))
        # Los postes se repartieron entre tres alimentadores.
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 2)
        self.assertEqual(result.layer_counts["TramoDistribucionAereo"], 1)
        self.assertEqual(result.layer_counts["PuestoTransfDistribucion"], 1)
        # Las tres unidades del puesto exportado viajan con el.
        self.assertEqual(result.layer_counts["UNIDADTRANSFDISTRIBUCION"], 3)

    def test_alimentador_sin_puestos_no_arrastra_unidades(self):
        result = self._package(Scope(ScopeKind.ALIMENTADOR, ["04OR240T22"]))
        self.assertEqual(result.layer_counts["PuestoTransfDistribucion"], 0)
        self.assertEqual(result.layer_counts["UNIDADTRANSFDISTRIBUCION"], 0)
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 2)

    def test_por_subestacion(self):
        result = self._package(Scope(ScopeKind.SUBESTACION, ["04BH07"]))
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 2)
        self.assertEqual(result.layer_counts["UNIDADTRANSFDISTRIBUCION"], 3)

    def test_por_canton_se_lleva_todo_lo_del_canton(self):
        result = self._package(Scope(ScopeKind.CANTON, ["0901"]))
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 6)

    def test_canton_inexistente_deja_el_paquete_vacio(self):
        result = self._package(Scope(ScopeKind.CANTON, ["9999"]))
        self.assertEqual(result.total_features, 0)

    def test_el_ambito_queda_registrado_en_el_manifiesto(self):
        result = self._package(Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]))
        self.assertEqual(result.manifest["scope"]["kind"], "alimentador")
        self.assertEqual(result.manifest["scope"]["values"], ["04BH070T11"])
        self.assertIn("Alimentador", result.scope_description)

    def test_el_filtro_del_usuario_se_suma_al_ambito(self):
        from qfieldesri.core.config import LayerConfig

        config = PackagingConfig(
            workspace="demo.gdb",
            output_dir=self.directory,
            project_name="combinado",
            scope=Scope(ScopeKind.CANTON, ["0901"]),
        )
        config.layers["EstructuraSoporte"] = LayerConfig(
            "EstructuraSoporte", where_clause="MATERIAL = 'HORMIGON'"
        )
        result = Packager(self.reader, config).run()
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 6)

        config.layers["EstructuraSoporte"].where_clause = "MATERIAL = 'ACERO'"
        config.project_name = "combinado2"
        result = Packager(self.reader, config).run()
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 0)

    def test_sin_ambito_se_empaqueta_todo(self):
        result = self._package(Scope())
        self.assertEqual(result.layer_counts["EstructuraSoporte"], 6)
        self.assertEqual(result.scope_description, "")

    def test_el_geopackage_solo_contiene_lo_acotado(self):
        result = self._package(Scope(ScopeKind.ALIMENTADOR, ["04SM320T22"]))
        connection = sqlite3.connect(result.gpkg_file)
        feeders = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT ALIMENTADORID FROM EstructuraSoporte"
            )
        ]
        connection.close()
        self.assertEqual(feeders, ["04SM320T22"])


if __name__ == "__main__":
    unittest.main()
