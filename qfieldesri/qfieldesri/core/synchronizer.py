# -*- coding: utf-8 -*-
"""Sincronizacion de vuelta: de QField a la geodatabase.

Es la contraparte del empaquetado y el equivalente de lo que en QFieldSync hace
el mecanismo de *deltas* de QFieldCloud. Aqui la comparacion se hace contra la
**linea base** que el empaquetador guardo dentro del propio GeoPackage
(``qfe_baseline``, una tabla que ni QGIS ni QField ven), lo que permite
distinguir tres situaciones sin depender de ningun servidor:

altas
    Filas del GeoPackage que no estaban en la linea base.
modificaciones
    Filas cuya huella cambio respecto de la linea base.
bajas
    Filas de la linea base que ya no estan en el GeoPackage.

Ademas se detectan **conflictos**: si el registro tambien cambio en la
geodatabase desde que se empaqueto, la modificacion de campo no se aplica sola
— se informa para que una persona decida.
"""

import datetime
import io
import json
import os
import sqlite3

from ..utils import wkb as wkb_utils
from ..utils.checksum import feature_checksum
from ..writers.geopackage import adapt_value, parse_gpkg_blob
from .packager import BASELINE_TABLE, MANIFEST_NAME, load_manifest


class SyncError(Exception):
    pass


class ConflictPolicy(object):
    """Que hacer cuando el registro cambio en los dos lados."""

    #: No tocar la geodatabase y dejarlo en el informe (recomendado).
    REPORT = "informar"
    #: Aplicar lo capturado en campo por encima.
    FIELD_WINS = "campo"
    #: Conservar lo que tiene la geodatabase.
    SOURCE_WINS = "geodatabase"

    ALL = (REPORT, FIELD_WINS, SOURCE_WINS)


class Change(object):
    """Una diferencia detectada entre el paquete y la geodatabase."""

    INSERT = "alta"
    UPDATE = "modificacion"
    DELETE = "baja"

    def __init__(
        self,
        kind,
        layer,
        table,
        fid,
        key_value=None,
        attributes=None,
        wkb=None,
        conflict=False,
        applied=False,
        message="",
    ):
        self.kind = kind
        self.layer = layer
        self.table = table
        self.fid = fid
        self.key_value = key_value
        self.attributes = attributes or {}
        self.wkb = wkb
        self.conflict = conflict
        self.applied = applied
        self.message = message

    def to_dict(self):
        return {
            "kind": self.kind,
            "layer": self.layer,
            "table": self.table,
            "fid": self.fid,
            "key_value": self.key_value,
            "conflict": self.conflict,
            "applied": self.applied,
            "message": self.message,
            "fields": sorted(self.attributes.keys()),
        }


class SyncReport(object):
    def __init__(self, project_dir, workspace):
        self.project_dir = project_dir
        self.workspace = workspace
        self.changes = []
        self.errors = []
        self.started_at = datetime.datetime.now()
        self.dry_run = True

    def add(self, change):
        self.changes.append(change)
        return change

    def of_kind(self, kind, applied=None):
        return [
            change
            for change in self.changes
            if change.kind == kind and (applied is None or change.applied == applied)
        ]

    @property
    def conflicts(self):
        return [change for change in self.changes if change.conflict]

    def summary(self):
        return {
            "altas": len(self.of_kind(Change.INSERT)),
            "modificaciones": len(self.of_kind(Change.UPDATE)),
            "bajas": len(self.of_kind(Change.DELETE)),
            "conflictos": len(self.conflicts),
            "aplicados": len([c for c in self.changes if c.applied]),
            "errores": len(self.errors),
        }

    def to_dict(self):
        return {
            "project_dir": self.project_dir,
            "workspace": self.workspace,
            "started_at": self.started_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "dry_run": self.dry_run,
            "summary": self.summary(),
            "changes": [change.to_dict() for change in self.changes],
            "errors": self.errors,
        }

    def format(self):
        summary = self.summary()
        lines = [
            "Altas:          %d" % summary["altas"],
            "Modificaciones: %d" % summary["modificaciones"],
            "Bajas:          %d" % summary["bajas"],
            "Conflictos:     %d" % summary["conflictos"],
            "Aplicados:      %d" % summary["aplicados"],
            "Errores:        %d" % summary["errores"],
        ]
        for change in self.conflicts:
            lines.append(
                "  CONFLICTO %s/%s (%s): %s"
                % (change.layer, change.key_value, change.kind, change.message)
            )
        for error in self.errors:
            lines.append("  ERROR %s" % error)
        return "\n".join(lines)

    def write(self, path):
        with io.open(path, "w", encoding="utf-8") as handle:
            text = json.dumps(
                self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False
            )
            try:
                handle.write(unicode(text))
            except NameError:
                handle.write(text)
        return path


