# -*- coding: utf-8 -*-
"""Caja de herramientas de qfieldESRI para ArcGIS Desktop.

Es el equivalente del menu de QFieldSync dentro de QGIS: las mismas
operaciones, pero como herramientas de geoprocesamiento de ArcGIS, que es la
forma nativa de extender ArcMap y ArcGIS Pro (y la unica que ademas queda
disponible en ModelBuilder y en la ventana de Python).

Instalacion: copie la carpeta completa y, en el panel *Catalogo*, conectese a
ella. ``QFieldESRI.pyt`` aparecera como una caja de herramientas con cinco
herramientas dentro.
"""

import os
import sys

import arcpy

# El paquete vive junto a este archivo; se antepone al path para que ArcGIS
# use siempre esta copia y no otra que hubiera instalada en el sistema.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from qfieldesri.core.checker import WorkspaceChecker  # noqa: E402
from qfieldesri.core.config import LayerAction, PackagingConfig  # noqa: E402
from qfieldesri.core.packager import Packager, load_manifest  # noqa: E402
from qfieldesri.core.synchronizer import ConflictPolicy, Synchronizer  # noqa: E402
from qfieldesri.profiles import available_profiles  # noqa: E402
from qfieldesri.readers.arcpy_reader import ArcpyReader  # noqa: E402
from qfieldesri.version import __version__  # noqa: E402


def _progress(message, percent=None):
    """Puente entre el progreso de qfieldESRI y la ventana de ArcGIS."""
    if percent is None:
        arcpy.AddMessage("    %s" % message)
    else:
        arcpy.AddMessage("[%3d%%] %s" % (percent, message))
        try:
            arcpy.SetProgressorPosition(percent)
        except Exception:  # noqa: BLE001 - fuera de una barra de progreso
            pass


def _report(result):
    """Vuelca los avisos del verificador en la ventana de resultados."""
    for feedback in result.feedbacks:
        text = feedback.format()
        if feedback.level == "error":
            arcpy.AddError(text)
        elif feedback.level == "aviso":
            arcpy.AddWarning(text)
        else:
            arcpy.AddMessage(text)


def _open_reader(workspace):
    reader = ArcpyReader(workspace)
    reader.open()
    return reader


def _list_layer_names(workspace):
    """Nombres de clases y tablas, para rellenar los desplegables."""
    try:
        reader = _open_reader(workspace)
    except Exception:  # noqa: BLE001 - workspace aun no valido en el dialogo
        return []
    try:
        return sorted(
            os.path.basename(path) for path, _dataset in reader.list_datasets()
        )
    except Exception:  # noqa: BLE001
        return []
    finally:
        reader.close()


def _aoi_to_wkt(feature_layer):
    """Une las entidades (o la seleccion) de una capa en un unico WKT."""
    if not feature_layer:
        return None, None
    union = None
    spatial_reference = arcpy.Describe(feature_layer).spatialReference
    with arcpy.da.SearchCursor(feature_layer, ["SHAPE@"]) as cursor:
        for (geometry,) in cursor:
            if geometry is None:
                continue
            union = geometry if union is None else union.union(geometry)
    if union is None:
        return None, None
    code = getattr(spatial_reference, "factoryCode", None) or None
    return union.WKT, code


class Toolbox(object):
    def __init__(self):
        self.label = "qfieldESRI"
        self.alias = "qfieldesri"
        self.description = (
            "Migracion de geodatabases de ESRI (File Geodatabase o "
            "corporativa) a QField, y regreso de lo capturado en campo. "
            "Version %s." % __version__
        )
        self.tools = [
            AnalizarGeodatabase,
            EmpaquetarParaQField,
            SincronizarDesdeQField,
            PublicarEnQFieldCloud,
            RecuperarDeQFieldCloud,
        ]


