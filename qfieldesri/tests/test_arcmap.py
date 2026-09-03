# -*- coding: utf-8 -*-
"""Guardias de compatibilidad con ArcGIS Desktop (ArcMap 10.x, Python 2.7).

El destino principal de qfieldESRI es **ArcMap**, cuyo Python es 2.7 y cuyo
``arcpy`` es el de la version 10.x. ArcGIS Pro se admite, pero no puede
dictar el codigo: basta con usar una llamada que solo exista en Pro para que
el programa deje de arrancar en la mitad de las instalaciones a las que va
dirigido, y el fallo no aparece aqui —aparece en el equipo del tecnico—.

Por eso estas pruebas no comprueban comportamiento sino **superficie**: que
sintaxis se escribe y que llamadas de arcpy se usan. Se pueden ejecutar sin
ArcGIS y sin Python 2.7, que es justo lo que hace falta para que sirvan de
red de seguridad en cualquier equipo.
"""

import ast
import io
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Carpetas que no son codigo del proyecto.
SKIP_DIRS = ("__pycache__", ".git", ".ruff_cache", "docs")

#: Nodos de sintaxis que Python 2.7 no sabe leer.
PY3_NODES = {
    "JoinedStr": "f-string",
    "FormattedValue": "f-string",
    "AsyncFunctionDef": "async def",
    "Await": "await",
    "AsyncFor": "async for",
    "AsyncWith": "async with",
    "Nonlocal": "nonlocal",
    "AnnAssign": "anotacion de variable",
    "NamedExpr": "operador :=",
    "MatMult": "operador @",
    "YieldFrom": "yield from",
}

#: Modulos que solo existen en Python 3. Se pueden usar, pero unicamente con
#: un respaldo para 2.7 (``try/except ImportError`` o ``sys.version_info``).
PY3_MODULES = (
    "queue",
    "tkinter",
    "configparser",
    "pathlib",
    "importlib.util",
    "importlib.machinery",
    "urllib.request",
    "urllib.parse",
    "urllib.error",
    "http.client",
    "builtins",
    "statistics",
    "enum",
)

#: Llamadas de arcpy que **no** existen en ArcGIS Desktop 10.x. Si hace falta
#: alguna, se usa con ``getattr`` y con alternativa, no directamente.
PRO_ONLY_CALLS = {
    "arcpy.FromWKT": "no existe en ArcMap; use _geometry_from_wkt",
    "arcpy.FromWKB": "no existe en ArcMap",
    "arcpy.management": (
        "el modulo por alias no es fiable en ArcMap; use la forma "
        "Herramienta_management"
    ),
    "arcpy.conversion": "use la forma Herramienta_conversion",
    "arcpy.analysis": "use la forma Herramienta_analysis",
}

#: Modulos de arcpy que solo existen en una de las dos versiones y por tanto
#: siempre se consultan con ``hasattr`` antes de usarlos.
VERSION_SPECIFIC = ("arcpy.mp", "arcpy.mapping")


def source_files():
    """Todos los archivos de codigo del proyecto, incluida la caja del .pyt."""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in sorted(filenames):
            if filename.endswith(".py") or filename.endswith(".pyt"):
                yield os.path.join(dirpath, filename)


def parse(path):
    with io.open(path, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), path)


