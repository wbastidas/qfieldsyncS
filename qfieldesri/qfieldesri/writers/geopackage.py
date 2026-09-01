"""Escritor OGC GeoPackage 1.3 implementado solo con ``sqlite3``.

Motivo de existir
-----------------
ArcGIS Desktop (ArcMap 10.x, Python 2.7) no expone GDAL/OGR desde ``arcpy`` y
su herramienta nativa de exportacion a GeoPackage depende de la version
instalada y no controla el detalle que QField necesita (nombres de capa,
columna de clave tecnica, indice espacial, tipos de campo). Este modulo escribe
el contenedor directamente: funciona igual en ArcMap, en ArcGIS Pro o en un
Python limpio, lo que ademas permite probarlo sin ArcGIS instalado.

Lo que produce es un GeoPackage valido segun la especificacion OGC: tablas
``gpkg_spatial_ref_sys``, ``gpkg_contents``, ``gpkg_geometry_columns``, indice
espacial R-Tree con el juego completo de disparadores del estandar y la
extension ``gpkg_ogr_contents`` que usa GDAL (y por tanto QGIS y QField) para
contar entidades sin recorrer la tabla.
"""

import datetime
import os
import sqlite3
import struct

from ..utils import wkb as wkb_utils

APPLICATION_ID = 0x47504B47  # 'GPKG'
USER_VERSION = 10300  # GeoPackage 1.3


class GeoPackageError(Exception):
    pass


def esri_type_to_gpkg(esri_type, length=None):
    """Traduce un tipo de campo de ESRI al tipo de columna de GeoPackage."""
    esri_type = (esri_type or "").lower()
    if esri_type in ("smallinteger", "short", "int16"):
        return "SMALLINT"
    if esri_type in ("integer", "long", "int32"):
        return "MEDIUMINT"
    if esri_type in ("biginteger", "int64", "oid"):
        return "INTEGER"
    if esri_type in ("single", "float", "float32"):
        return "FLOAT"
    if esri_type in ("double", "float64"):
        return "DOUBLE"
    if esri_type in ("date", "datetime", "timestamp", "timestamponly"):
        return "DATETIME"
    if esri_type == "dateonly":
        return "DATE"
    if esri_type in ("blob", "raster"):
        return "BLOB"
    if esri_type in ("guid", "globalid"):
        return "TEXT(38)"
    if esri_type in ("string", "text"):
        return "TEXT(%d)" % length if length else "TEXT"
    # Tipos exoticos (BigObject, XML, geometrias anidadas) se guardan como texto
    # para no perder el dato: QField los mostrara como cadena.
    return "TEXT"


