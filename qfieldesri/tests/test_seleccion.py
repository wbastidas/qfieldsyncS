# -*- coding: utf-8 -*-
"""Pruebas de la seleccion de clases: que se exporta y que no.

El ambito decide **que trozo** de la red viaja; esto decide **que clases**.
Son dos preguntas distintas y se combinan: "los clientes del alimentador
04BH070T11" es un ambito por alimentador y una seleccion de clientes.
"""

import shutil
import tempfile
import unittest

from qfieldesri.core.config import PackagingConfig
from qfieldesri.core.packager import Packager
from qfieldesri.core.scope import Scope, ScopeKind
from qfieldesri.core.selection import (
    ClassSet,
    Selection,
    SelectionError,
    SelectionResolver,
)
from qfieldesri.demo import build_enterprise_reader, build_reader, qualify
from qfieldesri.profiles import load_profile


class SelectionModelTest(unittest.TestCase):
    def test_sin_nada_elegido_se_exporta_todo(self):
        self.assertTrue(Selection().is_empty)
        self.assertFalse(Selection(sets=["postes"]).is_empty)
        self.assertFalse(Selection(classes=["Barra"]).is_empty)

    def test_ida_y_vuelta_a_diccionario(self):
        original = Selection(
            sets=["clientes"], classes=["Barra"], exclude=["PuntoCarga"]
        )
        copy = Selection.from_dict(original.to_dict())
        self.assertEqual(copy.sets, ["clientes"])
        self.assertEqual(copy.classes, ["Barra"])
        self.assertEqual(copy.exclude, ["PuntoCarga"])
        self.assertTrue(copy.include_related)


class AvailableSetsTest(unittest.TestCase):
    def setUp(self):
        self.reader = build_reader()
        self.resolver = SelectionResolver(
            self.reader.workspace_info, load_profile("cnel_ep")
        )

    def test_el_primero_es_toda_la_geodatabase(self):
        sets = self.resolver.available_sets()
        self.assertEqual(sets[0].id, "todo")
        self.assertEqual(len(sets[0]), len(self.reader.workspace_info.layers))

    def test_no_se_ofrecen_conjuntos_vacios(self):
        """Ofrecer "solo alumbrado" sin luminarias solo genera un paquete vacio."""
        identifiers = [item.id for item in self.resolver.available_sets()]
        self.assertNotIn("alumbrado", identifiers)
        self.assertNotIn("clientes", identifiers)
        self.assertIn("transformadores", identifiers)

    def test_hay_conjuntos_por_geometria_para_cualquier_modelo(self):
        by_source = {}
        for item in self.resolver.available_sets():
            by_source.setdefault(item.source, []).append(item.id)
        self.assertIn("puntos", by_source[ClassSet.GEOMETRY])
        self.assertIn("lineas", by_source[ClassSet.GEOMETRY])
        self.assertIn("tablas", by_source[ClassSet.GEOMETRY])

    def test_una_geodatabase_sin_perfil_conserva_los_de_geometria(self):
        resolver = SelectionResolver(
            self.reader.workspace_info, load_profile("generico")
        )
        identifiers = [item.id for item in resolver.available_sets()]
        self.assertEqual(identifiers[0], "todo")
        self.assertIn("puntos", identifiers)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.reader = build_reader()
        self.resolver = SelectionResolver(
            self.reader.workspace_info, load_profile("cnel_ep")
        )

    def test_un_conjunto_deja_fuera_lo_demas(self):
        plan = self.resolver.resolve(Selection(sets=["transformadores"]))
        self.assertTrue(plan.keeps("PuestoTransfDistribucion"))
        self.assertFalse(plan.keeps("TramoDistribucionAereo"))
        self.assertIn("TramoDistribucionAereo", plan.excluded)

    def test_lo_que_cuelga_de_una_clase_elegida_se_arrastra(self):
        """Un puesto sin sus transformadores no sirve para revisarlo."""
        plan = self.resolver.resolve(Selection(classes=["PuestoTransfDistribucion"]))
        self.assertTrue(plan.keeps("UNIDADTRANSFDISTRIBUCION"))
        self.assertEqual(
            plan.included["UNIDADTRANSFDISTRIBUCION"],
            "Relacionada con una clase elegida",
        )

    def test_se_puede_pedir_que_no_se_arrastre(self):
        plan = self.resolver.resolve(
            Selection(classes=["PuestoTransfDistribucion"], include_related=False)
        )
        self.assertFalse(plan.keeps("UNIDADTRANSFDISTRIBUCION"))

    def test_quitar_una_clase_manda_sobre_el_conjunto(self):
        plan = self.resolver.resolve(
            Selection(sets=["transformadores"], exclude=["UNIDADTRANSFDISTRIBUCION"])
        )
        self.assertTrue(plan.keeps("PuestoTransfDistribucion"))
        self.assertFalse(plan.keeps("UNIDADTRANSFDISTRIBUCION"))
        self.assertEqual(plan.excluded["UNIDADTRANSFDISTRIBUCION"], "Quitada a mano")

    def test_una_clase_que_no_existe_se_avisa_y_no_rompe(self):
        plan = self.resolver.resolve(Selection(classes=["NoExiste"]))
        self.assertTrue(any("NoExiste" in note for note in plan.notes))

    def test_un_conjunto_inventado_falla_con_la_lista_de_los_buenos(self):
        try:
            self.resolver.resolve(Selection(sets=["inventado"]))
        except SelectionError as error:
            self.assertIn("inventado", str(error))
            self.assertIn("transformadores", str(error))
        else:
            self.fail("deberia haber avisado del conjunto inexistente")

    def test_la_explicacion_dice_que_entra_y_que_no(self):
        texto = self.resolver.resolve(Selection(sets=["transformadores"])).describe()
        self.assertIn("Transformadores", texto)
        self.assertIn("Fuera del paquete", texto)

    def test_el_nombre_corto_vale_en_una_corporativa(self):
        """El usuario escribe 'PuestoTransfDistribucion', Oracle dice otra cosa."""
        reader = build_enterprise_reader()
        resolver = SelectionResolver(reader.workspace_info, load_profile("cnel_ep"))
        plan = resolver.resolve(Selection(classes=["PuestoTransfDistribucion"]))
        self.assertTrue(plan.keeps(qualify("PuestoTransfDistribucion")))
        self.assertTrue(plan.keeps(qualify("UNIDADTRANSFDISTRIBUCION")))


class PackagingWithSelectionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.reader = build_reader()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _package(self, selection=None, scope=None):
        config = PackagingConfig(
            workspace="demo.gdb",
            output_dir=self.directory,
            project_name="p",
            selection=selection,
            scope=scope,
        )
        return Packager(self.reader, config).run()

    def test_sin_seleccion_se_empaqueta_todo(self):
        result = self._package()
        self.assertEqual(
            len(result.layer_counts), len(self.reader.workspace_info.layers)
        )
        self.assertEqual(result.selection_description, "")

    def test_solo_el_conjunto_elegido_llega_al_paquete(self):
        result = self._package(Selection(sets=["transformadores"]))
        self.assertEqual(
            sorted(result.layer_counts),
            ["PuestoTransfDistribucion", "UNIDADTRANSFDISTRIBUCION"],
        )
        self.assertIn("Transformadores", result.selection_description)

    def test_lo_desmarcado_no_vuelve_por_la_puerta_de_atras(self):
        """Si se quita la Unidad a mano, el arrastre no puede reponerla."""
        result = self._package(
            Selection(
                classes=["PuestoTransfDistribucion"],
                include_related=False,
            )
        )
        self.assertEqual(sorted(result.layer_counts), ["PuestoTransfDistribucion"])

    def test_la_seleccion_y_el_ambito_se_combinan(self):
        """ "Los transformadores del alimentador 04BH070T11", no una cosa u otra."""
        result = self._package(
            selection=Selection(sets=["transformadores"]),
            scope=Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]),
        )
        self.assertEqual(
            sorted(result.layer_counts),
            ["PuestoTransfDistribucion", "UNIDADTRANSFDISTRIBUCION"],
        )
        self.assertTrue(result.scope_description)
        self.assertTrue(result.selection_description)

    def test_el_proyecto_solo_declara_las_capas_exportadas(self):
        import io as _io

        result = self._package(Selection(sets=["transformadores"]))
        with _io.open(result.project_file, encoding="utf-8") as handle:
            project = handle.read()
        self.assertIn("PuestoTransfDistribucion", project)
        self.assertNotIn("TramoDistribucionAereo", project)


if __name__ == "__main__":
    unittest.main()
