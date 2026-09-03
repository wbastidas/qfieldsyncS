# -*- coding: utf-8 -*-
"""Pruebas contra una geodatabase corporativa (Oracle 11gR2 con ArcSDE).

Lo que distingue a una geodatabase corporativa de una File Geodatabase, desde
el punto de vista de qfieldESRI, no es el motor: es **como se llaman las
clases**. Oracle las guarda en mayusculas y ArcSDE las califica con el usuario
propietario, asi que la clase que en la ``.gdb`` es ``EstructuraSoporte``
llega como ``SIGELEC.ESTRUCTURASOPORTE``. Y con otra conexion, como
``SDE.ESTRUCTURASOPORTE``.

Si esa etiqueta se tratara como identidad, contra una base corporativa
fallarian en cadena el perfil (ninguna clase reconocida), el ambito de
exportacion, la simbologia y —lo mas grave— la sincronizacion de vuelta: el
material capturado en campo no encontraria su clase de destino.

Estas pruebas ejercen el camino completo con el mismo modelo de siempre, pero
nombrado como lo nombraria Oracle.
"""

import io
import os
import shutil
import tempfile
import unittest

from qfieldesri.core.checker import WorkspaceChecker
from qfieldesri.core.config import LayerAction, PackagingConfig
from qfieldesri.core.model import WorkspaceInfo
from qfieldesri.core.naming import find, normalize, same_class, short_name
from qfieldesri.core.packager import Packager
from qfieldesri.core.scope import Scope, ScopeKind, ScopeResolver
from qfieldesri.core.synchronizer import Change, SyncError, Synchronizer
from qfieldesri.demo import build_enterprise_reader, build_reader, qualify
from qfieldesri.profiles import load_profile
from qfieldesri.utils.sqlite_gpkg import connect

HERE_ROOT = os.path.dirname(os.path.abspath(__file__))


class NamingTest(unittest.TestCase):
    def test_nombre_corto(self):
        self.assertEqual(short_name("SIGELEC.BARRA"), "BARRA")
        self.assertEqual(short_name("sde.DBO.Barra"), "Barra")
        self.assertEqual(short_name("Barra"), "Barra")
        self.assertEqual(short_name(None), "")

    def test_la_misma_clase_con_otra_etiqueta(self):
        self.assertTrue(same_class("SIGELEC.BARRA", "Barra"))
        self.assertTrue(same_class("SIGELEC.BARRA", "SDE.Barra"))
        self.assertFalse(same_class("SIGELEC.BARRA", "SIGELEC.POSTE"))
        self.assertFalse(same_class("", "Barra"))

    def test_la_coincidencia_exacta_manda(self):
        """Con dos esquemas cargados hay que respetar el que se pidio."""
        names = ["SIGELEC.BARRA", "SDE.BARRA"]
        self.assertEqual(find(names, "SDE.BARRA"), "SDE.BARRA")
        self.assertEqual(find(names, "Barra"), "SIGELEC.BARRA")
        self.assertIsNone(find(names, "Poste"))

    def test_normalizacion(self):
        self.assertEqual(normalize("SIGELEC.BARRA"), "barra")


class EnterpriseWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.reader = build_enterprise_reader()
        self.workspace = self.reader.workspace_info
        self.profile = load_profile("cnel_ep")

    def test_la_demostracion_llega_calificada(self):
        self.assertTrue(self.workspace.is_enterprise)
        self.assertTrue(self.workspace.is_versioned)
        for layer in self.workspace.layers:
            self.assertIn(".", layer.name)
            self.assertEqual(layer.name, layer.name.upper())

    def test_se_encuentra_la_capa_con_cualquiera_de_los_dos_nombres(self):
        for name in (
            "EstructuraSoporte",
            "SIGELEC.ESTRUCTURASOPORTE",
            "estructurasoporte",
        ):
            layer = self.workspace.layer(name)
            self.assertIsNotNone(layer, name)
            self.assertEqual(layer.name, qualify("EstructuraSoporte"))

    def test_el_perfil_reconoce_las_clases_calificadas(self):
        for layer in self.workspace.layers:
            self.assertTrue(
                self.profile.knows(layer.name),
                "el perfil deberia reconocer %s" % layer.name,
            )
        self.assertEqual(
            self.profile.kind_of(qualify("PuestoTransfDistribucion")), "puesto"
        )

    def test_las_relaciones_siguen_encontrando_sus_clases(self):
        related = self.workspace.relationships_of(qualify("PuestoTransfDistribucion"))
        self.assertEqual(len(related), 1)
        # Y tambien preguntando por el nombre corto.
        self.assertEqual(
            len(self.workspace.relationships_of("PuestoTransfDistribucion")), 1
        )