def relative(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def dotted_name(node):
    """``arcpy.management.Delete`` a partir del arbol de la llamada."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def guarded_nodes(tree):
    """Nodos que estan dentro de un ``try`` o de un ``if sys.version_info``."""
    guarded = set()
    for node in ast.walk(tree):
        conditional = isinstance(node, ast.Try)
        if isinstance(node, ast.If) and "version_info" in ast.dump(node.test):
            conditional = True
        if conditional:
            for child in ast.walk(node):
                guarded.add(id(child))
    return guarded


class Python27SyntaxTest(unittest.TestCase):
    """Todo el arbol tiene que poder leerlo el Python 2.7 de ArcMap."""

    def test_no_hay_sintaxis_de_python_3(self):
        offenders = []
        for path in source_files():
            tree = parse(path)
            for node in ast.walk(tree):
                kind = type(node).__name__
                if kind in PY3_NODES:
                    offenders.append(
                        "%s:%d %s" % (relative(path), node.lineno, PY3_NODES[kind])
                    )
                elif kind == "FunctionDef":
                    offenders.extend(_function_offenders(path, node))
                elif kind == "Call":
                    offenders.extend(_call_offenders(path, node))
                elif kind in ("List", "Tuple", "Set"):
                    if any(isinstance(item, ast.Starred) for item in node.elts):
                        offenders.append(
                            "%s:%d desempaquetado con * en una lista (PEP 448)"
                            % (relative(path), node.lineno)
                        )
                elif kind == "Dict":
                    if any(key is None for key in node.keys):
                        offenders.append(
                            "%s:%d ** dentro de un diccionario (PEP 448)"
                            % (relative(path), node.lineno)
                        )
                elif kind == "Raise" and node.cause is not None:
                    offenders.append(
                        "%s:%d raise ... from" % (relative(path), node.lineno)
                    )
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_los_modulos_solo_de_python_3_llevan_respaldo(self):
        offenders = []
        for path in source_files():
            tree = parse(path)
            guarded = guarded_nodes(tree)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                for name in names:
                    if name in PY3_MODULES and id(node) not in guarded:
                        offenders.append(
                            "%s:%d importa %s sin respaldo para 2.7"
                            % (relative(path), node.lineno, name)
                        )
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_super_se_llama_con_argumentos(self):
        """``super()`` a secas es sintaxis de Python 3."""
        offenders = []
        for path in source_files():
            for node in ast.walk(parse(path)):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "super"
                    and not node.args
                ):
                    offenders.append("%s:%d" % (relative(path), node.lineno))
        self.assertEqual(offenders, [], "; ".join(offenders))


def _function_offenders(path, node):
    offenders = []
    arguments = node.args
    if arguments.kwonlyargs:
        offenders.append(
            "%s:%d argumentos solo por nombre" % (relative(path), node.lineno)
        )
    if getattr(arguments, "posonlyargs", []):
        offenders.append(
            "%s:%d argumentos solo posicionales" % (relative(path), node.lineno)
        )
    annotated = node.returns is not None or any(
        argument.annotation is not None for argument in arguments.args
    )
    if annotated:
        offenders.append("%s:%d anotaciones de tipo" % (relative(path), node.lineno))
    return offenders


def _call_offenders(path, node):
    """``f(*a)`` y ``f(**k)`` valen en 2.7; repetirlos o mezclarlos, no."""
    offenders = []
    doubles = len([keyword for keyword in node.keywords if keyword.arg is None])
    stars = [
        index
        for index, argument in enumerate(node.args)
        if isinstance(argument, ast.Starred)
    ]
    if doubles > 1 or len(stars) > 1:
        offenders.append(
            "%s:%d desempaquetado multiple (PEP 448)" % (relative(path), node.lineno)
        )
    elif stars and stars[0] != len(node.args) - 1:
        offenders.append(
            "%s:%d * antes de un argumento posicional" % (relative(path), node.lineno)
        )
    return offenders


class ArcpySurfaceTest(unittest.TestCase):
    """Solo se llama al arcpy que existe en ArcMap 10.x."""

    def test_no_se_usan_llamadas_que_solo_estan_en_pro(self):
        offenders = []
        for path in source_files():
            if relative(path).startswith("tests/"):
                continue
            tree = parse(path)
            guarded = guarded_nodes(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = dotted_name(node.func)
                for forbidden, reason in PRO_ONLY_CALLS.items():
                    if name.startswith(forbidden + ".") or name == forbidden:
                        if id(node) in guarded:
                            continue
                        offenders.append(
                            "%s:%d %s: %s" % (relative(path), node.lineno, name, reason)
                        )
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_los_modulos_de_mapa_se_consultan_antes_de_usarse(self):
        """``arcpy.mp`` es de Pro y ``arcpy.mapping`` de ArcMap.

        Ninguno de los dos existe en las dos versiones, asi que el codigo que
        los toca tiene que haber preguntado antes con ``hasattr``.
        """
        for path in source_files():
            with io.open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            for module in VERSION_SPECIFIC:
                if module + "." not in text:
                    continue
                self.assertIn(
                    'hasattr(arcpy, "%s")' % module.split(".")[1],
                    text,
                    "%s usa %s sin comprobar que exista" % (relative(path), module),
                )

    def test_la_forma_de_llamar_a_las_herramientas_es_la_compatible(self):
        """``arcpy.Delete_management`` funciona en ArcMap y en Pro."""
        found = False
        with io.open(
            os.path.join(ROOT, "qfieldesri", "readers", "arcpy_reader.py"),
            "r",
            encoding="utf-8",
        ) as handle:
            text = handle.read()
        for tool in ("Delete_management", "MakeFeatureLayer_management"):
            self.assertIn("arcpy.%s(" % tool, text)
            found = True
        self.assertTrue(found)


if __name__ == "__main__":
    unittest.main()
