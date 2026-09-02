"""Configuracion de empaquetado de qfieldESRI.

Es el equivalente de ``SyncAction`` + ``ProjectConfig`` de QFieldSync, pero
guardado en un JSON propio. Ese JSON es lo que comparten la aplicacion de
escritorio, el Python Toolbox de ArcGIS y la linea de comandos, y es tambien lo
que permite repetir un empaquetado identico mes a mes.
"""

import io
import json

from .model import (
    CATEGORY_CONNECTIVITY,
    CATEGORY_CORE,
    CATEGORY_OTHER,
    CATEGORY_SYSTEM,
)
from .scope import Scope


class LayerAction(object):
    """Que hacer con cada clase de la geodatabase al empaquetar.

    Los nombres siguen los de ``libqfieldsync.layer.SyncAction`` para que quien
    venga de QFieldSync reconozca el vocabulario.
    """

    #: Copiar los datos al GeoPackage y permitir edicion en campo.
    COPY = "copy"
    #: Copiar los datos pero bloquear la edicion (capas de contexto).
    READ_ONLY = "read_only"
    #: Copiar solo el esquema, sin entidades (formularios de captura nueva).
    EMPTY = "empty"
    #: No incluir la capa en el paquete.
    REMOVE = "remove"

    ALL = (COPY, READ_ONLY, EMPTY, REMOVE)


class GeometryLock(object):
    """Nivel de bloqueo de geometria que se traslada a QField."""

    NONE = "none"
    LOCKED = "locked"


DEFAULT_ATTACHMENT_DIRS = ("DCIM", "audio", "video", "files")

#: Categorias que se envian a campo por defecto. Las de sistema viajan
#: ocultas (se necesitan para la sincronizacion) y las "otras" se incluyen
#: porque el propio catalogo advierte que "otro" no significa "innecesario".
DEFAULT_VISIBLE_CATEGORIES = (
    CATEGORY_CORE,
    CATEGORY_CONNECTIVITY,
    CATEGORY_OTHER,
)