class EnterpriseScopeTest(unittest.TestCase):
    def setUp(self):
        self.reader = build_enterprise_reader()
        self.workspace = self.reader.workspace_info
        self.resolver = ScopeResolver(
            self.workspace, load_profile("cnel_ep"), self.reader
        )

    def test_ambito_por_alimentador(self):
        plan = self.resolver.resolve(Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]))
        by_attribute = [
            name
            for name, filter_ in plan.filters.items()
            if filter_.method == filter_.BY_ATTRIBUTE
        ]
        self.assertIn(qualify("EstructuraSoporte"), by_attribute)

    def test_la_unidad_sigue_a_su_puesto(self):
        plan = self.resolver.resolve(Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"]))
        unidad = plan.filters[qualify("UNIDADTRANSFDISTRIBUCION")]
        self.assertEqual(unidad.method, unidad.BY_RELATIONSHIP)
        self.assertEqual(unidad.parent, qualify("PuestoTransfDistribucion"))

    def test_la_subestacion_se_expande_por_circuitofuente(self):
        kind, values = self.resolver.expand_values(
            Scope(ScopeKind.SUBESTACION, ["04BH07"])
        )
        self.assertEqual(kind, ScopeKind.ALIMENTADOR)
        self.assertEqual(values, ["04BH070T11"])


class EnterprisePackagingTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.reader = build_enterprise_reader()
        self.config = PackagingConfig(
            workspace=self.reader.workspace_info.path,
            output_dir=self.directory,
            project_name="corporativa",
        )

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_las_tablas_del_paquete_pierden_el_esquema(self):
        result = Packager(self.reader, self.config).run()
        connection = connect(result.gpkg_file)
        try:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM gpkg_contents ORDER BY table_name"
                )
            ]
        finally:
            connection.close()
        # En el dispositivo nadie quiere ver 'SIGELEC.ESTRUCTURASOPORTE'.
        self.assertIn("ESTRUCTURASOPORTE", tables)
        for table in tables:
            self.assertNotIn(".", table)

    def test_el_manifiesto_recuerda_el_nombre_del_servidor(self):
        result = Packager(self.reader, self.config).run()
        classes = [entry["source_class"] for entry in result.manifest["layers"]]
        self.assertIn(qualify("EstructuraSoporte"), classes)

    def test_la_configuracion_admite_el_nombre_corto(self):
        """El usuario escribe 'EstructuraSoporte', no 'SIGELEC.ESTRUCTURASOPORTE'."""
        self.config.layer_config("EstructuraSoporte").action = LayerAction.READ_ONLY
        result = Packager(self.reader, self.config).run()
        entry = next(
            item
            for item in result.manifest["layers"]
            if item["source_class"] == qualify("EstructuraSoporte")
        )
        self.assertEqual(entry["action"], LayerAction.READ_ONLY)
        self.assertTrue(entry["read_only"])

    def test_la_verificacion_avisa_del_versionado(self):
        """Quien sincroniza tiene que saber donde van a quedar sus cambios."""
        info = self.reader.describe_workspace()
        texto = "\n".join(
            feedback.format()
            for feedback in WorkspaceChecker(info, self.config).check().feedbacks
        )
        self.assertIn("geodatabase corporativa", texto)
        self.assertIn("Reconcile", texto)

    def test_la_verificacion_avisa_cuando_no_hay_version_que_revisar(self):
        info = build_enterprise_reader(versioned=False).describe_workspace()
        checked = WorkspaceChecker(info, self.config).check()
        checks = [feedback.check for feedback in checked.feedbacks]
        self.assertIn("sin_versionar", checks)

    def test_la_verificacion_no_da_la_geodatabase_por_desconocida(self):
        info = self.reader.describe_workspace()
        result = WorkspaceChecker(info, self.config).check()
        texto = "\n".join(feedback.format() for feedback in result.feedbacks)
        self.assertNotIn("no estan en el perfil", texto)

    def test_la_simbologia_del_perfil_se_aplica_a_las_clases_calificadas(self):
        result = Packager(self.reader, self.config).run()
        # El estilo del perfil se declara con nombres cortos; la geodatabase
        # corporativa los da calificados y aun asi tiene que casar.
        self.assertIn("archivo de estilo", result.symbology_description)


