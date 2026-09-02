# -*- coding: utf-8 -*-
"""Huella de una entidad, para detectar que cambio en campo.

Se calcula sobre los valores **ya adaptados al GeoPackage** y sobre el WKB
normalizado, de modo que la huella tomada al empaquetar (desde la geodatabase)
y la tomada al sincronizar (desde el GeoPackage devuelto) sean comparables.
Solo entran los campos que se pueden reescribir en la geodatabase: un cambio en
un campo de solo lectura no deberia contar como edicion de campo.
"""

import hashlib

_SEPARATOR = b"\x1f"


def _binary_types():
    """Tipos binarios de cada version de Python.

    ``sqlite3.Binary`` es ``memoryview`` en Python 3 y ``buffer`` en el 2.7 de
    ArcMap; los dos tienen que dar la misma huella que sus bytes.
    """
    types = [bytearray, memoryview]
    try:
        # ``buffer`` solo existe en Python 2.7; en 3.x levanta NameError.
        types.append(buffer)
    except NameError:
        pass
    return tuple(types)


_BINARY_TYPES = _binary_types()


def _encode(value):
    if value is None:
        return b"\x00"
    if isinstance(value, bytes):
        return value
    if isinstance(value, _BINARY_TYPES):
        # Un binario tiene que entrar por su contenido y no por su
        # representacion: ``str(memoryview)`` incluye la direccion de memoria y
        # la huella dejaria de ser reproducible entre ejecuciones.
        return bytes(value)
    if isinstance(value, float):
        # repr() de un float es estable y reversible en Python 2.7 y 3.x.
        return repr(value).encode("utf-8")
    if isinstance(value, bool):
        return b"1" if value else b"0"
    try:
        text = unicode(value)
    except NameError:
        text = str(value)
    return text.encode("utf-8")


def feature_checksum(attributes, field_names, wkb=None):
    """Huella hexadecimal de una entidad."""
    # md5 se usa para detectar cambios, no con fines de seguridad.
    digest = hashlib.md5()  # noqa: S324  # nosec B324
    for name in field_names:
        digest.update(_encode(attributes.get(name)))
        digest.update(_SEPARATOR)
    if wkb:
        digest.update(bytes(wkb))
    return digest.hexdigest()
