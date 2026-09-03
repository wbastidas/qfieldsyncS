"""Contrato comun de los lectores de geodatabase.

Un lector traduce una fuente concreta (File Geodatabase via arcpy, geodatabase
corporativa via arcpy/SDE, o un OpenFileGDB via GDAL) al modelo neutro de
``qfieldesri.core.model``. El empaquetador y el sincronizador solo hablan con
esta interfaz, que es lo que deja la puerta abierta a la geodatabase
corporativa sin tocar el resto del complemento.
"""


class ReaderError(Exception):
    """Error al abrir o consultar la geodatabase."""


class GeodatabaseReader(object):
    """Interfaz que deben cumplir todos los lectores."""

    #: nombre corto para mensajes y para el manifiesto del paquete
    name = "base"

    #: ``True`` si el lector puede devolver los cambios a la fuente
    supports_write = False

    def __init__(self, workspace):
        self.workspace = workspace

    # -- ciclo de vida --------------------------------------------------
    def open(self):
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    # -- metadatos ------------------------------------------------------
    def describe_workspace(self, layer_names=None):
        """Devuelve un :class:`~qfieldesri.core.model.WorkspaceInfo`.

        ``layer_names`` limita la inspeccion a esas clases (util en una
        geodatabase corporativa con cientos de clases, donde describirlas todas
        cuesta minutos).
        """
        raise NotImplementedError

    # -- lectura --------------------------------------------------------
    def iter_features(
        self,
        layer_info,
        field_names,
        where_clause=None,
        aoi_wkt=None,
        aoi_crs=None,
        limit=0,
    ):
        """Itera ``(wkb, {campo: valor})`` de una clase.

        ``wkb`` es ``None`` en tablas sin geometria o en entidades con
        geometria nula.
        """
        raise NotImplementedError

    def count_features(self, layer_info, where_clause=None):
        raise NotImplementedError

    def delimit_field(self, layer_info, name):
        """Cita el nombre de un campo como lo espera el motor de la fuente.

        Una File Geodatabase usa comillas dobles y una geodatabase corporativa
        depende del gestor; arcpy lo resuelve con ``AddFieldDelimiters``.
        """
        return name

    def union_wkt(self, layer_name, where_clause=None):
        """Une las entidades de una clase de poligonos en un unico WKT.

        Es lo que convierte "el poligono del sector" que elige el usuario en el
        area de interes que se aplica al recorte. Devuelve ``(wkt, codigo_epsg)``
        o ``(None, None)`` si el motor no sabe hacerlo.
        """
        return None, None

    # -- escritura (sincronizacion de vuelta) ---------------------------
    def start_editing(self, versioned=None):
        """Abre una sesion de edicion.

        ``versioned`` dice si los datos que se van a editar estan registrados
        como versionados. Importa solo en una geodatabase corporativa, y ahi
        importa mucho: ArcGIS abre la sesion de una forma para datos
        versionados y de otra para datos no versionados, y equivocarse no da
        un aviso, da un error. ``None`` significa "deducelo de la
        geodatabase".
        """

    def stop_editing(self, save=True):
        """Cierra la sesion de edicion."""

    def update_feature(self, layer_info, key_field, key_value, attributes, wkb=None):
        raise NotImplementedError

    def insert_feature(self, layer_info, attributes, wkb=None):
        raise NotImplementedError

    def delete_feature(self, layer_info, key_field, key_value):
        raise NotImplementedError


def get_reader(workspace, prefer=None):
    """Devuelve el lector adecuado para ``workspace``.

    Se prueba arcpy primero (es lo que habra en ArcGIS Desktop) y se cae a GDAL
    si no esta disponible, de modo que el mismo codigo sirva para automatizar
    fuera de ArcGIS.
    """
    if prefer == "memory":
        from .memory import MemoryReader

        return MemoryReader(workspace)

    errors = []
    if prefer in (None, "arcpy"):
        try:
            from .arcpy_reader import ArcpyReader

            return ArcpyReader(workspace)
        except ImportError as error:
            errors.append("arcpy: %s" % error)

    if prefer in (None, "ogr"):
        try:
            from .ogr_reader import OgrReader

            return OgrReader(workspace)
        except ImportError as error:
            errors.append("gdal/ogr: %s" % error)

    raise ReaderError(
        "No hay ningun motor de lectura disponible para '%s'. Detalle: %s"
        % (workspace, "; ".join(errors))
    )
