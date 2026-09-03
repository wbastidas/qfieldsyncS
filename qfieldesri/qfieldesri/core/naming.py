# -*- coding: utf-8 -*-
"""Como se llama una clase, segun quien la mire.

Una File Geodatabase devuelve ``Barra``. La misma clase en una geodatabase
corporativa de Oracle con ArcSDE llega como ``SIGELEC.BARRA``: calificada con el
usuario propietario y en mayusculas, porque asi la guarda Oracle. Y si manana
se conecta otro usuario, la misma clase llega como ``SDE.BARRA``.

Nada de eso cambia la clase. Cambia la etiqueta con la que el servidor la
nombra. Por eso todas las comparaciones de qfieldESRI —el perfil, la
configuracion por capa, el ambito de exportacion, la simbologia y, sobre todo,
la sincronizacion de vuelta— pasan por aqui: se compara **la clase**, no la
cadena que la nombra hoy.

Esto importa especialmente al volver de campo: el paquete se pudo generar con
una conexion y sincronizarse con otra, y no seria razonable que el material
capturado se perdiera porque el propietario del esquema es distinto.
"""

#: Separador de la calificacion en ArcSDE: ``base.propietario.Clase`` en SQL
#: Server, ``PROPIETARIO.CLASE`` en Oracle.
QUALIFIER = "."


def short_name(name):
    """Nombre de la clase sin el esquema ni la base.

    ``SIGELEC.BARRA`` -> ``BARRA``; ``sde.DBO.Barra`` -> ``Barra``; ``Barra``
    se queda como esta.
    """
    if not name:
        return ""
    return str(name).split(QUALIFIER)[-1]


def normalize(name):
    """Forma con la que se compara: sin esquema y sin mayusculas."""
    return short_name(name).lower()


def same_class(one, other):
    """``True`` si los dos nombres designan la misma clase."""
    if not one or not other:
        return False
    if str(one).lower() == str(other).lower():
        return True
    return normalize(one) == normalize(other)


def find(names, wanted):
    """Devuelve el nombre de ``names`` que designa la misma clase que ``wanted``.

    Primero busca la coincidencia exacta —si la geodatabase tiene ``BARRA`` y
    ``SIGELEC.BARRA``, hay que quedarse con la que se pidio— y solo despues afloja
    la comparacion.
    """
    if not wanted:
        return None
    names = list(names)
    lowered = str(wanted).lower()
    for name in names:
        if str(name).lower() == lowered:
            return name
    key = normalize(wanted)
    for name in names:
        if normalize(name) == key:
            return name
    return None


def index(names):
    """``{clase normalizada: nombre tal como viene}`` para busquedas repetidas."""
    result = {}
    for name in names:
        result.setdefault(normalize(name), name)
    return result
