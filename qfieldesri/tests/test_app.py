# -*- coding: utf-8 -*-
"""Pruebas de la aplicacion de escritorio y del lanzador.

Tkinter no siempre esta disponible donde corre la bateria de pruebas (y abrir
una ventana en una maquina sin pantalla no tendria sentido), asi que aqui se
prueba lo que si es logica propia y no depende de widgets: la traduccion de lo
elegido en la ventana a un ambito de exportacion, y la busqueda del Python de
ArcGIS. La construccion de la interfaz se deja para el uso real.
"""

import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _install_tkinter_stub():
    """Deja un Tkinter minimo en ``sys.modules`` si no hay uno real.

    Solo hace falta para poder *importar* el modulo de la aplicacion; no se
    construye ninguna ventana.
    """
    try:
        import tkinter as tk  # noqa: F401

        return False
    except ImportError:
        pass

    tkinter = types.ModuleType("tkinter")

    class _Widget(object):
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    for name in ("Frame", "Text", "Listbox", "StringVar", "BooleanVar", "Tk"):
        setattr(tkinter, name, type(name, (_Widget,), {}))

    ttk = types.ModuleType("tkinter.ttk")
    for name in (
        "Notebook",
        "Frame",
        "Label",
        "Entry",
        "Button",
        "Combobox",
        "Checkbutton",
        "Treeview",
        "Scrollbar",
        "Progressbar",
        "LabelFrame",
    ):
        setattr(ttk, name, type(name, (_Widget,), {}))

    for module_name, attributes in (
        ("tkinter.filedialog", ("askdirectory", "askopenfilename")),
        ("tkinter.messagebox", ("showinfo", "showwarning", "showerror", "askyesno")),
    ):
        module = types.ModuleType(module_name)
        for attribute in attributes:
            setattr(module, attribute, lambda *args, **kwargs: None)
        sys.modules[module_name] = module
        setattr(tkinter, module_name.split(".")[-1], module)

    tkinter.ttk = ttk
    sys.modules["tkinter"] = tkinter
    sys.modules["tkinter.ttk"] = ttk
    return True


_STUBBED = _install_tkinter_stub()

from qfieldesri import app  # noqa: E402
from qfieldesri.core.scope import ScopeKind  # noqa: E402


class ScopeFromWindowTest(unittest.TestCase):
    """Lo elegido en la ventana -> ambito de exportacion."""

    def test_sin_acotar(self):
        scope = app.build_scope(app.SCOPE_ALL)
        self.assertTrue(scope.is_empty)

    def test_por_alimentador(self):
        scope = app.build_scope(
            "Alimentador",
            selected=[
                "04BH070T11 - S/E BELO HORIZONTE",
                "04SM320T22 - S/E SAMANES",
            ],
        )
        self.assertEqual(scope.kind, ScopeKind.ALIMENTADOR)
        self.assertEqual(scope.values, ["04BH070T11", "04SM320T22"])

    def test_por_subestacion(self):
        scope = app.build_scope("Subestacion", selected=["04BH07 - S/E BELO"])
        self.assertEqual(scope.kind, ScopeKind.SUBESTACION)
        self.assertEqual(scope.values, ["04BH07"])

    def test_por_parroquia(self):
        scope = app.build_scope("Parroquia", selected=["090150"])
        self.assertEqual(scope.kind, ScopeKind.PARROQUIA)
        self.assertEqual(scope.values, ["090150"])

    def test_por_poligono_de_sector(self):
        scope = app.build_scope(
            "Poligono de sector",
            polygon_layer="SECTORES",
            polygon_where="CODIGO = '12'",
        )
        self.assertEqual(scope.kind, ScopeKind.POLIGONO)
        self.assertEqual(scope.polygon_layer, "SECTORES")
        self.assertEqual(scope.polygon_where, "CODIGO = '12'")

    def test_sin_arrastrar_unidades(self):
        scope = app.build_scope("Canton", selected=["0901"], follow_relationships=False)
        self.assertFalse(scope.follow_relationships)

    def test_elegir_ambito_sin_valores_avisa(self):
        with self.assertRaises(ValueError) as context:
            app.build_scope("Alimentador", selected=[])
        self.assertIn(app.SCOPE_ALL, str(context.exception))

    def test_poligono_sin_capa_avisa(self):
        with self.assertRaises(ValueError) as context:
            app.build_scope("Poligono de sector")
        self.assertIn("poligonos", str(context.exception))


