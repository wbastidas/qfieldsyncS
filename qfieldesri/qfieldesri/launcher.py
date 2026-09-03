# -*- coding: utf-8 -*-
r"""Localiza el Python de ArcGIS y arranca qfieldESRI con el.

El problema practico que resuelve: ``arcpy`` solo funciona con el interprete
que instala ArcGIS, y ese interprete no suele estar en el ``PATH``. Sin esto,
el usuario tendria que averiguar a mano donde vive
``...\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe`` y escribirlo
cada vez.

Orden de busqueda, de lo mas fiable a lo mas aproximado:

1. la variable de entorno ``QFIELDESRI_PYTHON``, si el usuario la fijo;
2. el propio interprete en curso, si ya trae ``arcpy``;
3. lo que diga el registro de Windows sobre las instalaciones de ArcGIS;
4. las rutas habituales de ArcGIS Pro y ArcMap en disco.

Funciona en Python 2.7 y 3.x, y en un equipo sin ArcGIS informa de por que no
lo encuentra en vez de fallar con un ``ImportError`` a media ejecucion.
"""

import glob
import os
import subprocess
import sys

#: Si el usuario la define, manda sobre todo lo demas.
ENV_VAR = "QFIELDESRI_PYTHON"

#: Rutas donde ArcGIS Pro suele dejar su entorno de conda.
PRO_PATTERNS = (
    r"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe",
    r"C:\Program Files\ArcGIS\Pro\bin\Python\envs\*\python.exe",
    os.path.expanduser(
        r"~\AppData\Local\Programs\ArcGIS\Pro\bin\Python\envs\*\python.exe"
    ),
    os.path.expanduser(r"~\AppData\Local\ESRI\conda\envs\*\python.exe"),
)

#: Rutas de ArcMap 10.x (Python 2.7 de 32 y 64 bits). ``ArcGIS10.x`` es el
#: interprete normal; ``ArcGISx6410.x`` el del geoprocesamiento en segundo
#: plano, que tambien trae arcpy.
ARCMAP_PATTERNS = (
    r"C:\Python27\ArcGIS*\python.exe",
    r"C:\Python27\ArcGISx64*\python.exe",
    r"C:\Python27\python.exe",
)

#: Versiones de ArcGIS Desktop que se buscan en el registro, de la mas nueva a
#: la mas antigua. 10.4 es la primera con la que se ha probado qfieldESRI.
DESKTOP_VERSIONS = ("10.8", "10.7", "10.6", "10.5", "10.4")


class LauncherError(Exception):
    pass


def has_arcpy(python_executable=None):
    """``True`` si ese interprete puede importar arcpy."""
    if python_executable is None:
        try:
            import arcpy  # noqa: F401
        except ImportError:
            return False
        return True

    try:
        with open(os.devnull, "w") as devnull:
            # La ruta viene de nuestra propia busqueda, no de entrada externa.
            code = subprocess.call(  # noqa: S603
                [python_executable, "-c", "import arcpy"],
                stdout=devnull,
                stderr=subprocess.STDOUT,
            )
    except OSError:
        return False
    return code == 0


def _registry_keys():
    r"""Claves del registro donde ESRI declara donde vive su Python.

    ArcGIS Desktop lo publica en ``SOFTWARE\ESRI\Python10.x`` con el valor
    ``PythonDir``, que apunta a la carpeta que contiene ``ArcGIS10.x`` (o
    ``ArcGISx6410.x`` si esta instalado el geoprocesamiento de 64 bits); ese
    es el camino bueno para ArcMap. ``Desktop10.x`` / ``InstallDir`` se
    consulta como respaldo, y ``ArcGISPro`` para quien tenga Pro.
    """
    keys = [(r"SOFTWARE\ESRI\ArcGISPro", "InstallDir")]
    for version in DESKTOP_VERSIONS:
        keys.append((r"SOFTWARE\ESRI\Python%s" % version, "PythonDir"))
        keys.append((r"SOFTWARE\ESRI\Desktop%s" % version, "InstallDir"))
    return keys


