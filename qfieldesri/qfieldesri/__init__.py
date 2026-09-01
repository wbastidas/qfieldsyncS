# -*- coding: utf-8 -*-
"""qfieldESRI: migracion de geodatabases de ESRI a QField y de vuelta.

Estructura del paquete::

    core/       modelo de metadatos, configuracion, empaquetado, verificacion,
                sincronizacion de vuelta, adjuntos y cliente de QFieldCloud
    readers/    lectura de la geodatabase (arcpy, OGR, memoria)
    writers/    escritura del GeoPackage y del proyecto QGIS que abre QField
    profiles/   curaduria del modelo de datos (CNEL EP o generico)
    utils/      WKB, huellas de entidad y utilidades de SQLite/GeoPackage

Ninguna parte de ``core``, ``writers``, ``profiles`` ni ``utils`` importa
arcpy: solo lo hace ``readers.arcpy_reader``. Por eso el complemento se puede
probar y automatizar fuera de ArcGIS.
"""

from .version import __version__

__all__ = ["__version__"]