class EnterpriseSyncTest(unittest.TestCase):
    """La vuelta de campo a la base corporativa, que es lo que no puede fallar."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.reader = build_enterprise_reader()
        config = PackagingConfig(
            workspace=self.reader.workspace_info.path,
            output_dir=self.directory,
            project_name="corporativa",
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

    def test_una_modificacion_vuelve_a_su_clase_calificada(self):
        self._edit(["UPDATE ESTRUCTURASOPORTE SET MATERIAL='ACERO' WHERE fid=1"])
        synchronizer = Synchronizer(self.result.project_dir, self.reader)
        report = synchronizer.detect()
        self.assertEqual(len(report.of_kind(Change.UPDATE)), 1)

        synchronizer.apply(report)
        self.assertEqual(len(self.reader.updated), 1)
        layer_name = self.reader.updated[0][0]
        self.assertEqual(layer_name, qualify("EstructuraSoporte"))
        self.assertFalse(report.errors)

    def test_recuerda_reconciliar_y_publicar(self):
        """Sin publicar, lo capturado en campo no llega a DEFAULT."""
        self._edit(["UPDATE ESTRUCTURASOPORTE SET MATERIAL='ACERO' WHERE fid=1"])
        report = Synchronizer(self.result.project_dir, self.reader).apply()
        self.assertIn("Reconcile", report.format())

    def test_un_alta_vuelve_a_su_clase_calificada(self):
        self._edit(
            [
                "INSERT INTO ESTRUCTURASOPORTE (ALIMENTADORID, CODIGOESTRUCTURA, "
                "MATERIAL) VALUES ('04BH070T11', 'GYE-P-9999', 'HORMIGON')"
            ]
        )
        synchronizer = Synchronizer(self.result.project_dir, self.reader)
        report = synchronizer.detect()
        self.assertEqual(len(report.of_kind(Change.INSERT)), 1)
        synchronizer.apply(report)
        self.assertEqual(self.reader.inserted[0][0], qualify("EstructuraSoporte"))

    def test_sincroniza_aunque_cambie_el_propietario_del_esquema(self):
        """El paquete se genero con SIGELEC y se sincroniza conectado como SDE.

        Es un caso real: se empaqueta desde el equipo de campo con una conexion
        y se aplica desde la oficina con otra. La clase es la misma.
        """
        self._edit(["UPDATE ESTRUCTURASOPORTE SET MATERIAL='ACERO' WHERE fid=1"])
        otra_conexion = build_enterprise_reader(owner="SDE")
        report = Synchronizer(self.result.project_dir, otra_conexion).detect()
        self.assertEqual(len(report.of_kind(Change.UPDATE)), 1)
        self.assertFalse(report.errors)

    def test_la_sesion_de_edicion_se_abre_en_modo_versionado(self):
        """Los datos de la demostracion estan registrados como versionados."""
        self._edit(["UPDATE ESTRUCTURASOPORTE SET MATERIAL='ACERO' WHERE fid=1"])
        Synchronizer(self.result.project_dir, self.reader).apply()
        self.assertEqual(self.reader.editing_calls[0], ("start", True))
        self.assertEqual(self.reader.editing_calls[-1], ("stop", True))

    def test_una_corporativa_sin_versionar_abre_la_sesion_de_otra_forma(self):
        """Es el error que ArcGIS no perdona: el modo tiene que coincidir."""
        directory = tempfile.mkdtemp()
        try:
            reader = build_enterprise_reader(versioned=False)
            config = PackagingConfig(
                workspace=reader.workspace_info.path,
                output_dir=directory,
                project_name="sinversionar",
            )
            result = Packager(reader, config).run()
            connection = connect(result.gpkg_file)
            connection.execute(
                "UPDATE ESTRUCTURASOPORTE SET MATERIAL='ACERO' WHERE fid=1"
            )
            connection.commit()
            connection.close()

            Synchronizer(result.project_dir, reader).apply()
            self.assertEqual(reader.editing_calls[0], ("start", False))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_en_una_file_geodatabase_no_se_pregunta_por_el_versionado(self):
        from qfieldesri.demo import build_reader

        directory = tempfile.mkdtemp()
        try:
            reader = build_reader()
            config = PackagingConfig(
                workspace="demo.gdb", output_dir=directory, project_name="local"
            )
            result = Packager(reader, config).run()
            connection = connect(result.gpkg_file)
            connection.execute(
                "UPDATE EstructuraSoporte SET MATERIAL='ACERO' WHERE fid=1"
            )
            connection.commit()
            connection.close()

            Synchronizer(result.project_dir, reader).apply()
            self.assertEqual(reader.editing_calls[0], ("start", True))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_un_fallo_puntual_queda_registrado_y_no_arrastra_al_resto(self):
        self._edit(["UPDATE ESTRUCTURASOPORTE SET MATERIAL='ACERO' WHERE fid=1"])
        synchronizer = Synchronizer(self.result.project_dir, self.reader)
        report = synchronizer.detect()

        def bloqueado(*_args, **_kwargs):
            raise RuntimeError("registro bloqueado por otro editor")

        self.reader.update_feature = bloqueado
        synchronizer.apply(report)
        self.assertFalse(report.changes[0].applied)
        self.assertIn("bloqueado", report.errors[0])
        # La sesion se cierra guardando: lo que si se pudo aplicar se guarda.
        self.assertEqual(self.reader.editing_calls[-1], ("stop", True))

    def test_si_la_base_rechaza_guardar_no_queda_nada_aplicado(self):
        """El caso feo de una corporativa: la sesion no cierra."""
        self._edit(["UPDATE ESTRUCTURASOPORTE SET MATERIAL='ACERO' WHERE fid=1"])
        synchronizer = Synchronizer(self.result.project_dir, self.reader)
        report = synchronizer.detect()

        original = self.reader.stop_editing

        def rechaza(save=True):
            if save:
                raise RuntimeError("version en conflicto con DEFAULT")
            return original(save=False)

        self.reader.stop_editing = rechaza
        self.assertRaises(SyncError, synchronizer.apply, report)
        self.assertFalse(any(change.applied for change in report.changes))
        self.assertTrue(
            any("no se aplico ningun cambio" in error for error in report.errors)
        )


class SmokeTest(unittest.TestCase):
    """El guion de ``tools/prueba_ida_y_vuelta.py``, como prueba.

    Comprueba lo que el usuario quiere saber antes de conectar produccion: que
    el ciclo entero cierra igual contra una File Geodatabase y contra la
    corporativa, sin que el nombre del esquema cambie ni un resultado.
    """

    def test_los_dos_origenes_dan_el_mismo_resultado(self):
        import sys

        sys.path.insert(0, os.path.dirname(HERE_ROOT))
        from tools.prueba_ida_y_vuelta import run

        directory = tempfile.mkdtemp()
        stdout = sys.stdout
        try:
            # El guion esta hecho para leerse en pantalla; aqui solo interesa
            # lo que devuelve.
            with io.open(os.devnull, "w") as devnull:
                sys.stdout = devnull
                local = run(build_reader(), os.path.join(directory, "local"), "local")
                corporate = run(
                    build_enterprise_reader(),
                    os.path.join(directory, "sde"),
                    "corporativa",
                )
        finally:
            sys.stdout = stdout
            shutil.rmtree(directory, ignore_errors=True)

        self.assertEqual(local["entidades"], corporate["entidades"])
        self.assertEqual(local["escrituras"], corporate["escrituras"])
        self.assertEqual(local["escrituras"], (1, 1, 1))
        self.assertEqual(local["errores"], [])
        self.assertEqual(corporate["errores"], [])


class WorkspaceTypeTest(unittest.TestCase):
    def test_la_file_geodatabase_sigue_siendo_file_geodatabase(self):
        from qfieldesri.demo import build_reader

        workspace = build_reader().workspace_info
        self.assertEqual(workspace.workspace_type, WorkspaceInfo.FILE_GDB)
        self.assertFalse(workspace.is_enterprise)
        self.assertFalse(workspace.is_versioned)

    def test_el_paquete_de_una_gdb_y_el_de_una_sde_traen_las_mismas_clases(self):
        from qfieldesri.demo import build_reader

        directory = tempfile.mkdtemp()
        try:
            files = []
            for reader, name in (
                (build_reader(), "local"),
                (build_enterprise_reader(), "corporativa"),
            ):
                config = PackagingConfig(
                    workspace=reader.workspace_info.path,
                    output_dir=directory,
                    project_name=name,
                )
                result = Packager(reader, config).run()
                files.append(sorted(result.layer_counts.values()))
                self.assertTrue(os.path.isfile(result.gpkg_file))
            self.assertEqual(files[0], files[1])
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