def _from_registry():
    """Instalaciones de ArcGIS declaradas en el registro de Windows."""
    try:
        import winreg
    except ImportError:
        try:  # pragma: no cover - Python 2.7 (ArcMap)
            import _winreg as winreg
        except ImportError:
            return []

    # ArcGIS Desktop es una aplicacion de 32 bits: en un Windows de 64 bits sus
    # claves viven en la vista de 32 bits. Se miran las dos, porque el
    # lanzador puede estar corriendo en cualquiera de los dos interpretes.
    views = [0]
    for flag in ("KEY_WOW64_32KEY", "KEY_WOW64_64KEY"):
        value = getattr(winreg, flag, None)
        if value is not None:
            views.append(value)

    candidates = []
    for path, value_name in _registry_keys():
        for view in views:
            directory = _registry_value(winreg, path, value_name, view)
            if not directory:
                continue
            # ArcGIS Pro: entornos de conda bajo bin\Python\envs.
            candidates.extend(
                glob.glob(
                    os.path.join(directory, "bin", "Python", "envs", "*", "python.exe")
                )
            )
            # ArcMap: C:\Python27\ArcGIS10.6\python.exe y su gemelo de 64 bits.
            candidates.extend(glob.glob(os.path.join(directory, "*", "python.exe")))
            candidates.extend(glob.glob(os.path.join(directory, "python.exe")))
    return candidates


def _registry_value(winreg, path, value_name, view):
    """Lee un valor del registro, o ``None`` si no esta."""
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | view)
    except (OSError, EnvironmentError):  # noqa: UP024 - 2.7 lanza WindowsError
        return None
    try:
        return winreg.QueryValueEx(key, value_name)[0]
    except (OSError, EnvironmentError):  # noqa: UP024
        return None
    finally:
        key.Close()


def find_python(check_arcpy=True):
    """Devuelve la ruta del Python de ArcGIS, o ``None`` si no aparece."""
    configured = os.environ.get(ENV_VAR)
    if configured and os.path.isfile(configured):
        return configured

    if has_arcpy():
        # Ya estamos dentro del Python correcto.
        return sys.executable

    candidates = list(_from_registry())
    for pattern in PRO_PATTERNS + ARCMAP_PATTERNS:
        candidates.extend(glob.glob(pattern))

    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen or not os.path.isfile(candidate):
            continue
        seen.add(normalized)
        if not check_arcpy or has_arcpy(candidate):
            return candidate
    return None


def describe_search():
    """Texto de ayuda cuando no se encuentra el interprete."""
    return (
        "No se encontro el Python de ArcGIS.\n\n"
        "Se busco en:\n"
        "  - la variable de entorno %s\n"
        "  - el interprete actual (%s)\n"
        "  - el registro de Windows (ArcGIS Pro y ArcGIS Desktop 10.4 a 10.8)\n"
        "  - las rutas habituales de instalacion\n\n"
        "Solucion: defina %s con la ruta completa de python.exe. En ArcMap\n"
        "suele ser\n"
        "  set %s=C:\\Python27\\ArcGIS10.6\\python.exe\n"
        "y en ArcGIS Pro\n"
        "  set %s=C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\"
        "arcgispro-py3\\python.exe"
        % (ENV_VAR, sys.executable, ENV_VAR, ENV_VAR, ENV_VAR)
    )


def relaunch(argv=None):
    """Vuelve a lanzar la aplicacion con el Python de ArcGIS.

    Si el interprete en curso ya sirve, devuelve ``None`` para que quien llama
    siga en el mismo proceso; si no, arranca el otro y devuelve su codigo de
    salida.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if has_arcpy():
        return None

    executable = find_python()
    if executable is None:
        raise LauncherError(describe_search())

    package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    command = [executable, "-m", "qfieldesri.app"] + argv
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        package_dir + os.pathsep + existing if existing else package_dir
    )
    # El ejecutable lo localizamos nosotros y se comprobo que importa arcpy.
    return subprocess.call(command, env=environment)  # noqa: S603


def main(argv=None):
    """Punto de entrada del lanzador."""
    try:
        code = relaunch(argv)
    except LauncherError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    if code is None:
        from .app import main as app_main

        return app_main(argv)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
