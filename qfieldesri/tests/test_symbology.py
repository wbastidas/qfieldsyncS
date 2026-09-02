# -*- coding: utf-8 -*-
"""Pruebas de la simbologia: importacion, archivo de estilo y resolucion."""

import io
import json
import os
import shutil
import tempfile
import unittest

from qfieldesri.profiles import load_profile
from qfieldesri.symbology import SymbologyResolver, load_symbology
from qfieldesri.symbology import defaults as symbology_defaults
from qfieldesri.symbology.lyrx import LyrxError, read_lyrx, read_lyrx_folder
from qfieldesri.symbology.model import (
    Color,
    LineStyle,
    MarkerShape,
    Renderer,
    Symbol,
    SymbolLayer,
    points_to_mm,
)
from qfieldesri.symbology.stylesheet import (
    StyleSheet,
    StyleSheetError,
    describe_layer_style,
)


# ----------------------------------------------------------------------
def cim_document():
    """Un ``.lyrx`` sintetico con lo que se encuentra en la practica."""
    return {
        "type": "CIMLayerDocument",
        "layerDefinitions": [
            {
                "type": "CIMFeatureLayer",
                "name": "TramoDistribucionAereo",
                "minScale": 25000,
                "maxScale": 0,
                "layerTransparency": 20,
                "renderer": {
                    "type": "CIMUniqueValueRenderer",
                    "fields": ["SUBTIPO"],
                    "groups": [
                        {
                            "classes": [
                                {
                                    "label": "MT trifasico",
                                    "values": [{"fieldValues": ["1"]}],
                                    "symbol": _line_symbol(
                                        1.5,
                                        {
                                            "type": "CIMRGBColor",
                                            "values": [230, 0, 0, 100],
                                        },
                                    ),
                                },
                                {
                                    "label": "MT monofasico",
                                    "values": [{"fieldValues": ["2"]}],
                                    "symbol": _line_symbol(
                                        1.0,
                                        {
                                            "type": "CIMCMYKColor",
                                            "values": [0, 100, 100, 0, 100],
                                        },
                                        dashes=[6, 3],
                                    ),
                                },
                            ]
                        }
                    ],
                },
                "labelClasses": [
                    {
                        "expression": "$feature.CODIGOESTRUCTURA",
                        "visibility": True,
                        "minimumScale": 5000,
                        "textSymbol": {
                            "symbol": {
                                "type": "CIMTextSymbol",
                                "height": 8,
                                "fontFamilyName": "Tahoma",
                                "fontStyleName": "Bold",
                                "haloSize": 1.5,
                                "symbolLayers": [
                                    {
                                        "type": "CIMSolidFill",
                                        "color": {
                                            "type": "CIMRGBColor",
                                            "values": [0, 0, 0, 100],
                                        },
                                    }
                                ],
                                "haloSymbol": {
                                    "symbolLayers": [
                                        {
                                            "type": "CIMSolidFill",
                                            "color": {
                                                "type": "CIMRGBColor",
                                                "values": [255, 255, 255, 100],
                                            },
                                        }
                                    ]
                                },
                            }
                        },
                    }
                ],
            },
            {
                "type": "CIMFeatureLayer",
                "name": "EstructuraSoporte",
                "renderer": {
                    "type": "CIMSimpleRenderer",
                    "symbol": {
                        "type": "CIMSymbolReference",
                        "symbol": {
                            "type": "CIMPointSymbol",
                            "symbolLayers": [
                                {
                                    "type": "CIMVectorMarker",
                                    "enable": True,
                                    "size": 6,
                                    "markerGraphics": [
                                        {
                                            "geometry": {
                                                "rings": [
                                                    [
                                                        [0, -1],
                                                        [1, 0],
                                                        [0, 1],
                                                        [-1, 0],
                                                        [0, -1],
                                                    ]
                                                ]
                                            },
                                            "symbol": {
                                                "type": "CIMPolygonSymbol",
                                                "symbolLayers": [
                                                    {
                                                        "type": "CIMSolidFill",
                                                        "color": {
                                                            "type": "CIMRGBColor",
                                                            "values": [
                                                                0,
                                                                112,
                                                                255,
                                                                100,
                                                            ],
                                                        },
                                                    },
                                                    {
                                                        "type": "CIMSolidStroke",
                                                        "width": 0.5,
                                                        "color": {
                                                            "type": "CIMGrayColor",
                                                            "values": [80, 100],
                                                        },
                                                    },
                                                ],
                                            },
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                },
            },
            {
                "type": "CIMFeatureLayer",
                "name": "PuestoTransfDistribucion",
                "renderer": {
                    "type": "CIMClassBreaksRenderer",
                    "field": "POTENCIATOTAL",
                    "minimumBreak": 0,
                    "breaks": [
                        {
                            "upperBound": 25,
                            "label": "Hasta 25 kVA",
                            "symbol": _point_symbol([0, 255, 0, 100]),
                        },
                        {
                            "upperBound": 100,
                            "label": "25 a 100 kVA",
                            "symbol": _point_symbol([255, 128, 0, 100]),
                        },
                    ],
                },
            },
        ],
    }


def _line_symbol(width, color, dashes=None):
    layer = {"type": "CIMSolidStroke", "enable": True, "width": width, "color": color}
    if dashes:
        layer["effects"] = [
            {"type": "CIMGeometricEffectDashes", "dashTemplate": dashes}
        ]
    return {
        "type": "CIMSymbolReference",
        "symbol": {"type": "CIMLineSymbol", "symbolLayers": [layer]},
    }


def _point_symbol(rgb):
    return {
        "type": "CIMSymbolReference",
        "symbol": {
            "type": "CIMPointSymbol",
            "symbolLayers": [
                {
                    "type": "CIMVectorMarker",
                    "enable": True,
                    "size": 8,
                    "markerGraphics": [
                        {
                            "geometry": {"x": 0, "y": 0},
                            "symbol": {
                                "type": "CIMPolygonSymbol",
                                "symbolLayers": [
                                    {
                                        "type": "CIMSolidFill",
                                        "color": {"type": "CIMRGBColor", "values": rgb},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        },
    }


# ----------------------------------------------------------------------
class ColorTest(unittest.TestCase):
    def test_hexadecimal(self):
        self.assertEqual(Color.from_hex("#d81e05").to_qgis(), "216,30,5,255")
        self.assertEqual(Color.from_hex("#d81e0580").alpha, 128)

    def test_formatos_admitidos(self):
        self.assertEqual(Color.parse("216,30,5").to_qgis(), "216,30,5,255")
        self.assertEqual(Color.parse("216,30,5,128").alpha, 128)
        self.assertEqual(Color.parse([1, 2, 3]).to_hex(), "#010203")
        self.assertIsNone(Color.parse(None))

    def test_color_invalido(self):
        with self.assertRaises(ValueError):
            Color.parse("no es un color")

    def test_valores_fuera_de_rango_se_recortan(self):
        self.assertEqual(Color(300, -5, 10).to_qgis(), "255,0,10,255")

    def test_conversion_de_unidades(self):
        # ArcGIS trabaja en puntos; el proyecto, en milimetros.
        self.assertAlmostEqual(points_to_mm(72), 25.4, places=3)
        self.assertEqual(points_to_mm(None, 1.5), 1.5)


class SymbolTest(unittest.TestCase):
    def test_marcador(self):
        symbol = Symbol.marker("#d81e05", shape=MarkerShape.SQUARE, size=3.2)
        self.assertEqual(symbol.symbol_type, Symbol.MARKER)
        layer = symbol.layers[0]
        self.assertEqual(layer.get("shape"), "square")
        self.assertEqual(layer.get("size"), 3.2)
        self.assertEqual(symbol.primary_color.to_hex(), "#d81e05")

    def test_flecha_de_sentido(self):
        # En una red electrica el sentido del flujo importa: el manual exige
        # digitalizar de la fuente hacia la carga.
        symbol = Symbol.line("#d81e05").with_flow_arrow()
        self.assertEqual(len(symbol.layers), 2)
        arrow = symbol.layers[-1]
        self.assertEqual(arrow.kind, SymbolLayer.MARKER_LINE)
        self.assertTrue(arrow.get("rotate"))
        self.assertEqual(
            arrow.get("marker").layers[0].get("shape"), MarkerShape.FILLED_ARROWHEAD
        )

    def test_la_flecha_solo_aplica_a_lineas(self):
        marker = Symbol.marker("#000000")
        self.assertIs(marker.with_flow_arrow(), marker)


# ----------------------------------------------------------------------
class LyrxTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "red.lyrx")
        with io.open(self.path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(cim_document()))
        self.result = read_lyrx(self.path)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_lee_todas_las_capas(self):
        self.assertEqual(
            sorted(self.result.styles),
            ["EstructuraSoporte", "PuestoTransfDistribucion", "TramoDistribucionAereo"],
        )
        self.assertEqual(self.result.warnings, [])

    def test_valores_unicos_a_categorizado(self):
        renderer = self.result.styles["TramoDistribucionAereo"].renderer
        self.assertEqual(renderer.kind, Renderer.CATEGORIZED)
        self.assertEqual(renderer.field, "SUBTIPO")
        self.assertEqual(renderer.source, "lyrx")
        self.assertEqual([c.value for c in renderer.categories], ["1", "2"])
        self.assertEqual(renderer.categories[0].label, "MT trifasico")

    def test_colores_rgb_y_cmyk(self):
        categories = self.result.styles["TramoDistribucionAereo"].renderer.categories
        self.assertEqual(
            categories[0].symbol.layers[0].get("color").to_qgis(), "230,0,0,255"
        )
        # CMYK 0/100/100/0 es rojo puro.
        self.assertEqual(
            categories[1].symbol.layers[0].get("color").to_qgis(), "255,0,0,255"
        )

    def test_ancho_en_milimetros_y_guiones(self):
        categories = self.result.styles["TramoDistribucionAereo"].renderer.categories
        self.assertAlmostEqual(categories[0].symbol.layers[0].get("width"), 0.529, 2)
        self.assertEqual(categories[1].symbol.layers[0].get("style"), LineStyle.DASH)
        self.assertTrue(categories[1].symbol.layers[0].get("custom_dash"))

    def test_forma_deducida_de_la_geometria(self):
        # Un cuadrado girado 45 grados es, en la practica, un rombo.
        symbol = self.result.styles["EstructuraSoporte"].renderer.symbol
        marker = symbol.layers[-1]
        self.assertEqual(marker.get("shape"), MarkerShape.DIAMOND)
        self.assertEqual(marker.get("color").to_qgis(), "0,112,255,255")

    def test_color_gris(self):
        marker = self.result.styles["EstructuraSoporte"].renderer.symbol.layers[-1]
        # CIMGrayColor 80 es oscuro: 255 * (1 - 0.8) = 51.
        self.assertEqual(marker.get("outline_color").to_qgis(), "51,51,51,255")

    def test_intervalos_a_graduado(self):
        renderer = self.result.styles["PuestoTransfDistribucion"].renderer
        self.assertEqual(renderer.kind, Renderer.GRADUATED)
        self.assertEqual(renderer.field, "POTENCIATOTAL")
        self.assertEqual(len(renderer.ranges), 2)
        self.assertEqual(renderer.ranges[0].lower, 0)
        self.assertEqual(renderer.ranges[0].upper, 25)
        self.assertEqual(renderer.ranges[1].lower, 25)

    def test_escala_y_opacidad(self):
        style = self.result.styles["TramoDistribucionAereo"]
        self.assertEqual(style.min_scale, 25000)
        self.assertAlmostEqual(style.opacity, 0.8)

    def test_etiquetado(self):
        label = self.result.styles["TramoDistribucionAereo"].label
        self.assertEqual(label.field, "CODIGOESTRUCTURA")
        self.assertFalse(label.is_expression)
        self.assertEqual(label.font_family, "Tahoma")
        self.assertTrue(label.bold)
        self.assertEqual(label.min_scale, 5000)
        self.assertTrue(label.buffer_size > 0)

    def test_expresion_de_etiqueta_compleja(self):
        from qfieldesri.symbology.lyrx import _label_field

        self.assertEqual(_label_field("$feature.CODIGO", None), ("CODIGO", False))
        self.assertEqual(_label_field("[CODIGO]", None), ("CODIGO", False))
        field, is_expression = _label_field(
            '$feature.CODIGO + " - " + $feature.FASE', None
        )
        self.assertTrue(is_expression)
        self.assertNotIn("$feature", field)

    def test_archivo_que_no_es_json(self):
        path = os.path.join(self.directory, "malo.lyrx")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("esto no es json")
        with self.assertRaises(LyrxError):
            read_lyrx(path)

    def test_archivo_inexistente(self):
        with self.assertRaises(LyrxError):
            read_lyrx(os.path.join(self.directory, "no_existe.lyrx"))

    def test_carpeta_completa(self):
        result = read_lyrx_folder(self.directory)
        self.assertIn("TramoDistribucionAereo", result.styles)

    def test_carpeta_inexistente(self):
        with self.assertRaises(LyrxError):
            read_lyrx_folder(os.path.join(self.directory, "no_existe"))

    def test_fabrica_de_carga(self):
        styles, warnings = load_symbology(self.path)
        self.assertIn("EstructuraSoporte", styles)
        self.assertEqual(warnings, [])

    def test_origen_no_reconocido(self):
        with self.assertRaises(StyleSheetError):
            load_symbology(os.path.join(self.directory, "algo.txt"))


# ----------------------------------------------------------------------
class StyleSheetTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _sheet(self, capas):
        return StyleSheet({"version": 1, "capas": capas})

    def test_simbolo_de_linea_con_flecha(self):
        sheet = self._sheet(
            {
                "Tramo": {
                    "simbologia": {
                        "tipo": "simple",
                        "simbolo": {"color": "#d81e05", "ancho": 0.8, "flecha": True},
                    }
                }
            }
        )
        style = sheet.style_for("Tramo", "Line")
        symbol = style.renderer.symbol
        self.assertEqual(symbol.layers[0].get("color").to_hex(), "#d81e05")
        self.assertEqual(symbol.layers[-1].kind, SymbolLayer.MARKER_LINE)

    def test_nombres_en_espanol_de_formas_y_estilos(self):
        sheet = self._sheet(
            {
                "Poste": {
                    "simbologia": {"simbolo": {"forma": "rombo", "color": "#111111"}}
                },
                "Tramo": {
                    "simbologia": {"simbolo": {"estilo": "guiones", "color": "#111111"}}
                },
            }
        )
        self.assertEqual(
            sheet.style_for("Poste", "Point").renderer.symbol.layers[0].get("shape"),
            MarkerShape.DIAMOND,
        )
        self.assertEqual(
            sheet.style_for("Tramo", "Line").renderer.symbol.layers[0].get("style"),
            LineStyle.DASH,
        )

    def test_atajo_declarando_solo_el_simbolo(self):
        sheet = self._sheet({"Poste": {"simbologia": {"color": "#00ff00"}}})
        renderer = sheet.style_for("Poste", "Point").renderer
        self.assertEqual(renderer.kind, Renderer.SINGLE)

    def test_categorizado(self):
        sheet = self._sheet(
            {
                "Barra": {
                    "simbologia": {
                        "tipo": "categorizado",
                        "campo": "SUBTIPO",
                        "categorias": [
                            {
                                "valor": 1,
                                "etiqueta": "BT",
                                "simbolo": {"color": "#1f6fb4"},
                            },
                            {
                                "valor": 2,
                                "etiqueta": "MT",
                                "simbolo": {"color": "#d81e05"},
                            },
                        ],
                    }
                }
            }
        )
        renderer = sheet.style_for("Barra", "Line").renderer
        self.assertEqual(renderer.kind, Renderer.CATEGORIZED)
        self.assertEqual([c.value for c in renderer.categories], [1, 2])

    def test_por_subtipos_usa_los_de_la_geodatabase(self):
        # Los codigos no se fijan en el archivo: los declara la geodatabase, y
        # cambian de una Unidad de Negocio a otra.
        sheet = self._sheet(
            {
                "Tramo": {
                    "simbologia": {
                        "tipo": "subtipos",
                        "simbolo": {"ancho": 0.8},
                        "colores": ["#aa0000", "#00aa00"],
                    }
                }
            }
        )
        style = sheet.style_for(
            "Tramo",
            "Line",
            subtype_field="SUBTIPO",
            subtype_categories=[(1, "Trifasico"), (2, "Monofasico"), (3, "Bifasico")],
        )
        renderer = style.renderer
        self.assertEqual(renderer.kind, Renderer.CATEGORIZED)
        self.assertEqual(renderer.field, "SUBTIPO")
        self.assertEqual(
            [c.label for c in renderer.categories],
            ["Trifasico", "Monofasico", "Bifasico"],
        )
        colors = [c.symbol.layers[0].get("color").to_hex() for c in renderer.categories]
        self.assertEqual(colors, ["#aa0000", "#00aa00", "#aa0000"])

    def test_por_subtipos_admite_excepciones(self):
        sheet = self._sheet(
            {
                "Tramo": {
                    "simbologia": {
                        "tipo": "subtipos",
                        "colores": ["#aa0000"],
                        "por_subtipo": {"2": {"color": "#0000ff", "ancho": 2.0}},
                    }
                }
            }
        )
        renderer = sheet.style_for(
            "Tramo",
            "Line",
            subtype_field="SUBTIPO",
            subtype_categories=[(1, "A"), (2, "B")],
        ).renderer
        self.assertEqual(
            renderer.categories[1].symbol.layers[0].get("color").to_hex(), "#0000ff"
        )
        self.assertEqual(renderer.categories[1].symbol.layers[0].get("width"), 2.0)

    def test_por_subtipos_sin_subtipos_no_deja_la_capa_sin_dibujar(self):
        sheet = self._sheet(
            {"Tramo": {"simbologia": {"tipo": "subtipos", "colores": ["#aa0000"]}}}
        )
        renderer = sheet.style_for("Tramo", "Line").renderer
        self.assertEqual(renderer.kind, Renderer.SINGLE)
        self.assertEqual(renderer.symbol.layers[0].get("color").to_hex(), "#aa0000")

    def test_graduado_y_reglas(self):
        sheet = self._sheet(
            {
                "Puesto": {
                    "simbologia": {
                        "tipo": "graduado",
                        "campo": "POTENCIA",
                        "intervalos": [
                            {"desde": 0, "hasta": 25, "simbolo": {"color": "#00ff00"}},
                            {
                                "desde": 25,
                                "hasta": 100,
                                "simbolo": {"color": "#ff0000"},
                            },
                        ],
                    }
                },
                "Otro": {
                    "simbologia": {
                        "tipo": "reglas",
                        "reglas": [
                            {
                                "expresion": '"FASE" = 1',
                                "etiqueta": "A",
                                "simbolo": {"color": "#123456"},
                            }
                        ],
                    }
                },
            }
        )
        self.assertEqual(
            sheet.style_for("Puesto", "Point").renderer.kind, Renderer.GRADUATED
        )
        rules = sheet.style_for("Otro", "Point").renderer.rules
        self.assertEqual(rules[0].expression, '"FASE" = 1')

    def test_escala_opacidad_y_etiqueta(self):
        sheet = self._sheet(
            {
                "Poste": {
                    "escala_minima": 15000,
                    "opacidad": 0.5,
                    "simbologia": {"simbolo": {"color": "#111111"}},
                    "etiqueta": {
                        "campo": "CODIGO",
                        "tamano": 9,
                        "negrita": True,
                        "escala_minima": 2500,
                    },
                }
            }
        )
        style = sheet.style_for("Poste", "Point")
        self.assertEqual(style.min_scale, 15000)
        self.assertEqual(style.opacity, 0.5)
        self.assertEqual(style.label.field, "CODIGO")
        self.assertTrue(style.label.bold)
        self.assertEqual(style.label.min_scale, 2500)

    def test_valores_por_defecto_para_las_no_declaradas(self):
        sheet = StyleSheet(
            {"capas": {}, "por_defecto": {"simbologia": {"color": "#ababab"}}}
        )
        style = sheet.style_for("LoQueSea", "Point")
        self.assertIsNotNone(style)
        self.assertEqual(
            style.renderer.symbol.layers[0].get("color").to_hex(), "#ababab"
        )

    def test_errores_con_mensaje_util(self):
        cases = (
            ({"tipo": "inventado"}, "Point", "inventado"),
            ({"tipo": "categorizado"}, "Point", "campo"),
            ({"simbolo": {"forma": "espiral"}}, "Point", "espiral"),
            ({"simbolo": {"estilo": "ondulada"}}, "Line", "ondulada"),
            ({"simbolo": {"estilo": "rayado"}}, "Polygon", "rayado"),
        )
        for simbologia, geometry, fragment in cases:
            sheet = self._sheet({"X": {"simbologia": simbologia}})
            with self.assertRaises(StyleSheetError) as context:
                sheet.style_for("X", geometry)
            self.assertIn(fragment, str(context.exception))

    def test_color_en_forma_corta(self):
        sheet = self._sheet({"X": {"simbologia": {"simbolo": {"color": "#f00"}}}})
        self.assertEqual(
            sheet.style_for("X", "Point")
            .renderer.symbol.layers[0]
            .get("color")
            .to_hex(),
            "#ff0000",
        )

    def test_archivo_invalido(self):
        path = os.path.join(self.directory, "estilo.json")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("[1, 2, 3]")
        with self.assertRaises(StyleSheetError):
            StyleSheet.load(path)

    def test_guardar_y_releer(self):
        sheet = self._sheet(
            {"Poste": {"simbologia": {"simbolo": {"color": "#123456"}}}}
        )
        path = os.path.join(self.directory, "estilo.json")
        sheet.save(path)
        again = StyleSheet.load(path)
        self.assertEqual(
            again.style_for("Poste", "Point")
            .renderer.symbol.layers[0]
            .get("color")
            .to_hex(),
            "#123456",
        )

    def test_exportar_un_estilo_resuelto_como_plantilla(self):
        # Es como se obtiene un punto de partida editable.
        style = symbology_defaults.build_style(
            "EstructuraSoporte", "Point", field_names=["CODIGOESTRUCTURA"]
        )
        definition = describe_layer_style(style, "Point")
        self.assertIn("simbologia", definition)
        self.assertIn("color", definition["simbologia"]["simbolo"])

        sheet = StyleSheet({"capas": {}})
        sheet.set_style("EstructuraSoporte", style, "Point")
        self.assertTrue(sheet.knows("EstructuraSoporte"))

    def test_el_estilo_del_perfil_es_un_archivo_de_estilo_mas(self):
        here = os.path.dirname(
            os.path.dirname(os.path.abspath(symbology_defaults.__file__))
        )
        path = os.path.join(here, "profiles", "cnel_ep.estilo.json")
        self.assertTrue(os.path.isfile(path))
        sheet = StyleSheet.load(path)
        self.assertTrue(sheet.knows("TramoDistribucionAereo"))
        style = sheet.style_for(
            "TramoDistribucionAereo",
            "Line",
            subtype_field="SUBTIPO",
            subtype_categories=[(1, "Trifasico")],
        )
        self.assertEqual(style.renderer.kind, Renderer.CATEGORIZED)


# ----------------------------------------------------------------------
class DefaultsTest(unittest.TestCase):
    def test_el_color_es_estable_por_nombre(self):
        # El mismo poste tiene que salir del mismo color aunque cambie el orden
        # de empaquetado.
        first = symbology_defaults.color_for("EstructuraSoporte")
        second = symbology_defaults.color_for("EstructuraSoporte")
        self.assertEqual(first, second)
        self.assertNotEqual(first, symbology_defaults.color_for("PuntoCarga"))

    def test_forma_segun_el_papel_de_la_clase(self):
        profile = load_profile("cnel_ep")
        puesto = symbology_defaults.build_style(
            "PuestoTransfDistribucion", "Point", profile=profile
        )
        self.assertEqual(
            puesto.renderer.symbol.layers[0].get("shape"), MarkerShape.SQUARE
        )

    def test_etiqueta_del_primer_campo_con_sentido(self):
        style = symbology_defaults.build_style(
            "X", "Point", field_names=["OBJECTID", "TEXTOETIQUETA", "OTRO"]
        )
        self.assertEqual(style.label.field, "TEXTOETIQUETA")
        # Configurada pero apagada: el proyecto abre limpio.
        self.assertFalse(style.label.enabled)

    def test_sin_campo_util_no_hay_etiqueta(self):
        style = symbology_defaults.build_style("X", "Point", field_names=["A", "B"])
        self.assertIsNone(style.label)

    def test_las_capas_densas_reciben_limite_de_escala(self):
        style = symbology_defaults.build_style("X", "Point", feature_count=500000)
        self.assertEqual(style.min_scale, symbology_defaults.DENSE_MIN_SCALE)
        self.assertEqual(
            symbology_defaults.build_style("X", "Point", feature_count=10).min_scale, 0
        )

    def test_subtipos_sin_estilo_declarado(self):
        style = symbology_defaults.build_style(
            "X",
            "Point",
            subtype_field="SUBTIPO",
            subtype_categories=[(1, "A"), (2, "B")],
        )
        self.assertEqual(style.renderer.kind, Renderer.CATEGORIZED)
        colors = [
            c.symbol.layers[0].get("color").to_hex() for c in style.renderer.categories
        ]
        self.assertNotEqual(colors[0], colors[1])


# ----------------------------------------------------------------------
class ResolverTest(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("cnel_ep")

    def test_orden_de_precedencia(self):
        user = StyleSheet(
            {"capas": {"Barra": {"simbologia": {"simbolo": {"color": "#ffffff"}}}}}
        )
        imported = {
            "Barra": StyleSheet(
                {"capas": {"Barra": {"simbologia": {"simbolo": {"color": "#000000"}}}}}
            ).style_for("Barra", "Line")
        }
        resolver = SymbologyResolver(
            profile=self.profile, stylesheet=user, imported=imported
        )
        style = resolver.style_for("Barra", "Line")
        # Manda el archivo de estilo del usuario.
        self.assertEqual(
            style.renderer.symbol.layers[0].get("color").to_hex(), "#ffffff"
        )
        self.assertEqual(resolver.sources["Barra"], "estilo")

    def test_sin_estilo_del_usuario_manda_lo_importado(self):
        imported = {
            "Barra": StyleSheet(
                {"capas": {"Barra": {"simbologia": {"simbolo": {"color": "#010203"}}}}}
            ).style_for("Barra", "Line")
        }
        imported["Barra"].renderer.source = "lyrx"
        resolver = SymbologyResolver(profile=self.profile, imported=imported)
        style = resolver.style_for("Barra", "Line")
        self.assertEqual(
            style.renderer.symbol.layers[0].get("color").to_hex(), "#010203"
        )
        self.assertEqual(resolver.sources["Barra"], "lyrx")

    def test_el_perfil_cubre_lo_no_declarado(self):
        resolver = SymbologyResolver(profile=self.profile)
        resolver.style_for("PuestoTransfDistribucion", "Point")
        self.assertEqual(resolver.sources["PuestoTransfDistribucion"], "estilo")

    def test_lo_desconocido_cae_en_automatico(self):
        resolver = SymbologyResolver(profile=self.profile)
        resolver.style_for("ClaseQueNadieConoce", "Point")
        self.assertEqual(resolver.sources["ClaseQueNadieConoce"], "automatico")

    def test_nombre_calificado_de_geodatabase_corporativa(self):
        imported = {
            "EstructuraSoporte": StyleSheet(
                {"capas": {"X": {"simbologia": {"simbolo": {"color": "#010203"}}}}}
            ).style_for("X", "Point")
        }
        resolver = SymbologyResolver(imported=imported)
        style = resolver.style_for("GYE.SDE.EstructuraSoporte", "Point")
        self.assertEqual(
            style.renderer.symbol.layers[0].get("color").to_hex(), "#010203"
        )

    def test_avisa_si_clasifica_por_un_campo_que_no_viaja(self):
        user = StyleSheet(
            {
                "capas": {
                    "Barra": {
                        "simbologia": {
                            "tipo": "categorizado",
                            "campo": "NO_EXISTE",
                            "categorias": [
                                {"valor": 1, "simbolo": {"color": "#ffffff"}}
                            ],
                        }
                    }
                }
            }
        )
        resolver = SymbologyResolver(stylesheet=user)
        style = resolver.style_for("Barra", "Line", field_names=["SUBTIPO"])
        # Se degrada a simbolo unico en vez de dejar la capa sin dibujar.
        self.assertEqual(style.renderer.kind, Renderer.SINGLE)
        self.assertTrue(any("NO_EXISTE" in warning for warning in resolver.warnings))

    def test_avisa_si_la_etiqueta_usa_un_campo_que_no_viaja(self):
        user = StyleSheet(
            {
                "capas": {
                    "Barra": {
                        "etiqueta": {"campo": "NO_EXISTE"},
                        "simbologia": {"simbolo": {"color": "#fff"}},
                    }
                }
            }
        )
        resolver = SymbologyResolver(stylesheet=user)
        style = resolver.style_for("Barra", "Line", field_names=["SUBTIPO"])
        self.assertFalse(style.label.enabled)
        self.assertTrue(any("NO_EXISTE" in warning for warning in resolver.warnings))

    def test_un_estilo_roto_no_tumba_el_empaquetado(self):
        user = StyleSheet({"capas": {"Barra": {"simbologia": {"tipo": "inventado"}}}})
        resolver = SymbologyResolver(profile=self.profile, stylesheet=user)
        style = resolver.style_for("Barra", "Line")
        self.assertIsNotNone(style.renderer)
        self.assertTrue(resolver.warnings)

    def test_resumen_de_origenes(self):
        resolver = SymbologyResolver(profile=self.profile)
        resolver.style_for("PuestoTransfDistribucion", "Point")
        resolver.style_for("ClaseNueva", "Point")
        summary = resolver.summary()
        self.assertIn("archivo de estilo", summary)
        self.assertIn("automatica", summary)


if __name__ == "__main__":
    unittest.main()