class LayerConfig(object):
    """Configuracion de empaquetado de una clase concreta."""

    def __init__(
        self,
        name,
        action=LayerAction.COPY,
        where_clause="",
        group=None,
        visible=True,
        geometry_lock=GeometryLock.NONE,
        allow_feature_addition=True,
        allow_feature_deletion=False,
        visible_categories=None,
        hidden_fields=None,
        readonly_fields=None,
        attachment_fields=None,
        display_field=None,
        max_features=0,
    ):
        self.name = name
        self.action = action
        #: filtro SQL aplicado al leer de la geodatabase (definition query)
        self.where_clause = where_clause
        #: grupo del arbol de capas del proyecto QField
        self.group = group
        self.visible = visible
        self.geometry_lock = geometry_lock
        self.allow_feature_addition = allow_feature_addition
        #: en una red electrica borrar en campo casi nunca es deseable
        self.allow_feature_deletion = allow_feature_deletion
        self.visible_categories = list(
            visible_categories
            if visible_categories is not None
            else DEFAULT_VISIBLE_CATEGORIES
        )
        self.hidden_fields = list(hidden_fields or [])
        self.readonly_fields = list(readonly_fields or [])
        #: ``{campo: "image"|"file"|"audio"|"video"}``
        self.attachment_fields = dict(attachment_fields or {})
        self.display_field = display_field
        #: 0 = sin limite; util para pruebas de campo rapidas
        self.max_features = max_features

    @property
    def is_included(self):
        return self.action != LayerAction.REMOVE

    @property
    def is_editable(self):
        return self.action in (LayerAction.COPY, LayerAction.EMPTY)

    def to_dict(self):
        return {
            "name": self.name,
            "action": self.action,
            "where_clause": self.where_clause,
            "group": self.group,
            "visible": self.visible,
            "geometry_lock": self.geometry_lock,
            "allow_feature_addition": self.allow_feature_addition,
            "allow_feature_deletion": self.allow_feature_deletion,
            "visible_categories": self.visible_categories,
            "hidden_fields": self.hidden_fields,
            "readonly_fields": self.readonly_fields,
            "attachment_fields": self.attachment_fields,
            "display_field": self.display_field,
            "max_features": self.max_features,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


class PackagingConfig(object):
    """Todo lo que define un empaquetado GDB -> QField."""

    def __init__(
        self,
        workspace,
        output_dir,
        project_name="qfieldesri",
        title=None,
        profile="cnel_ep",
        layers=None,
        area_of_interest=None,
        area_of_interest_crs=None,
        include_related_tables=True,
        include_attachments=True,
        attachment_dirs=DEFAULT_ATTACHMENT_DIRS,
        max_image_width_height=1600,
        value_map_button_threshold=6,
        big_domain_threshold=40,
        digitizing_logs_layer=None,
        initial_map_mode="browse",
        basemap_layer=None,
        crs_code=None,
        spatial_index=True,
        connection_note="",
        scope=None,
        symbology_source=None,
        style_file=None,
    ):
        #: ruta de la File Geodatabase (.gdb) o del ``.sde`` corporativo
        self.workspace = workspace
        self.output_dir = output_dir
        self.project_name = project_name
        self.title = title or project_name
        #: perfil de modelo de datos (``cnel_ep`` o ``generico``)
        self.profile = profile
        #: ``{nombre_clase: LayerConfig}``
        self.layers = dict(layers or {})
        #: WKT del poligono de area de interes (recorte espacial)
        self.area_of_interest = area_of_interest
        self.area_of_interest_crs = area_of_interest_crs
        self.include_related_tables = include_related_tables
        self.include_attachments = include_attachments
        self.attachment_dirs = list(attachment_dirs)
        self.max_image_width_height = max_image_width_height
        #: por debajo de este numero de valores, QField dibuja botones en vez
        #: de un desplegable (equivale a ``value_map_button_interface_threshold``)
        self.value_map_button_threshold = value_map_button_threshold
        #: los dominios con mas miembros que esto no se vuelcan como ValueMap
        #: sino como tabla de catalogo + ValueRelation, para que el .qgs no
        #: crezca sin control (el modelo CNEL tiene dominios de 1853 valores)
        self.big_domain_threshold = big_domain_threshold
        self.digitizing_logs_layer = digitizing_logs_layer
        self.initial_map_mode = initial_map_mode
        self.basemap_layer = basemap_layer
        #: CRS de salida; ``None`` = el de la geodatabase (EPSG:32717 en CNEL)
        self.crs_code = crs_code
        self.spatial_index = spatial_index
        #: nota libre que se guarda en el manifiesto (p. ej. version SDE)
        self.connection_note = connection_note
        #: ambito de exportacion (alimentador, subestacion, poligono...).
        #: Es lo que decide que subconjunto de la red viaja al dispositivo.
        self.scope = scope if isinstance(scope, Scope) else Scope.from_dict(scope)
        #: carpeta de archivos .lyrx, o un .lyrx/.lyr/.mxd del que importar la
        #: simbologia de ArcGIS
        self.symbology_source = symbology_source
        #: archivo de estilo propio; manda sobre todo lo demas
        self.style_file = style_file

    # ------------------------------------------------------------------
    def layer_config(self, name):
        """Configuracion de una capa, creando una por defecto si no existe."""
        if name not in self.layers:
            self.layers[name] = LayerConfig(name)
        return self.layers[name]

    def included_layer_names(self):
        return [
            name for name, config in sorted(self.layers.items()) if config.is_included
        ]

    # ------------------------------------------------------------------
    def to_dict(self):
        return {
            "workspace": self.workspace,
            "output_dir": self.output_dir,
            "project_name": self.project_name,
            "title": self.title,
            "profile": self.profile,
            "layers": {name: config.to_dict() for name, config in self.layers.items()},
            "area_of_interest": self.area_of_interest,
            "area_of_interest_crs": self.area_of_interest_crs,
            "include_related_tables": self.include_related_tables,
            "include_attachments": self.include_attachments,
            "attachment_dirs": self.attachment_dirs,
            "max_image_width_height": self.max_image_width_height,
            "value_map_button_threshold": self.value_map_button_threshold,
            "big_domain_threshold": self.big_domain_threshold,
            "digitizing_logs_layer": self.digitizing_logs_layer,
            "initial_map_mode": self.initial_map_mode,
            "basemap_layer": self.basemap_layer,
            "crs_code": self.crs_code,
            "spatial_index": self.spatial_index,
            "connection_note": self.connection_note,
            "scope": self.scope.to_dict(),
            "symbology_source": self.symbology_source,
            "style_file": self.style_file,
        }

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        layers = {
            name: LayerConfig.from_dict(layer_data)
            for name, layer_data in (data.pop("layers", None) or {}).items()
        }
        return cls(layers=layers, **data)

    def save(self, path):
        with io.open(path, "w", encoding="utf-8") as handle:
            text = json.dumps(
                self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
            )
            handle.write(text if isinstance(text, type("")) else text.decode("utf-8"))

    @classmethod
    def load(cls, path):
        with io.open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


#: Categorias reconocidas, reexportadas para comodidad de quien importe config.
CATEGORIES = (
    CATEGORY_CORE,
    CATEGORY_CONNECTIVITY,
    CATEGORY_SYSTEM,
    CATEGORY_OTHER,
)
