# -*- coding: utf-8 -*-
"""Guardia de dependencias.

qfieldESRI es un programa externo que trabaja contra ArcGIS y **no depende de
QGIS ni de Qt en ninguna parte**. Eso es facil de decir y facil de romper sin
darse cuenta: basta con que alguien importe una utilidad comoda. Esta prueba
recorre todo el codigo fuente y falla si aparece cualquier importacion que no
sea la biblioteca estandar, el propio paquete o las dos dependencias opcionales
declaradas (``arcpy`` en su lector, ``osgeo`` en el suyo).

Tambien comprueba lo contrario de lo obvio: que el nucleo se pueda importar
entero **sin arcpy instalado**, que es lo que permite probarlo y automatizarlo
fuera de ArcGIS.
"""

import ast
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = os.path.join(ROOT, "qfieldesri")

#: Nada de esto puede aparecer en ninguna parte del codigo.
FORBIDDEN_ROOTS = (
    "qgis",
    "PyQt",
    "PyQt4",
    "PyQt5",
    "PyQt6",
    "PySide",
    "PySide2",
    "PySide6",
    "qgis.core",
    "qgis.gui",
    "processing",
    "libqfieldsync",
)

#: Dependencias externas admitidas y los archivos donde pueden aparecer.
ALLOWED_EXTERNAL = {
    "arcpy": (
        "qfieldesri/readers/arcpy_reader.py",
        "qfieldesri/core/attachments.py",
        "qfieldesri/symbology/arcgis.py",
        "qfieldesri/launcher.py",
        "qfieldesri/app.py",
        "QFieldESRI.pyt",
    ),
    "osgeo": ("qfieldesri/readers/ogr_reader.py",),
}

#: Donde SI puede importarse arcpy en la primera linea del modulo. En el resto
#: tiene que ser una importacion perezosa, dentro de la funcion que la usa: de
#: lo contrario el nucleo dejaria de poder cargarse sin ArcGIS.
ARCPY_AT_MODULE_LEVEL = (
    "qfieldesri/readers/arcpy_reader.py",
    "QFieldESRI.pyt",
)

#: Modulos de la biblioteca estandar que usa el proyecto. Se listan de forma
#: explicita para que anadir una dependencia nueva sea una decision consciente.
STDLIB = {
    "__future__",
    "argparse",
    "ast",
    "codecs",
    "collections",
    "contextlib",
    "csv",
    "datetime",
    "getpass",
    "glob",
    "hashlib",
    "imp",
    "importlib",
    "io",
    "json",
    "logging",
    "math",
    "mimetypes",
    "os",
    "platform",
    "Queue",
    "queue",
    "re",
    "shutil",
    "socket",
    "sqlite3",
    "ssl",
    "string",
    "struct",
    "subprocess",
    "sys",
    "tempfile",
    "threading",
    "time",
    "traceback",
    "types",
    "unittest",
    "urllib",
    "urllib2",
    "urlparse",
    "uuid",
    "warnings",
    "winreg",
    "_winreg",
    "xml",
    # Interfaz grafica: Tkinter viene incluido en el Python de ArcGIS.
    "tkinter",
    "Tkinter",
    "ttk",
    "tkFileDialog",
    "tkMessageBox",
}


def python_files():
    """Todos los archivos de codigo del proyecto, incluido el .pyt."""
    for directory in (PACKAGE, os.path.join(ROOT, "tools")):
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames[:] = [name for name in dirnames if name != "__pycache__"]
            for filename in filenames:
                if filename.endswith(".py"):
                    yield os.path.join(dirpath, filename)
    for filename in ("QFieldESRI.pyt", "QFieldESRI.py"):
        yield os.path.join(ROOT, filename)


def _roots_of(nodes):
    roots = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # importacion relativa: es el propio paquete
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _parse(path):
    with io.open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def imported_roots(path):
    """Modulos de primer nivel importados por un archivo, en cualquier sitio."""
    return _roots_of(ast.walk(_parse(path)))


def module_level_roots(path):
    """Solo los que se importan al cargar el modulo, no dentro de funciones."""
    tree = _parse(path)
    nodes = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    for node in tree.body:
        # Las importaciones bajo un try/except de compatibilidad tambien
        # ocurren al cargar el modulo.
        if isinstance(node, ast.Try):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    nodes.append(child)
    return _roots_of(nodes)


class DependencyTest(unittest.TestCase):
    def test_hay_codigo_que_revisar(self):
        files = list(python_files())
        self.assertGreater(len(files), 20)
        for path in files:
            self.assertTrue(os.path.isfile(path), path)

    def test_ningun_import_de_qgis_ni_de_qt(self):
        offenders = []
        for path in python_files():
            relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for root in imported_roots(path):
                if root in FORBIDDEN_ROOTS:
                    offenders.append("%s importa %s" % (relative, root))
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_las_dependencias_externas_estan_donde_toca(self):
        offenders = []
        for path in python_files():
            relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for root in imported_roots(path):
                if root in ALLOWED_EXTERNAL:
                    if relative not in ALLOWED_EXTERNAL[root]:
                        offenders.append("%s importa %s" % (relative, root))
                    continue
                if root in STDLIB or root == "qfieldesri":
                    continue
                offenders.append("%s importa %s (dependencia nueva)" % (relative, root))
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_arcpy_solo_se_carga_donde_corresponde(self):
        offenders = []
        for path in python_files():
            relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if relative in ARCPY_AT_MODULE_LEVEL:
                continue
            if "arcpy" in module_level_roots(path):
                offenders.append(relative)
        self.assertEqual(
            offenders,
            [],
            "arcpy debe importarse dentro de la funcion que lo usa en: %s"
            % ", ".join(offenders),
        )

    def test_el_nucleo_se_importa_sin_arcpy(self):
        # Si esto falla, qfieldESRI habria dejado de poder probarse y
        # automatizarse fuera de ArcGIS.
        self.assertNotIn("arcpy", sys.modules)
        for name in (
            "qfieldesri.core.packager",
            "qfieldesri.core.synchronizer",
            "qfieldesri.core.checker",
            "qfieldesri.core.scope",
            "qfieldesri.core.cloudapi",
            "qfieldesri.writers.geopackage",
            "qfieldesri.writers.qfield_project",
            "qfieldesri.profiles",
            "qfieldesri.utils.wkb",
            "qfieldesri.cli",
        ):
            __import__(name)
        self.assertNotIn("arcpy", sys.modules)

    def test_el_lector_de_arcpy_avisa_en_vez_de_reventar(self):
        from qfieldesri.readers import ReaderError, get_reader

        # Sin arcpy ni GDAL disponibles, la fabrica explica que falta en vez
        # de dejar escapar un ImportError a medio camino.
        with self.assertRaises(ReaderError) as context:
            get_reader("C:/datos/no_existe.gdb")
        self.assertIn("arcpy", str(context.exception))

    def test_el_paquete_no_declara_dependencias_de_instalacion(self):
        path = os.path.join(ROOT, "pyproject.toml")
        with io.open(path, encoding="utf-8") as handle:
            content = handle.read()
        # Un bloque de dependencias significaria que hace falta pip install.
        self.assertNotIn("dependencies = [", content)
        self.assertNotIn("install_requires", content)


if __name__ == "__main__":
    unittest.main()
