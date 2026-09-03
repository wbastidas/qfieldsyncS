# -*- coding: utf-8 -*-
"""Lectura de simbologia desde ArcGIS, con un ``arcpy`` simulado.

La duda razonable es si qfieldESRI **lee de verdad** la simbologia que ya
existe en la oficina. Aqui se comprueba con un ``arcpy`` de mentira que imita
lo que devuelve cada version, porque lo que devuelve cada version es
precisamente lo que decide cuanto se puede trasladar:

* **ArcMap 10.x** (``arcpy.mapping``): un MXD y un ``.lyr`` son binarios y la
  API publica **la clasificacion, no los colores**. Se traslada la estructura
  —campo, valores y rotulos, y el etiquetado— con la paleta de qfieldESRI, y
  se avisa. No es una limitacion de qfieldESRI: ArcGIS no expone mas.
* **ArcGIS Pro** (``arcpy.mp``): la definicion CIM de la capa es el mismo JSON
  que un ``.lyrx``, asi que de ahi salen los colores exactos.

Las dos vias terminan en el mismo modelo neutro, que es lo que despues se
escribe en el proyecto de QField.
"""

import json
import sys
import types
import unittest

from qfieldesri.symbology.model import Renderer


class FakeSymbology(object):
    """Lo que ``arcpy.mapping`` deja ver de una simbologia de valores unicos."""

    def __init__(self, field, values, labels):
        self.valueField = field
        self.classValues = values
        self.classLabels = labels


class FakeLabelClass(object):
    def __init__(self, expression):
        self.expression = expression


class FakeMapLayer(object):
    """Capa de ArcMap: nombre de mapa distinto del de la clase de origen."""

    def __init__(self, name, data_source, symbology=None, labels=None):
        self.name = name
        self.dataSource = data_source
        self.isFeatureLayer = True
        self.symbologyType = "UNIQUE_VALUES" if symbology else "OTHER"
        self.symbology = symbology
        self.showLabels = bool(labels)
        self.labelClasses = [FakeLabelClass(labels)] if labels else []

    def supports(self, name):
        return name == "LABELCLASSES" and bool(self.labelClasses)


class FakeProLayer(object):
    """Capa de ArcGIS Pro: la definicion CIM trae los colores completos."""

    def __init__(self, name, data_source, definition):
        self.name = name
        self.dataSource = data_source
        self.isFeatureLayer = True
        self._definition = definition

    def getDefinition(self, _version):  # noqa: N802 - API de arcpy
        return _Definition(self._definition)


class _Definition(object):
    def __init__(self, payload):
        self._payload = payload

    def toJSON(self):  # noqa: N802 - API de arcpy
        return json.dumps(self._payload)


def cim_layer(color):
    """Definicion CIM minima de una capa con un simbolo de linea solido."""
    return {
        "type": "CIMFeatureLayer",
        "name": "Tramo MT",
        "renderer": {
            "type": "CIMSimpleRenderer",
            "symbol": {
                "symbol": {
                    "type": "CIMLineSymbol",
                    "symbolLayers": [
                        {
                            "type": "CIMSolidStroke",
                            "width": 2.0,
                            "color": {"type": "CIMRGBColor", "values": color},
                        }
                    ],
                }
            },
        },
    }


def install_fake_arcpy(module):
    """Deja el ``arcpy`` simulado en ``sys.modules`` y devuelve el anterior."""
    previous = sys.modules.get("arcpy")
    sys.modules["arcpy"] = module
    # El lector se importa perezosamente, asi que basta con recargar el modulo
    # que lo consume si ya estaba cargado.
    sys.modules.pop("qfieldesri.symbology.arcgis", None)
    return previous


def restore_arcpy(previous):
    sys.modules.pop("qfieldesri.symbology.arcgis", None)
    if previous is None:
        sys.modules.pop("arcpy", None)
    else:
        sys.modules["arcpy"] = previous


def arcmap_arcpy(layers):
    """``arcpy`` de ArcMap: tiene ``mapping`` y no tiene ``mp``."""
    module = types.ModuleType("arcpy")
    mapping = types.ModuleType("arcpy.mapping")
    mapping.MapDocument = lambda path: ("documento", path)
    mapping.ListLayers = lambda _document: list(layers)
    mapping.Layer = lambda path: layers[0]
    module.mapping = mapping
    module.Describe = lambda _path: types.SimpleNamespace(shapeType="Polyline")
    return module


def pro_arcpy(layers):
    """``arcpy`` de ArcGIS Pro: tiene ``mp`` y no tiene ``mapping``."""
    module = types.ModuleType("arcpy")
    mp = types.ModuleType("arcpy.mp")

    class Project(object):
        def __init__(self, _path):
            pass

        def listMaps(self):  # noqa: N802 - API de arcpy
            return [types.SimpleNamespace(listLayers=lambda: list(layers))]

    mp.ArcGISProject = Project
    module.mp = mp
    module.Describe = lambda _path: types.SimpleNamespace(shapeType="Polyline")
    return module