class Synchronizer(object):
    """Devuelve a la geodatabase lo capturado en QField."""

    def __init__(
        self,
        project_dir,
        reader,
        conflict_policy=ConflictPolicy.REPORT,
        apply_deletes=False,
        progress=None,
    ):
        self.project_dir = project_dir
        self.reader = reader
        self.conflict_policy = conflict_policy
        #: Borrar en la geodatabase lo borrado en campo es una decision seria
        #: en una red electrica; por eso esta desactivado salvo peticion.
        self.apply_deletes = apply_deletes
        self.progress = progress or (lambda message, percent=None: None)
        self.manifest = load_manifest(project_dir)
        self.gpkg_path = os.path.join(project_dir, "data.gpkg")
        if not os.path.isfile(self.gpkg_path):
            raise SyncError("No se encuentra %s" % self.gpkg_path)
        self.workspace_info = None

    # ------------------------------------------------------------------
    def detect(self):
        """Calcula los cambios sin tocar la geodatabase."""
        report = SyncReport(self.project_dir, self.manifest.get("workspace"))
        connection = sqlite3.connect(self.gpkg_path)
        try:
            self._require_baseline(connection)
            self.workspace_info = self.reader.describe_workspace(
                [entry["source_class"] for entry in self.manifest["layers"]]
            )
            total = len(self.manifest["layers"])
            for index, entry in enumerate(self.manifest["layers"]):
                self.progress(
                    "Comparando %s" % entry["source_class"],
                    int(100.0 * index / max(total, 1)),
                )
                self._detect_layer(connection, entry, report)
        finally:
            connection.close()
        return report

    def apply(self, report=None):
        """Aplica a la geodatabase los cambios detectados."""
        if not self.reader.supports_write:
            raise SyncError(
                "El motor de lectura '%s' no puede escribir en la "
                "geodatabase. Ejecute la sincronizacion desde ArcGIS."
                % self.reader.name
            )
        report = report or self.detect()
        report.dry_run = False

        layers = dict((entry["table"], entry) for entry in self.manifest["layers"])
        self.reader.start_editing()
        try:
            for change in report.changes:
                entry = layers[change.table]
                layer_info = self.workspace_info.layer(entry["source_class"])
                if layer_info is None:
                    report.errors.append(
                        "La clase '%s' ya no existe en la geodatabase"
                        % entry["source_class"]
                    )
                    continue
                self._apply_change(change, entry, layer_info, report)
            self.reader.stop_editing(save=True)
        except Exception as error:
            self.reader.stop_editing(save=False)
            report.errors.append("Se revirtieron los cambios: %s" % error)
            raise
        return report

    # ------------------------------------------------------------------
    def _require_baseline(self, connection):
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (BASELINE_TABLE,),
        )
        if cursor.fetchone() is None:
            raise SyncError(
                "El GeoPackage no tiene la linea base '%s': no fue generado "
                "por qfieldESRI o fue reconstruido por otra herramienta. Sin "
                "ella no se puede saber que cambio en campo." % BASELINE_TABLE
            )

    def _detect_layer(self, connection, entry, report):
        table = entry["table"]
        writable = entry["writable_fields"]
        key_field = entry["key_field"]
        exported = entry["exported_fields"]
        is_spatial = bool(entry.get("geometry_type"))

        baseline = {}
        for fid, source_key, checksum in connection.execute(
            "SELECT fid, source_key, checksum FROM %s WHERE table_name=?"
            % BASELINE_TABLE,
            (table,),
        ):
            baseline[fid] = (source_key, checksum)

        columns = ["fid"] + exported
        if is_spatial:
            columns.append("geom")
        sql = 'SELECT %s FROM "%s"' % (
            ", ".join('"%s"' % column for column in columns),
            table,
        )

        seen = set()
        for row in connection.execute(sql):
            values = dict(zip(columns, row))
            fid = values.pop("fid")
            seen.add(fid)
            wkb = None
            if is_spatial:
                blob = values.pop("geom", None)
                if blob is not None:
                    _srs_id, wkb = parse_gpkg_blob(blob)

            checksum = feature_checksum(
                dict((name, values.get(name)) for name in writable), writable, wkb
            )

            if fid not in baseline:
                report.add(
                    Change(
                        Change.INSERT,
                        entry["source_class"],
                        table,
                        fid,
                        attributes=dict((name, values.get(name)) for name in writable),
                        wkb=wkb,
                    )
                )
                continue

            source_key, original = baseline[fid]
            if checksum == original:
                continue

            change = Change(
                Change.UPDATE,
                entry["source_class"],
                table,
                fid,
                key_value=source_key,
                attributes=dict((name, values.get(name)) for name in writable),
                wkb=wkb,
            )
            self._flag_conflict(change, entry, key_field, writable, original)
            report.add(change)

        for fid, (source_key, _checksum) in baseline.items():
            if fid in seen:
                continue
            report.add(
                Change(
                    Change.DELETE,
                    entry["source_class"],
                    table,
                    fid,
                    key_value=source_key,
                    message=(
                        "Borrado en campo; solo se aplica si se pidio expresamente."
                    ),
                )
            )

    def _flag_conflict(self, change, entry, key_field, writable, original):
        """Marca el cambio si la geodatabase tambien se movio."""
        layer_info = self.workspace_info.layer(entry["source_class"])
        if layer_info is None or change.key_value in (None, ""):
            return
        current = self._current_checksum(
            layer_info, key_field, change.key_value, writable, entry
        )
        if current is None:
            change.conflict = True
            change.message = "El registro ya no existe en la geodatabase (%s = %s)." % (
                key_field,
                change.key_value,
            )
            return
        if current != original:
            change.conflict = True
            change.message = (
                "El registro tambien cambio en la geodatabase desde el empaquetado."
            )

    def _current_checksum(self, layer_info, key_field, key_value, writable, entry):
        where = "%s = %s" % (key_field, _sql_literal(key_value))
        promote = bool(entry.get("geometry_type")) and entry["geometry_type"] in (
            "Polyline",
            "Polygon",
            "Multipoint",
        )
        for wkb, attributes in self.reader.iter_features(
            layer_info, writable, where_clause=where, limit=1
        ):
            adapted = dict(
                (name, adapt_value(attributes.get(name))) for name in writable
            )
            normalized = None
            if wkb:
                info = wkb_utils.analyze(wkb)
                if promote:
                    info = wkb_utils.promote_to_multi(info)
                normalized = info.wkb
            return feature_checksum(adapted, writable, normalized)
        return None

    # ------------------------------------------------------------------
    def _apply_change(self, change, entry, layer_info, report):
        try:
            if change.kind == Change.INSERT:
                self._apply_insert(change, layer_info)
            elif change.kind == Change.UPDATE:
                self._apply_update(change, entry, layer_info)
            elif change.kind == Change.DELETE:
                self._apply_delete(change, entry, layer_info)
        except Exception as error:
            change.applied = False
            change.message = str(error)
            report.errors.append(
                "%s %s (fid %s): %s" % (change.kind, change.layer, change.fid, error)
            )

    def _apply_insert(self, change, layer_info):
        attributes = _drop_managed(change.attributes, layer_info)
        self.reader.insert_feature(layer_info, attributes, wkb=change.wkb)
        change.applied = True
        change.message = "Entidad nueva creada en la geodatabase."

    def _apply_update(self, change, entry, layer_info):
        if change.conflict and self.conflict_policy != ConflictPolicy.FIELD_WINS:
            change.applied = False
            if not change.message:
                change.message = "Conflicto sin resolver."
            return
        attributes = _drop_managed(change.attributes, layer_info)
        self.reader.update_feature(
            layer_info,
            entry["key_field"],
            change.key_value,
            attributes,
            wkb=change.wkb,
        )
        change.applied = True

    def _apply_delete(self, change, entry, layer_info):
        if not self.apply_deletes:
            change.applied = False
            return
        self.reader.delete_feature(layer_info, entry["key_field"], change.key_value)
        change.applied = True


def _drop_managed(attributes, layer_info):
    """Quita los campos que ArcGIS gestiona solo (OID, GlobalID, no editables)."""
    clean = {}
    for name, value in attributes.items():
        field = layer_info.field(name)
        if field is None or not field.editable:
            continue
        if (field.field_type or "").lower() in ("oid", "globalid"):
            continue
        clean[name] = value
    return clean


def _sql_literal(value):
    if value is None:
        return "NULL"
    try:
        float(value)
        if str(value).strip().lstrip("-").replace(".", "", 1).isdigit():
            return str(value)
    except (TypeError, ValueError):
        pass
    return "'%s'" % str(value).replace("'", "''")


def is_qfieldesri_package(path):
    """``True`` si la carpeta parece un paquete generado por qfieldESRI."""
    return os.path.isfile(os.path.join(path, MANIFEST_NAME)) and os.path.isfile(
        os.path.join(path, "data.gpkg")
    )
