# -*- coding: utf-8 -*-
"""Pruebas de la caja de herramientas de ArcGIS.

``QFieldESRI.pyt`` solo se puede abrir dentro de ArcGIS, asi que sin esto se
quedaria sin probar hasta que alguien lo ejecutara en produccion. Aqui se carga
con un ``arcpy`` simulado —lo justo para construir parametros y registrar
mensajes— y se comprueba lo que si es logica propia: los parametros que expone,
como traduce lo elegido en el dialogo a un ambito de exportacion y los
auxiliares de los desplegables.
"""

import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class FakeFilter(object):
    def __init__(self):
        self.list = []


class FakeParameter(object):
    """Lo imprescindible de ``arcpy.Parameter`` para construir el dialogo."""

    def __init__(self, **kwargs):
        self.name = kwargs.get("name")
        self.displayName = kwargs.get("displayName")
        self.datatype = kwargs.get("datatype")
        self.parameterType = kwargs.get("parameterType")
        self.direction = kwargs.get("direction")
        self.multiValue = kwargs.get("multiValue", False)
        self.category = kwargs.get("category")
        self.filter = FakeFilter()
        self.columns = []
        self.enabled = True
        self.altered = False
        self.value = None
        self.messages = []

    @property
    def valueAsText(self):  # noqa: N802 - API de ArcGIS
        if self.value is None:
            return None
        if isinstance(self.value, (list, tuple)):
            return ";".join(str(item) for item in self.value)
        return str(self.value)

    def setWarningMessage(self, text):  # noqa: N802 - API de ArcGIS
        self.messages.append(("warning", text))

    def setErrorMessage(self, text):  # noqa: N802 - API de ArcGIS
        self.messages.append(("error", text))

    def setIDMessage(self, kind, code):  # noqa: N802 - API de ArcGIS
        self.messages.append((kind.lower(), code))


def build_fake_arcpy():
    module = types.ModuleType("arcpy")
    module.Parameter = FakeParameter
    module.messages = []
    module.AddMessage = lambda text: module.messages.append(("mensaje", text))
    module.AddWarning = lambda text: module.messages.append(("aviso", text))
    module.AddError = lambda text: module.messages.append(("error", text))
    module.AddFieldDelimiters = lambda _path, name: '"%s"' % name
    module.Exists = lambda _path: False
    module.Describe = lambda _path: None
    module.SetProgressor = lambda *args, **kwargs: None
    module.SetProgressorPosition = lambda *args: None
    module.ResetProgressor = lambda: None
    module.env = types.SimpleNamespace(
        workspace=None, overwriteOutput=True, scratchFolder=None
    )
    module.da = types.ModuleType("arcpy.da")
    module.management = types.ModuleType("arcpy.management")
    return module