def adapt_value(value):
    """Convierte un valor de arcpy/ogr a algo que sqlite3 sepa guardar."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        # GeoPackage exige ISO-8601 en UTC para DATETIME.
        return "%s.%03dZ" % (
            value.strftime("%Y-%m-%dT%H:%M:%S"),
            value.microsecond // 1000,
        )
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bytearray, memoryview)):
        return sqlite3.Binary(bytes(value))
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def build_gpkg_blob(wkb_info, srs_id):
    """Envuelve un WKB ISO en la cabecera binaria de GeoPackage."""
    flags = 0x01  # cabecera en little-endian
    if wkb_info.is_empty:
        flags |= 0x10  # bandera de geometria vacia, sin envolvente
        envelope = b""
    else:
        flags |= 0x02  # indicador de envolvente = 1 (minx, maxx, miny, maxy)
        min_x, min_y, max_x, max_y = wkb_info.bbox
        envelope = struct.pack("<4d", min_x, max_x, min_y, max_y)
    header = b"GP" + struct.pack("<BBi", 0, flags, srs_id)
    return sqlite3.Binary(header + envelope + wkb_info.wkb)


def parse_gpkg_blob(blob):
    """Descompone un blob de GeoPackage en ``(srs_id, wkb)``.

    Es la operacion inversa de :func:`build_gpkg_blob`; la necesita el
    sincronizador para leer las geometrias que vuelven de QField.
    """
    if blob is None:
        return None, None
    data = bytes(blob)
    if len(data) < 8 or data[:2] != b"GP":
        raise GeoPackageError("El blob no tiene cabecera de GeoPackage")
    flags = data[3] if isinstance(data[3], int) else ord(data[3])
    endian = "<" if flags & 0x01 else ">"
    (srs_id,) = struct.unpack(endian + "i", data[4:8])
    envelope_indicator = (flags >> 1) & 0x07
    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    if envelope_indicator not in envelope_sizes:
        raise GeoPackageError(
            "Indicador de envolvente no valido: %d" % envelope_indicator
        )
    offset = 8 + envelope_sizes[envelope_indicator]
    return srs_id, data[offset:]


class LayerHandle(object):
    """Estado de una tabla en curso de escritura."""

    def __init__(
        self, name, columns, geometry_column, srs_id, has_rtree, promote_to_multi=False
    ):
        self.name = name
        self.columns = columns
        self.geometry_column = geometry_column
        self.srs_id = srs_id
        self.has_rtree = has_rtree
        self.promote_to_multi = promote_to_multi
        self.feature_count = 0
        self.next_fid = 1
        self.min_x = self.min_y = None
        self.max_x = self.max_y = None
        self.geometry_types = set()

    def track_bbox(self, bbox):
        if bbox is None:
            return
        min_x, min_y, max_x, max_y = bbox
        if self.min_x is None or min_x < self.min_x:
            self.min_x = min_x
        if self.min_y is None or min_y < self.min_y:
            self.min_y = min_y
        if self.max_x is None or max_x > self.max_x:
            self.max_x = max_x
        if self.max_y is None or max_y > self.max_y:
            self.max_y = max_y

    @property
    def extent(self):
        if self.min_x is None:
            return None
        return (self.min_x, self.min_y, self.max_x, self.max_y)


class GeoPackageWriter(object):
    """Crea un GeoPackage y le agrega capas y entidades.

    Uso::

        with GeoPackageWriter("data.gpkg") as gpkg:
            srs = gpkg.add_srs(32717, "EPSG", wkt)
            gpkg.create_feature_table("Poste", fields, "POINT", srs)
            gpkg.insert("Poste", {"CODIGO": "P1"}, wkb=geom_wkb)
    """

    def __init__(self, path, overwrite=True, batch_size=2000):
        self.path = path
        self.batch_size = batch_size
        self._layers = {}
        self._pending = {}
        self._pending_rtree = {}

        if overwrite and os.path.exists(path):
            os.remove(path)

        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)

        is_new = not os.path.exists(path)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA journal_mode=MEMORY")
        if is_new:
            self._create_core_tables()

    # ------------------------------------------------------------------
    # ciclo de vida
    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.close()
        else:  # pragma: no cover - camino de error
            self.conn.close()
        return False

    def close(self):
        self.flush()
        self._finalize()
        self.conn.commit()
        self.conn.close()

    # ------------------------------------------------------------------
    # esqueleto del contenedor
    # ------------------------------------------------------------------
    def _create_core_tables(self):
        cur = self.conn.cursor()
        cur.execute("PRAGMA application_id=%d" % APPLICATION_ID)
        cur.execute("PRAGMA user_version=%d" % USER_VERSION)
        cur.executescript(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL PRIMARY KEY,
                organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL,
                definition TEXT NOT NULL,
                description TEXT
            );
            CREATE TABLE gpkg_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                data_type TEXT NOT NULL,
                identifier TEXT UNIQUE,
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
                srs_id INTEGER,
                CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                    REFERENCES gpkg_spatial_ref_sys(srs_id)
            );
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT NOT NULL,
                column_name TEXT NOT NULL,
                geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL,
                z TINYINT NOT NULL,
                m TINYINT NOT NULL,
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
                CONSTRAINT uk_gc_table_name UNIQUE (table_name),
                CONSTRAINT fk_gc_tn FOREIGN KEY (table_name)
                    REFERENCES gpkg_contents(table_name),
                CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id)
                    REFERENCES gpkg_spatial_ref_sys (srs_id)
            );
            CREATE TABLE gpkg_extensions (
                table_name TEXT,
                column_name TEXT,
                extension_name TEXT NOT NULL,
                definition TEXT NOT NULL,
                scope TEXT NOT NULL,
                CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name)
            );
            CREATE TABLE gpkg_ogr_contents (
                table_name TEXT NOT NULL PRIMARY KEY,
                feature_count INTEGER DEFAULT NULL
            );
            """
        )
        # Filas obligatorias de la especificacion.
        cur.executemany(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            [
                (
                    "Undefined cartesian SRS",
                    -1,
                    "NONE",
                    -1,
                    "undefined",
                    "undefined cartesian coordinate reference system",
                ),
                (
                    "Undefined geographic SRS",
                    0,
                    "NONE",
                    0,
                    "undefined",
                    "undefined geographic coordinate reference system",
                ),
                (
                    "WGS 84 geodetic",
                    4326,
                    "EPSG",
                    4326,
                    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
                    '298.257223563,AUTHORITY["EPSG","7030"]],'
                    'AUTHORITY["EPSG","6326"]],'
                    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
                    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
                    'AUTHORITY["EPSG","4326"]]',
                    "longitude/latitude coordinates in decimal degrees on the "
                    "WGS 84 spheroid",
                ),
            ],
        )
        self.conn.commit()

    def add_srs(self, srs_id, organization="EPSG", definition=None, name=None):
        """Registra un sistema de referencia y devuelve su ``srs_id``.

        Si la clase no declara sistema de referencia se devuelve -1 (SRS
        cartesiano indefinido), que es lo que exige la especificacion.
        """
        if not srs_id or srs_id <= 0:
            return -1
        cur = self.conn.execute(
            "SELECT srs_id FROM gpkg_spatial_ref_sys WHERE srs_id=?", (srs_id,)
        )
        if cur.fetchone():
            return srs_id
        self.conn.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            (
                name or ("%s:%s" % (organization or "EPSG", srs_id)),
                srs_id,
                organization or "EPSG",
                srs_id,
                definition or "undefined",
                None,
            ),
        )
        return srs_id

    # ------------------------------------------------------------------
    # creacion de tablas
    # ------------------------------------------------------------------
    def create_feature_table(
        self,
        name,
        fields,
        geometry_type_name,
        srs_id,
        has_z=False,
        has_m=False,
        identifier=None,
        description="",
        geometry_column="geom",
        spatial_index=True,
        promote_to_multi=False,
    ):
        """Crea una capa con geometria.

        ``fields`` es una lista de ``(nombre, tipo_gpkg[, no_nulo])``.
        """
        self._create_table(
            name,
            fields,
            geometry_column=geometry_column,
            geometry_type_name=geometry_type_name,
            srs_id=srs_id,
            has_z=has_z,
            has_m=has_m,
            identifier=identifier,
            description=description,
            spatial_index=spatial_index,
            promote_to_multi=promote_to_multi,
        )

    def create_attribute_table(self, name, fields, identifier=None, description=""):
        """Crea una tabla sin geometria (las ``Unidad`` del modelo, catalogos...)."""
        self._create_table(
            name,
            fields,
            geometry_column=None,
            geometry_type_name=None,
            srs_id=None,
            identifier=identifier,
            description=description,
        )

    def _create_table(
        self,
        name,
        fields,
        geometry_column,
        geometry_type_name,
        srs_id,
        has_z=False,
        has_m=False,
        identifier=None,
        description="",
        spatial_index=False,
        promote_to_multi=False,
    ):
        if name in self._layers:
            raise GeoPackageError("La capa '%s' ya existe en el GeoPackage" % name)

        columns = ['"fid" INTEGER PRIMARY KEY AUTOINCREMENT']
        if geometry_column:
            columns.append('"%s" %s' % (geometry_column, geometry_type_name))

        reserved = {"fid"}
        if geometry_column:
            reserved.add(geometry_column.lower())

        field_names = []
        for field in fields:
            field_name, field_type = field[0], field[1]
            not_null = field[2] if len(field) > 2 else False
            if field_name.lower() in reserved:
                raise GeoPackageError(
                    "El nombre de campo '%s' choca con una columna reservada del "
                    "GeoPackage; renombrelo antes de empaquetar." % field_name
                )
            columns.append(
                '"%s" %s%s' % (field_name, field_type, " NOT NULL" if not_null else "")
            )
            field_names.append(field_name)

        self.conn.execute('CREATE TABLE "%s" (%s)' % (name, ", ".join(columns)))

        self.conn.execute(
            "INSERT INTO gpkg_contents "
            "(table_name, data_type, identifier, description, srs_id) "
            "VALUES (?,?,?,?,?)",
            (
                name,
                "features" if geometry_column else "attributes",
                identifier or name,
                description or "",
                srs_id if geometry_column else None,
            ),
        )
        if geometry_column:
            self.conn.execute(
                "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
                (
                    name,
                    geometry_column,
                    geometry_type_name,
                    srs_id,
                    1 if has_z else 0,
                    1 if has_m else 0,
                ),
            )
        self.conn.execute(
            "INSERT INTO gpkg_ogr_contents (table_name, feature_count) VALUES (?, 0)",
            (name,),
        )

        has_rtree = bool(geometry_column and spatial_index)
        self._layers[name] = LayerHandle(
            name, field_names, geometry_column, srs_id, has_rtree, promote_to_multi
        )
        self._pending[name] = []
        if has_rtree:
            self._create_rtree(name, geometry_column)
            self._pending_rtree[name] = []

    def _create_rtree(self, table, column):
        self.conn.execute(
            'CREATE VIRTUAL TABLE "rtree_%s_%s" USING rtree'
            "(id, minx, maxx, miny, maxy)" % (table, column)
        )
        self.conn.execute(
            "INSERT INTO gpkg_extensions VALUES (?,?,?,?,?)",
            (
                table,
                column,
                "gpkg_rtree_index",
                "http://www.geopackage.org/spec120/#extension_rtree",
                "write-only",
            ),
        )

    def create_private_table(self, name, columns):
        """Crea una tabla auxiliar de qfieldESRI.

        No se registra en ``gpkg_contents``, asi que QGIS, QField y GDAL la
        ignoran por completo: sirve para llevar la linea base de
        sincronizacion dentro del propio paquete sin ensuciar el proyecto.
        """
        definition = ", ".join('"%s" %s' % (column, kind) for column, kind in columns)
        self.conn.execute('CREATE TABLE "%s" (%s)' % (name, definition))

    def insert_private(self, name, columns, rows):
        self.conn.executemany(
            'INSERT INTO "%s" (%s) VALUES (%s)'
            % (
                name,
                ", ".join('"%s"' % column for column in columns),
                ", ".join("?" * len(columns)),
            ),
            rows,
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # insercion de datos
    # ------------------------------------------------------------------
    def insert(self, table, attributes, wkb=None):
        """Encola una entidad. ``wkb`` es el WKB crudo (arcpy/ogr) o ``None``.

        Devuelve el ``fid`` asignado, que el empaquetador usa para dejar
        rastro entre la geodatabase y el GeoPackage.
        """
        handle = self._layers[table]
        fid = handle.next_fid
        handle.next_fid += 1

        blob = None
        if wkb is not None and handle.geometry_column:
            info = wkb_utils.analyze(wkb)
            if handle.promote_to_multi:
                info = wkb_utils.promote_to_multi(info)
            blob = build_gpkg_blob(info, handle.srs_id)
            handle.track_bbox(info.bbox)
            handle.geometry_types.add((info.geometry_type, info.has_z, info.has_m))
            if handle.has_rtree and info.bbox is not None:
                min_x, min_y, max_x, max_y = info.bbox
                self._pending_rtree[table].append((fid, min_x, max_x, min_y, max_y))

        row = [fid]
        if handle.geometry_column:
            row.append(blob)
        row.extend(adapt_value(attributes.get(column)) for column in handle.columns)
        self._pending[table].append(row)
        handle.feature_count += 1
        if len(self._pending[table]) >= self.batch_size:
            self.flush(table)
        return fid

    def flush(self, table=None):
        tables = [table] if table else list(self._pending.keys())
        for name in tables:
            rows = self._pending.get(name)
            if rows:
                handle = self._layers[name]
                columns = ["fid"]
                if handle.geometry_column:
                    columns.append(handle.geometry_column)
                columns.extend(handle.columns)
                sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % (
                    name,
                    ", ".join('"%s"' % column for column in columns),
                    ", ".join("?" * len(columns)),
                )
                self.conn.executemany(sql, rows)
                self._pending[name] = []
            rtree_rows = self._pending_rtree.get(name)
            if rtree_rows:
                self.conn.executemany(
                    'INSERT INTO "rtree_%s_%s" (id, minx, maxx, miny, maxy) '
                    "VALUES (?,?,?,?,?)" % (name, self._layers[name].geometry_column),
                    rtree_rows,
                )
                self._pending_rtree[name] = []
        self.conn.commit()

    # ------------------------------------------------------------------
    # cierre
    # ------------------------------------------------------------------
    def _finalize(self):
        for name, handle in self._layers.items():
            if handle.extent is not None:
                self.conn.execute(
                    "UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? "
                    "WHERE table_name=?",
                    (handle.min_x, handle.min_y, handle.max_x, handle.max_y, name),
                )
            self.conn.execute(
                "UPDATE gpkg_ogr_contents SET feature_count=? WHERE table_name=?",
                (handle.feature_count, name),
            )
            if handle.has_rtree:
                self._create_rtree_triggers(name, handle.geometry_column)
            self._create_ogr_contents_triggers(name)

    def _create_rtree_triggers(self, table, column):
        """Disparadores estandar del indice R-Tree (GeoPackage 1.2, anexo F.3).

        Usan las funciones ``ST_*`` que registran GDAL/QGIS/QField al abrir el
        contenedor; por eso se crean al final, cuando ya no vamos a escribir
        nosotros con ``sqlite3`` puro (que no las tiene).
        """
        values = (
            'NEW."fid", ST_MinX(NEW."{c}"), ST_MaxX(NEW."{c}"), '
            'ST_MinY(NEW."{c}"), ST_MaxY(NEW."{c}")'
        ).format(c=column)
        self.conn.executescript(
            """
        CREATE TRIGGER "rtree_{t}_{c}_insert" AFTER INSERT ON "{t}"
          WHEN (new."{c}" NOT NULL AND NOT ST_IsEmpty(NEW."{c}"))
        BEGIN
          INSERT OR REPLACE INTO "rtree_{t}_{c}" VALUES ({v});
        END;
        CREATE TRIGGER "rtree_{t}_{c}_update1" AFTER UPDATE OF "{c}" ON "{t}"
          WHEN OLD."fid" = NEW."fid" AND
               (NEW."{c}" NOTNULL AND NOT ST_IsEmpty(NEW."{c}"))
        BEGIN
          INSERT OR REPLACE INTO "rtree_{t}_{c}" VALUES ({v});
        END;
        CREATE TRIGGER "rtree_{t}_{c}_update2" AFTER UPDATE OF "{c}" ON "{t}"
          WHEN OLD."fid" = NEW."fid" AND
               (NEW."{c}" ISNULL OR ST_IsEmpty(NEW."{c}"))
        BEGIN
          DELETE FROM "rtree_{t}_{c}" WHERE id = OLD."fid";
        END;
        CREATE TRIGGER "rtree_{t}_{c}_update3" AFTER UPDATE ON "{t}"
          WHEN OLD."fid" != NEW."fid" AND
               (NEW."{c}" NOTNULL AND NOT ST_IsEmpty(NEW."{c}"))
        BEGIN
          DELETE FROM "rtree_{t}_{c}" WHERE id = OLD."fid";
          INSERT OR REPLACE INTO "rtree_{t}_{c}" VALUES ({v});
        END;
        CREATE TRIGGER "rtree_{t}_{c}_update4" AFTER UPDATE ON "{t}"
          WHEN OLD."fid" != NEW."fid" AND
               (NEW."{c}" ISNULL OR ST_IsEmpty(NEW."{c}"))
        BEGIN
          DELETE FROM "rtree_{t}_{c}" WHERE id IN (OLD."fid", NEW."fid");
        END;
        CREATE TRIGGER "rtree_{t}_{c}_delete" AFTER DELETE ON "{t}"
          WHEN old."{c}" NOT NULL
        BEGIN
          DELETE FROM "rtree_{t}_{c}" WHERE id = OLD."fid";
        END;
        """.format(t=table, c=column, v=values)
        )

    def _create_ogr_contents_triggers(self, name):
        """Disparadores de ``gpkg_ogr_contents`` (los mismos que crea GDAL)."""
        self.conn.executescript(
            """
            CREATE TRIGGER "trigger_insert_feature_count_{t}"
            AFTER INSERT ON "{t}"
            BEGIN
              UPDATE gpkg_ogr_contents SET feature_count = feature_count + 1
              WHERE lower(table_name) = lower('{t}');
            END;
            CREATE TRIGGER "trigger_delete_feature_count_{t}"
            AFTER DELETE ON "{t}"
            BEGIN
              UPDATE gpkg_ogr_contents SET feature_count = feature_count - 1
              WHERE lower(table_name) = lower('{t}');
            END;
            """.format(t=name)
        )

    # ------------------------------------------------------------------
    def layer_extent(self, name):
        return self._layers[name].extent

    def layer_feature_count(self, name):
        return self._layers[name].feature_count

    def layer_geometry_types(self, name):
        return self._layers[name].geometry_types
