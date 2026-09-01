# -*- coding: utf-8 -*-
r"""Arranque de la aplicacion de escritorio de qfieldESRI.

Doble clic en este archivo, o::

    python QFieldESRI.py [ruta\\a\\la.gdb]

Si el Python con el que se abre no trae ``arcpy``, el lanzador busca el de
ArcGIS en el equipo y vuelve a arrancar la aplicacion con el, de modo que el
usuario no tenga que saber donde esta instalado.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qfieldesri.launcher import main

if __name__ == "__main__":
    sys.exit(main())