def load_toolbox():
    """Carga ``QFieldESRI.pyt`` como si fuera un modulo de Python."""
    sys.modules["arcpy"] = build_fake_arcpy()
    sys.modules.pop("qfieldesri.readers.arcpy_reader", None)
    path = os.path.join(ROOT, "QFieldESRI.pyt")

    if sys.version_info[0] >= 3:  # noqa: UP036 - ArcMap 10.x sigue en 2.7
        import importlib.util
        from importlib.machinery import SourceFileLoader

        # ``.pyt`` no es un sufijo de codigo fuente reconocido: hay que
        # indicarle el cargador a mano.
        loader = SourceFileLoader("qfieldesri_toolbox", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    import imp  # pragma: no cover - Python 2.7 (ArcMap)

    return imp.load_source("qfieldesri_toolbox", path)


class ToolboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.toolbox = load_toolbox()

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("arcpy", None)
        sys.modules.pop("qfieldesri.readers.arcpy_reader", None)

    def test_la_caja_expone_las_cinco_herramientas(self):
        toolbox = self.toolbox.Toolbox()
        self.assertEqual(toolbox.alias, "qfieldesri")
        self.assertEqual(len(toolbox.tools), 5)
        labels = [tool().label for tool in toolbox.tools]
        self.assertTrue(labels[0].startswith("1"))
        self.assertTrue(any("Empaquetar" in label for label in labels))
        self.assertTrue(any("Sincronizar" in label for label in labels))

    def test_parametros_del_empaquetado(self):
        parameters = self.toolbox.EmpaquetarParaQField().getParameterInfo()
        names = [parameter.name for parameter in parameters]
        for expected in (
            "workspace",
            "output_dir",
            "project_name",
            "profile",
            "scope_kind",
            "scope_values",
            "scope_polygon",
            "scope_follow",
            "layers",
            "filters",
            "photos",
        ):
            self.assertIn(expected, names)

    def test_el_ambito_esta_en_su_propia_categoria(self):
        parameters = self.toolbox.EmpaquetarParaQField().getParameterInfo()
        by_name = dict((p.name, p) for p in parameters)
        self.assertEqual(by_name["scope_kind"].category, "Ambito de exportacion")
        self.assertIn(self.toolbox.SCOPE_ALL, by_name["scope_kind"].filter.list)
        self.assertIn("Alimentador", by_name["scope_kind"].filter.list)
        self.assertIn("Poligono de sector", by_name["scope_kind"].filter.list)

    def test_etiqueta_del_ambito_a_identificador(self):
        convert = self.toolbox._scope_kind_from_label
        self.assertEqual(convert("Alimentador"), "alimentador")
        self.assertEqual(convert("Poligono de sector"), "poligono")
        self.assertIsNone(convert(self.toolbox.SCOPE_ALL))
        self.assertIsNone(convert(None))

    def test_codigo_de_un_valor_del_desplegable(self):
        extract = self.toolbox._scope_code
        self.assertEqual(extract("04BH070T11 - S/E BELO HORIZONTE"), "04BH070T11")
        self.assertEqual(extract("0901"), "0901")

    def test_lectura_de_parametros_multivalor(self):
        parameter = FakeParameter(name="x", multiValue=True)
        parameter.value = ["A - uno", "B - dos"]
        self.assertEqual(self.toolbox._multi(parameter), ["A - uno", "B - dos"])
        parameter.value = None
        self.assertEqual(self.toolbox._multi(parameter), [])

    # -- traduccion del dialogo al ambito -------------------------------
    def _packaging_parameters(self, **values):
        tool = self.toolbox.EmpaquetarParaQField()
        parameters = tool.getParameterInfo()
        by_name = dict((p.name, p) for p in parameters)
        for key, value in values.items():
            by_name[key].value = value
        return tool, by_name

    def test_sin_acotar_produce_un_ambito_vacio(self):
        tool, by_name = self._packaging_parameters(scope_kind=self.toolbox.SCOPE_ALL)
        scope = tool._scope(by_name)
        self.assertTrue(scope.is_empty)

    def test_ambito_por_alimentador(self):
        tool, by_name = self._packaging_parameters(
            scope_kind="Alimentador",
            scope_values=[
                "04BH070T11 - S/E BELO HORIZONTE",
                "04SM320T22 - S/E SAMANES",
            ],
        )
        scope = tool._scope(by_name)
        self.assertEqual(scope.kind, "alimentador")
        self.assertEqual(scope.values, ["04BH070T11", "04SM320T22"])
        self.assertTrue(scope.follow_relationships)

    def test_ambito_por_parroquia_sin_arrastrar_unidades(self):
        tool, by_name = self._packaging_parameters(
            scope_kind="Parroquia", scope_values=["090150"]
        )
        by_name["scope_follow"].value = False
        scope = tool._scope(by_name)
        self.assertEqual(scope.kind, "parroquia")
        self.assertEqual(scope.values, ["090150"])
        self.assertFalse(scope.follow_relationships)

    def test_ambito_por_poligono_sin_capa_no_revienta(self):
        tool, by_name = self._packaging_parameters(scope_kind="Poligono de sector")
        scope = tool._scope(by_name)
        self.assertEqual(scope.kind, "poligono")
        self.assertIsNone(scope.polygon_wkt)

    # -- comportamiento del dialogo -------------------------------------
    def test_los_campos_del_ambito_se_habilitan_segun_el_tipo(self):
        tool, by_name = self._packaging_parameters(scope_kind="Alimentador")
        tool.updateParameters(list(by_name.values()))
        self.assertTrue(by_name["scope_values"].enabled)
        self.assertFalse(by_name["scope_polygon"].enabled)

        by_name["scope_kind"].value = "Poligono de sector"
        tool.updateParameters(list(by_name.values()))
        self.assertTrue(by_name["scope_polygon"].enabled)
        self.assertFalse(by_name["scope_values"].enabled)

    def test_avisa_si_se_elige_ambito_sin_valores(self):
        tool, by_name = self._packaging_parameters(scope_kind="Canton")
        tool.updateMessages(list(by_name.values()))
        self.assertTrue(by_name["scope_values"].messages)

    def test_exige_capa_cuando_el_ambito_es_un_poligono(self):
        tool, by_name = self._packaging_parameters(scope_kind="Poligono de sector")
        tool.updateMessages(list(by_name.values()))
        self.assertTrue(by_name["scope_polygon"].messages)

    def test_la_herramienta_de_sincronizacion_valida_la_carpeta(self):
        tool = self.toolbox.SincronizarDesdeQField()
        parameters = tool.getParameterInfo()
        parameters[0].value = ROOT  # no es un paquete
        tool.updateMessages(parameters)
        self.assertTrue(parameters[0].messages)


if __name__ == "__main__":
    unittest.main()