# ----------------------------------------------------------------------
class AnalizarGeodatabase(object):
    """Inventario y verificacion previa."""

    def __init__(self):
        self.label = "1 · Analizar geodatabase"
        self.description = (
            "Inventaria las clases, dominios, subtipos y relaciones de la "
            "geodatabase y avisa de lo que puede dar problemas al llevarla a "
            "QField. No modifica nada."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        workspace = arcpy.Parameter(
            displayName="Geodatabase (File Geodatabase o conexion .sde)",
            name="workspace",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input",
        )
        profile = arcpy.Parameter(
            displayName="Perfil de modelo de datos",
            name="profile",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        profile.filter.list = available_profiles()
        profile.value = "cnel_ep"

        report = arcpy.Parameter(
            displayName="Informe JSON (opcional)",
            name="report",
            datatype="DEFile",
            parameterType="Optional",
            direction="Output",
        )
        return [workspace, profile, report]

    def execute(self, parameters, messages):  # noqa: ARG002
        workspace = parameters[0].valueAsText
        reader = _open_reader(workspace)
        try:
            info = reader.describe_workspace()
            config = PackagingConfig(
                workspace=workspace,
                output_dir=arcpy.env.scratchFolder or ".",
                profile=parameters[1].valueAsText,
            )
            arcpy.AddMessage(
                "Geodatabase: %s (%s)" % (info.path, info.workspace_type)
            )
            arcpy.AddMessage(
                "Clases: %d · dominios: %d · relaciones: %d"
                % (len(info.layers), len(info.domains), len(info.relationships))
            )
            for layer in info.layers:
                arcpy.AddMessage(
                    "  %-40s %-12s %3d campos%s"
                    % (
                        layer.name,
                        layer.geometry_type or "tabla",
                        len(layer.fields),
                        "  %d subtipos" % len(layer.subtypes)
                        if layer.subtypes
                        else "",
                    )
                )
            result = WorkspaceChecker(info, config).check()
            _report(result)

            if parameters[2].valueAsText:
                import io
                import json

                with io.open(parameters[2].valueAsText, "w", encoding="utf-8") as h:
                    h.write(
                        json.dumps(
                            {
                                "workspace": info.path,
                                "layers": [
                                    {
                                        "name": layer.name,
                                        "geometry_type": layer.geometry_type,
                                        "fields": len(layer.fields),
                                    }
                                    for layer in info.layers
                                ],
                                "feedback": result.to_list(),
                            },
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
        finally:
            reader.close()


# ----------------------------------------------------------------------
class EmpaquetarParaQField(object):
    """Geodatabase -> carpeta de proyecto de QField."""

    def __init__(self):
        self.label = "2 · Empaquetar para QField"
        self.description = (
            "Genera la carpeta que se copia al dispositivo: un GeoPackage con "
            "los datos y un proyecto QGIS con los formularios, dominios, "
            "subtipos y relaciones ya traducidos."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):  # noqa: PLR0915
        workspace = arcpy.Parameter(
            displayName="Geodatabase de origen",
            name="workspace",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input",
        )
        output = arcpy.Parameter(
            displayName="Carpeta de salida",
            name="output_dir",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input",
        )
        name = arcpy.Parameter(
            displayName="Nombre del proyecto",
            name="project_name",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        name.value = "qfield_proyecto"

        title = arcpy.Parameter(
            displayName="Titulo visible en QField",
            name="title",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        profile = arcpy.Parameter(
            displayName="Perfil de modelo de datos",
            name="profile",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        profile.filter.list = available_profiles()
        profile.value = "cnel_ep"

        layers = arcpy.Parameter(
            displayName="Clases a incluir (vacio = todas)",
            name="layers",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True,
        )
        read_only = arcpy.Parameter(
            displayName="Clases de solo consulta",
            name="read_only",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True,
        )
        filters = arcpy.Parameter(
            displayName="Filtros por clase (Clase / Clausula WHERE)",
            name="filters",
            datatype="GPValueTable",
            parameterType="Optional",
            direction="Input",
        )
        filters.columns = [["GPString", "Clase"], ["GPSQLExpression", "WHERE"]]

        photos = arcpy.Parameter(
            displayName="Campos de fotografia (Clase / Campo)",
            name="photos",
            datatype="GPValueTable",
            parameterType="Optional",
            direction="Input",
        )
        photos.columns = [["GPString", "Clase"], ["GPString", "Campo"]]

        aoi = arcpy.Parameter(
            displayName="Area de interes (capa de poligonos)",
            name="aoi",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input",
        )
        related = arcpy.Parameter(
            displayName="Incluir las tablas relacionadas (Unidades)",
            name="include_related",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        related.value = True

        threshold = arcpy.Parameter(
            displayName="Dominios con mas valores que este numero van como catalogo",
            name="threshold",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input",
        )
        threshold.value = 40

        force = arcpy.Parameter(
            displayName="Empaquetar aunque la verificacion encuentre errores",
            name="force",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        force.value = False

        config_out = arcpy.Parameter(
            displayName="Guardar la configuracion en (opcional)",
            name="config_out",
            datatype="DEFile",
            parameterType="Optional",
            direction="Output",
        )
        return [
            workspace,
            output,
            name,
            title,
            profile,
            layers,
            read_only,
            filters,
            photos,
            aoi,
            related,
            threshold,
            force,
            config_out,
        ]

    def updateParameters(self, parameters):
        """Rellena los desplegables de clases en cuanto se elige la geodatabase."""
        if parameters[0].altered and parameters[0].valueAsText:
            names = _list_layer_names(parameters[0].valueAsText)
            for index in (5, 6):
                parameters[index].filter.list = names
        return

    def execute(self, parameters, messages):  # noqa: ARG002, PLR0912
        workspace = parameters[0].valueAsText
        selected = parameters[5].valueAsText
        selected = selected.split(";") if selected else []
        read_only = parameters[6].valueAsText
        read_only = read_only.split(";") if read_only else []

        config = PackagingConfig(
            workspace=workspace,
            output_dir=parameters[1].valueAsText,
            project_name=parameters[2].valueAsText,
            title=parameters[3].valueAsText or parameters[2].valueAsText,
            profile=parameters[4].valueAsText,
            include_related_tables=bool(parameters[10].value),
            big_domain_threshold=int(parameters[11].value or 40),
        )

        aoi_wkt, aoi_crs = _aoi_to_wkt(parameters[9].valueAsText)
        config.area_of_interest = aoi_wkt
        config.area_of_interest_crs = aoi_crs

        reader = _open_reader(workspace)
        try:
            info = reader.describe_workspace()

            if selected:
                for layer in info.layers:
                    config.layer_config(layer.name).action = (
                        LayerAction.COPY
                        if layer.name in selected
                        else LayerAction.REMOVE
                    )
            for layer_name in read_only:
                config.layer_config(layer_name).action = LayerAction.READ_ONLY

            for row in parameters[7].value or []:
                config.layer_config(str(row[0])).where_clause = str(row[1] or "")
            for row in parameters[8].value or []:
                config.layer_config(str(row[0])).attachment_fields[
                    str(row[1])
                ] = "image"

            result = WorkspaceChecker(info, config).check()
            _report(result)
            if result.has_errors and not parameters[12].value:
                arcpy.AddError(
                    "Se encontraron errores. Corrijalos o marque la casilla "
                    "para empaquetar de todos modos."
                )
                return

            arcpy.SetProgressor("step", "Empaquetando para QField", 0, 100, 1)
            packaging = Packager(reader, config, progress=_progress).run()

            arcpy.AddMessage("")
            arcpy.AddMessage("Paquete: %s" % packaging.project_dir)
            for layer_name in sorted(packaging.layer_counts):
                arcpy.AddMessage(
                    "  %-40s %8d entidades"
                    % (layer_name, packaging.layer_counts[layer_name])
                )
            arcpy.AddMessage(
                "  %-40s %8d entidades" % ("TOTAL", packaging.total_features)
            )
            arcpy.AddMessage("")
            arcpy.AddMessage(
                "Copie la carpeta completa al dispositivo (o publiquela con la "
                "herramienta 4) y abra el archivo .qgs desde QField."
            )

            if parameters[13].valueAsText:
                config.save(parameters[13].valueAsText)
                arcpy.AddMessage(
                    "Configuracion guardada en %s" % parameters[13].valueAsText
                )
        finally:
            reader.close()
            arcpy.ResetProgressor()


# ----------------------------------------------------------------------
class SincronizarDesdeQField(object):
    """Carpeta de QField -> geodatabase."""

    def __init__(self):
        self.label = "3 · Sincronizar desde QField"
        self.description = (
            "Compara lo que vuelve del dispositivo con la linea base guardada "
            "al empaquetar y aplica altas, modificaciones y (si se pide) bajas "
            "en la geodatabase, avisando de los conflictos."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        package = arcpy.Parameter(
            displayName="Carpeta del proyecto devuelto por QField",
            name="package",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input",
        )
        workspace = arcpy.Parameter(
            displayName="Geodatabase destino (vacio = la del manifiesto)",
            name="workspace",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Input",
        )
        apply_changes = arcpy.Parameter(
            displayName="Aplicar los cambios (sin marcar, solo simula)",
            name="apply",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        apply_changes.value = False

        deletes = arcpy.Parameter(
            displayName="Aplicar tambien las bajas hechas en campo",
            name="deletes",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        deletes.value = False

        conflicts = arcpy.Parameter(
            displayName="Ante un conflicto",
            name="conflicts",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        conflicts.filter.list = list(ConflictPolicy.ALL)
        conflicts.value = ConflictPolicy.REPORT

        report = arcpy.Parameter(
            displayName="Informe JSON (opcional)",
            name="report",
            datatype="DEFile",
            parameterType="Optional",
            direction="Output",
        )
        return [package, workspace, apply_changes, deletes, conflicts, report]

    def updateMessages(self, parameters):
        """Avisa antes de ejecutar si la carpeta no es un paquete de qfieldESRI."""
        if parameters[0].valueAsText:
            manifest = os.path.join(
                parameters[0].valueAsText, "qfieldesri_manifest.json"
            )
            if not os.path.isfile(manifest):
                parameters[0].setErrorMessage(
                    "La carpeta no contiene 'qfieldesri_manifest.json': no es "
                    "un paquete generado por esta herramienta."
                )
        return

    def execute(self, parameters, messages):  # noqa: ARG002
        package = parameters[0].valueAsText
        manifest = load_manifest(package)
        workspace = parameters[1].valueAsText or manifest.get("workspace")
        if not workspace:
            arcpy.AddError("No se sabe a que geodatabase volver.")
            return

        reader = _open_reader(workspace)
        try:
            synchronizer = Synchronizer(
                package,
                reader,
                conflict_policy=parameters[4].valueAsText,
                apply_deletes=bool(parameters[3].value),
                progress=_progress,
            )
            report = synchronizer.detect()
            for line in report.format().splitlines():
                arcpy.AddMessage(line)

            if parameters[2].value:
                arcpy.AddMessage("")
                arcpy.AddMessage("Aplicando cambios en %s" % workspace)
                report = synchronizer.apply(report)
                for line in report.format().splitlines():
                    arcpy.AddMessage(line)
            else:
                arcpy.AddWarning(
                    "Simulacion: no se ha escrito nada en la geodatabase. "
                    "Marque 'Aplicar los cambios' para confirmar."
                )

            for change in report.conflicts:
                arcpy.AddWarning(
                    "Conflicto en %s (%s): %s"
                    % (change.layer, change.key_value, change.message)
                )
            for error in report.errors:
                arcpy.AddError(error)

            if parameters[5].valueAsText:
                report.write(parameters[5].valueAsText)
                arcpy.AddMessage("Informe: %s" % parameters[5].valueAsText)
        finally:
            reader.close()


# ----------------------------------------------------------------------
class PublicarEnQFieldCloud(object):
    def __init__(self):
        self.label = "4 · Publicar en QFieldCloud"
        self.description = (
            "Sube el paquete a QFieldCloud para que los equipos de campo lo "
            "descarguen sin cable."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        package = arcpy.Parameter(
            displayName="Carpeta del paquete",
            name="package",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input",
        )
        project = arcpy.Parameter(
            displayName="Nombre del proyecto en la nube",
            name="project",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        server = arcpy.Parameter(
            displayName="Servidor",
            name="server",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        server.value = "https://app.qfield.cloud"

        user = arcpy.Parameter(
            displayName="Usuario",
            name="user",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        password = arcpy.Parameter(
            displayName="Contrasena",
            name="password",
            datatype="GPStringHidden",
            parameterType="Required",
            direction="Input",
        )
        owner = arcpy.Parameter(
            displayName="Propietario (usuario u organizacion)",
            name="owner",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        return [package, project, server, user, password, owner]

    def execute(self, parameters, messages):  # noqa: ARG002
        from qfieldesri.core.cloudapi import QFieldCloudClient

        client = QFieldCloudClient(parameters[2].valueAsText)
        client.login(parameters[3].valueAsText, parameters[4].valueAsText)
        user = client.user()
        owner = parameters[5].valueAsText or user.get("username")

        project = client.ensure_project(parameters[1].valueAsText, owner)
        arcpy.AddMessage(
            "Proyecto en la nube: %s/%s" % (owner, project.get("name"))
        )
        uploaded = client.upload_package(
            project["id"], parameters[0].valueAsText, progress=_progress
        )
        arcpy.AddMessage("Archivos subidos: %d" % len(uploaded))


# ----------------------------------------------------------------------
class RecuperarDeQFieldCloud(object):
    def __init__(self):
        self.label = "5 · Recuperar de QFieldCloud"
        self.description = (
            "Descarga lo capturado en campo desde QFieldCloud a una carpeta "
            "local, lista para la herramienta de sincronizacion."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        project = arcpy.Parameter(
            displayName="Nombre del proyecto en la nube",
            name="project",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        destination = arcpy.Parameter(
            displayName="Carpeta de destino",
            name="destination",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input",
        )
        server = arcpy.Parameter(
            displayName="Servidor",
            name="server",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        server.value = "https://app.qfield.cloud"

        user = arcpy.Parameter(
            displayName="Usuario",
            name="user",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        password = arcpy.Parameter(
            displayName="Contrasena",
            name="password",
            datatype="GPStringHidden",
            parameterType="Required",
            direction="Input",
        )
        owner = arcpy.Parameter(
            displayName="Propietario (usuario u organizacion)",
            name="owner",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        return [project, destination, server, user, password, owner]

    def execute(self, parameters, messages):  # noqa: ARG002
        from qfieldesri.core.cloudapi import QFieldCloudClient

        client = QFieldCloudClient(parameters[2].valueAsText)
        client.login(parameters[3].valueAsText, parameters[4].valueAsText)
        user = client.user()
        owner = parameters[5].valueAsText or user.get("username")

        project = client.find_project(parameters[0].valueAsText, owner)
        if project is None:
            arcpy.AddError(
                "No existe el proyecto '%s' para '%s'."
                % (parameters[0].valueAsText, owner)
            )
            return
        downloaded = client.download_package(
            project["id"], parameters[1].valueAsText, progress=_progress
        )
        arcpy.AddMessage("Archivos descargados: %d" % len(downloaded))
        arcpy.AddMessage(
            "Ahora ejecute '3 · Sincronizar desde QField' sobre esa carpeta."
        )