class ArcMapReadingTest(unittest.TestCase):
    """Lo que se puede rescatar de un MXD abierto en ArcMap."""

    def setUp(self):
        layer = FakeMapLayer(
            "Tramos de media tension",
            r"C:\datos\SIGELEC.sde\SIGELEC.TRAMODISTRIBUCIONAEREO",
            symbology=FakeSymbology(
                "SUBTIPO", [1, 2], ["Tramo MTA Monofasico", "Tramo MTA Trifasico"]
            ),
            labels="[ALIMENTADORID]",
        )
        self.previous = install_fake_arcpy(arcmap_arcpy([layer]))

    def tearDown(self):
        restore_arcpy(self.previous)

    def test_se_lee_el_mapa_abierto(self):
        from qfieldesri.symbology.arcgis import read_active_document

        result = read_active_document()
        self.assertEqual(len(result.styles), 1)

    def test_la_capa_se_indexa_por_la_clase_no_por_el_rotulo_del_mapa(self):
        """En el mapa se llama "Tramos de media tension"; la clase es otra."""
        from qfieldesri.symbology.arcgis import read_active_document

        styles = read_active_document().styles
        self.assertIn("SIGELEC.TRAMODISTRIBUCIONAEREO", styles)

    def test_se_traslada_la_clasificacion_con_sus_rotulos(self):
        from qfieldesri.symbology.arcgis import read_active_document

        style = read_active_document().styles["SIGELEC.TRAMODISTRIBUCIONAEREO"]
        self.assertEqual(style.renderer.kind, Renderer.CATEGORIZED)
        self.assertEqual(style.renderer.field, "SUBTIPO")
        self.assertEqual([c.value for c in style.renderer.categories], [1, 2])
        self.assertEqual(
            [c.label for c in style.renderer.categories],
            ["Tramo MTA Monofasico", "Tramo MTA Trifasico"],
        )

    def test_cada_categoria_sale_con_un_color_distinto(self):
        """ArcGIS no da los colores: al menos que se distingan entre si."""
        from qfieldesri.symbology.arcgis import read_active_document

        style = read_active_document().styles["SIGELEC.TRAMODISTRIBUCIONAEREO"]
        colors = [
            category.symbol.layers[0].get("color").to_qgis()
            for category in style.renderer.categories
        ]
        self.assertEqual(len(set(colors)), len(colors))

    def test_se_traslada_la_etiqueta(self):
        from qfieldesri.symbology.arcgis import read_active_document

        style = read_active_document().styles["SIGELEC.TRAMODISTRIBUCIONAEREO"]
        self.assertIsNotNone(style.label)
        self.assertEqual(style.label.field, "ALIMENTADORID")
        self.assertTrue(style.label.enabled)

    def test_se_avisa_de_que_los_colores_no_vienen(self):
        """Un aviso claro vale mas que un color inventado sin explicacion."""
        from qfieldesri.symbology.arcgis import read_active_document

        avisos = " ".join(read_active_document().warnings)
        self.assertIn("no los colores", avisos)
        self.assertIn(".lyrx", avisos)


class ProReadingTest(unittest.TestCase):
    """En Pro si salen los colores, porque la definicion CIM es JSON."""

    def setUp(self):
        layer = FakeProLayer(
            "Tramo MT",
            r"C:\datos\red.gdb\TramoDistribucionAereo",
            cim_layer([216, 30, 5, 100]),
        )
        self.previous = install_fake_arcpy(pro_arcpy([layer]))

    def tearDown(self):
        restore_arcpy(self.previous)

    def test_los_colores_llegan_exactos(self):
        from qfieldesri.symbology.arcgis import read_active_document

        style = read_active_document().styles["TramoDistribucionAereo"]
        symbol_layer = style.renderer.symbol.layers[0]
        self.assertEqual(symbol_layer.get("color").to_qgis(), "216,30,5,255")

    def test_el_grosor_se_convierte_de_puntos_a_milimetros(self):
        from qfieldesri.symbology.arcgis import read_active_document

        style = read_active_document().styles["TramoDistribucionAereo"]
        width = style.renderer.symbol.layers[0].get("width")
        # 2 puntos = 0,7056 mm
        self.assertAlmostEqual(width, 0.7056, places=3)


class SymbologyReachesTheProjectTest(unittest.TestCase):
    """De lo leido en ArcGIS al archivo que abre QField, sin pasos manuales."""

    def setUp(self):
        layer = FakeProLayer(
            "Tramo MT",
            r"C:\datos\red.gdb\TramoDistribucionAereo",
            cim_layer([10, 200, 30, 100]),
        )
        self.previous = install_fake_arcpy(pro_arcpy([layer]))

    def tearDown(self):
        restore_arcpy(self.previous)

    def test_el_color_leido_de_arcgis_termina_en_el_proyecto(self):
        import io as _io
        import shutil
        import tempfile

        from qfieldesri.core.config import PackagingConfig
        from qfieldesri.core.packager import Packager
        from qfieldesri.demo import build_reader
        from qfieldesri.symbology.arcgis import CURRENT

        directory = tempfile.mkdtemp()
        try:
            config = PackagingConfig(
                workspace="demo.gdb",
                output_dir=directory,
                project_name="conmxd",
                symbology_source=CURRENT,
            )
            result = Packager(build_reader(), config).run()
            with _io.open(result.project_file, encoding="utf-8") as handle:
                project = handle.read()
            self.assertIn("10,200,30,255", project)
            self.assertIn(
                "archivos de capa de ArcGIS Pro", result.symbology_description
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
