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

#: Rutas de ArcMap 10.x (Python 2.7 de 32 y 64 bits).
ARCMAP_PATTERNS = (
    r"C:\Python27\ArcGIS*\python.exe",
    r"C:\Python27\ArcGISx64*\python.exe",
    r"C:\Python27\python.exe",
)


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


def _from_registry():
    """Instalaciones de ArcGIS declaradas en el registro de Windows."""
    try:
        import winreg
    except ImportError:
        try:  # pragma: no cover - Python 2.7 (ArcMap)
            import _winreg as winreg
        except ImportError:
            return []

    candidates = []
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ESRI\ArcGISPro", "InstallDir"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ESRI\Desktop10.8", "PythonDir"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ESRI\Desktop10.7", "PythonDir"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ESRI\Desktop10.6", "PythonDir"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ESRI\Desktop10.5", "PythonDir"),
    )
    for root, path, value_name in keys:
        try:
            with winreg.OpenKey(root, path) as key:
                install_dir = winreg.QueryValueEx(key, value_name)[0]
        except OSError:
            continue
        if not install_dir:
            continue
        candidates.extend(
            glob.glob(
                os.path.join(install_dir, "bin", "Python", "envs", "*", "python.exe")
            )
        )
        candidates.extend(glob.glob(os.path.join(install_dir, "*", "python.exe")))
    return candidates


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
        "  - el registro de Windows (ArcGIS Pro y ArcMap 10.5 a 10.8)\n"
        "  - las rutas habituales de instalacion\n\n"
        "Solucion: defina %s con la ruta completa de python.exe, por ejemplo\n"
        "  set %s=C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\"
        "arcgispro-py3\\python.exe" % (ENV_VAR, sys.executable, ENV_VAR, ENV_VAR)
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