class LabelsTest(unittest.TestCase):
    def test_etiquetas_de_ida_y_vuelta(self):
        for kind in ScopeKind.ALL:
            label = app.scope_label(kind)
            self.assertEqual(app.scope_kind_from_label(label), kind)

    def test_la_opcion_de_no_acotar_no_es_un_ambito(self):
        self.assertIsNone(app.scope_kind_from_label(app.SCOPE_ALL))
        self.assertIsNone(app.scope_kind_from_label(""))

    def test_los_modos_de_simbologia_son_de_archivo(self):
        """La ventana es un programa aparte: no ve el mapa abierto en ArcGIS.

        Ofrecer ahi "la simbologia del mapa actual" seria prometer algo que
        solo puede cumplir la caja de herramientas, que si corre dentro.
        """
        self.assertIn(app.SYMBOLOGY_AUTO, app.SYMBOLOGY_MODES)
        self.assertIn(app.SYMBOLOGY_FOLDER, app.SYMBOLOGY_MODES)
        self.assertIn(app.SYMBOLOGY_DOCUMENT, app.SYMBOLOGY_MODES)
        for mode in app.SYMBOLOGY_MODES:
            self.assertNotIn("abierto", mode)

    def test_formato_de_los_valores(self):
        self.assertEqual(app.format_value("04BH07", "S/E BELO"), "04BH07 - S/E BELO")
        self.assertEqual(app.format_value("0901", None), "0901")
        self.assertEqual(app.scope_code("04BH07 - S/E BELO"), "04BH07")


class LauncherTest(unittest.TestCase):
    def setUp(self):
        from qfieldesri import launcher

        self.launcher = launcher
        self._original = os.environ.get(launcher.ENV_VAR)

    def tearDown(self):
        if self._original is None:
            os.environ.pop(self.launcher.ENV_VAR, None)
        else:
            os.environ[self.launcher.ENV_VAR] = self._original

    def test_la_variable_de_entorno_manda(self):
        # Se apunta a un archivo que existe seguro: el propio interprete.
        os.environ[self.launcher.ENV_VAR] = sys.executable
        self.assertEqual(self.launcher.find_python(), sys.executable)

    def test_una_ruta_inexistente_no_se_toma_en_cuenta(self):
        os.environ[self.launcher.ENV_VAR] = os.path.join(ROOT, "no_existe.exe")
        # Sin ArcGIS en esta maquina no se encuentra nada, y eso es correcto.
        self.assertIsNone(self.launcher.find_python())

    def test_sin_arcpy_el_interprete_actual_no_vale(self):
        self.assertFalse(self.launcher.has_arcpy())

    def test_el_mensaje_de_ayuda_explica_como_arreglarlo(self):
        text = self.launcher.describe_search()
        self.assertIn(self.launcher.ENV_VAR, text)
        self.assertIn("ArcGIS", text)
        self.assertIn("python.exe", text)

    def test_sin_python_de_arcgis_falla_con_instrucciones(self):
        os.environ.pop(self.launcher.ENV_VAR, None)
        with self.assertRaises(self.launcher.LauncherError) as context:
            self.launcher.relaunch([])
        self.assertIn(self.launcher.ENV_VAR, str(context.exception))

    def test_main_devuelve_error_en_vez_de_reventar(self):
        os.environ.pop(self.launcher.ENV_VAR, None)
        import io as _io

        original = sys.stderr
        sys.stderr = _io.StringIO()
        try:
            code = self.launcher.main([])
        finally:
            sys.stderr = original
        self.assertEqual(code, 1)


class EntryPointsTest(unittest.TestCase):
    def test_los_arranques_existen(self):
        for filename in ("QFieldESRI.py", "QFieldESRI.bat", "QFieldESRI.pyt"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, filename)), filename)

    def test_el_arranque_no_hace_nada_al_importarse(self):
        # QFieldESRI.py solo debe abrir la ventana cuando se ejecuta, no
        # cuando alguien lo importa.
        with open(os.path.join(ROOT, "QFieldESRI.py")) as handle:
            content = handle.read()
        self.assertIn('if __name__ == "__main__":', content)


if __name__ == "__main__":
    unittest.main()
