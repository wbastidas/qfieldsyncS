# -*- coding: utf-8 -*-
"""Adjuntos capturados en campo (fotos, audio, video, archivos).

QField guarda cada archivo dentro de la carpeta del proyecto (``DCIM/``,
``audio/``, ``video/``, ``files/``) y escribe la ruta relativa en el campo de
texto configurado como ``ExternalResource``. Al volver hay dos caminos, y
qfieldESRI soporta los dos porque en la practica conviven:

``ruta``
    Se conserva la ruta relativa en el campo de texto y los archivos se copian
    a una carpeta compartida. Es lo que funciona cuando la clase no tiene
    habilitados los adjuntos de ArcGIS.
``adjunto``
    Los archivos se registran como **attachments** de la geodatabase con
    ``arcpy.management.AddAttachments``. Requiere que la clase tenga los
    adjuntos habilitados y que exista GlobalID.
"""

import os
import shutil

ATTACHMENT_DIRS = ("DCIM", "audio", "video", "files")


class AttachmentMode(object):
    PATH = "ruta"
    GDB_ATTACHMENT = "adjunto"
    ALL = (PATH, GDB_ATTACHMENT)


class AttachmentItem(object):
    """Un archivo capturado en campo y a que registro pertenece."""

    def __init__(
        self, layer, key_field, key_value, field, relative_path, absolute_path
    ):
        self.layer = layer
        self.key_field = key_field
        self.key_value = key_value
        self.field = field
        self.relative_path = relative_path
        self.absolute_path = absolute_path
        self.exists = os.path.isfile(absolute_path)


def collect(project_dir, manifest, connection):
    """Recorre el paquete y devuelve los adjuntos referenciados.

    ``connection`` es una conexion ``sqlite3`` al ``data.gpkg`` del paquete.
    Solo se miran los campos que el empaquetado marco como adjunto, para no
    confundir una ruta de foto con cualquier otro campo de texto.
    """
    items = []
    for entry in manifest.get("layers", []):
        fields = entry.get("attachment_fields") or {}
        if not fields:
            continue
        columns = ["fid", entry["key_field"]] + list(fields.keys())
        sql = 'SELECT %s FROM "%s"' % (
            ", ".join('"%s"' % column for column in columns),
            entry["table"],
        )
        for row in connection.execute(sql):
            values = dict(zip(columns, row))
            for field in fields:
                relative = values.get(field)
                if not relative:
                    continue
                items.append(
                    AttachmentItem(
                        layer=entry["source_class"],
                        key_field=entry["key_field"],
                        key_value=values.get(entry["key_field"]),
                        field=field,
                        relative_path=relative,
                        absolute_path=os.path.join(
                            project_dir, str(relative).replace("/", os.sep)
                        ),
                    )
                )
    return items


def copy_to_repository(items, repository_dir):
    """Copia los archivos a una carpeta compartida y devuelve el mapa de rutas.

    Devuelve ``{ruta_relativa: ruta_final}``. Los archivos que QField
    referencia pero que no llegaron (porque el tecnico no sincronizo la carpeta
    completa) se devuelven en la segunda lista.
    """
    copied = {}
    missing = []
    for item in items:
        if not item.exists:
            missing.append(item)
            continue
        destination_dir = os.path.join(
            repository_dir, item.layer, os.path.dirname(item.relative_path)
        )
        if not os.path.isdir(destination_dir):
            os.makedirs(destination_dir)
        destination = os.path.join(
            destination_dir, os.path.basename(item.absolute_path)
        )
        shutil.copy2(item.absolute_path, destination)
        copied[item.relative_path] = destination
    return copied, missing


def build_match_table(items, path):
    """Escribe la tabla de coincidencias que espera ``AddAttachments``.

    El formato es el documentado por ESRI: una tabla con la clave del registro
    y la ruta del archivo. Se genera como CSV para no depender de arcpy en esta
    parte y poder revisarla a mano antes de cargarla.
    """
    import csv

    rows = [item for item in items if item.exists]
    with open(path, "w") as handle:
        writer = csv.writer(handle)
        writer.writerow(["CLAVE", "RUTA"])
        for item in rows:
            writer.writerow([item.key_value, item.absolute_path])
    return path, len(rows)


def attach_with_arcpy(layer_path, key_field, match_table, progress=None):
    """Registra los adjuntos en la geodatabase con ``arcpy``.

    Se importa arcpy aqui dentro para que el modulo siga siendo utilizable (y
    comprobable) en un equipo sin ArcGIS.
    """
    import arcpy

    progress = progress or (lambda message: None)
    progress("Registrando adjuntos en %s" % layer_path)
    arcpy.management.EnableAttachments(layer_path)
    arcpy.management.AddAttachments(
        layer_path,
        key_field,
        match_table,
        "CLAVE",
        "RUTA",
    )
    return True
