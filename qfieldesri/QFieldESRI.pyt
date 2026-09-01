# -*- coding: utf-8 -*-
"""Caja de herramientas de qfieldESRI para ArcGIS Desktop.

Expone qfieldESRI como herramientas de geoprocesamiento, que es la forma
nativa de extender ArcMap y ArcGIS Pro y la unica que ademas queda disponible
en ModelBuilder y en la ventana de Python. Lo mismo se puede hacer con la
aplicacion de escritorio (``QFieldESRI.py``) o con la linea de comandos.

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
from qfieldesri.core.scope import Scope, ScopeKind, ScopeResolver  # noqa: E402
from qfieldesri.core.synchronizer import ConflictPolicy, Synchronizer  # noqa: E402
from qfieldesri.profiles import available_profiles, load_profile  # noqa: E402
from qfieldesri.readers.arcpy_reader import ArcpyReader  # noqa: E402
from qfieldesri.version import __version__  # noqa: E402


#: Opcion "sin acotar" del desplegable de ambito.
SCOPE_ALL = "Toda la geodatabase"

#: Separador entre el codigo y su descripcion en los desplegables de valores.
_CODE_SEPARATOR = " - "


def _by_name(parameters):
    """Parametros por nombre: mas legible y a prueba de reordenaciones."""
    return dict((parameter.name, parameter) for parameter in parameters)


def _multi(parameter):
    """Lista de un parametro multivalor, venga como lista o como texto."""
    value = parameter.valueAsText
    if not value:
        return []
    return [item.strip("';\"") for item in value.split(";") if item.strip()]


def _scope_kind_from_label(label):
    """Del texto del desplegable al identificador del ambito."""
    if not label or label == SCOPE_ALL:
        return None
    for kind, text in ScopeKind.LABELS.items():
        if text == label:
            return kind
    return label


def _scope_code(item):
    """Del texto 'codigo - descripcion' al codigo que se guarda."""
    return item.split(_CODE_SEPARATOR)[0].strip()


def _scope_value_labels(workspace, profile_name, kind, only_present_in=None):
    """Valores elegibles del ambito, leidos de la geodatabase.

    Nunca de una lista fija: los alimentadores y las subestaciones cambian en
    cada Unidad de Negocio.
    """
    if not kind:
        return []
    try:
        reader = _open_reader(workspace)
    except Exception:  # noqa: BLE001 - geodatabase aun no valida en el dialogo
        return []
    try:
        info = reader.describe_workspace()
        resolver = ScopeResolver(info, load_profile(profile_name or "cnel_ep"), reader)
        values = resolver.available_values(kind, only_present_in=only_present_in)
        return [
            "%s%s%s" % (code, _CODE_SEPARATOR, label) if label else str(code)
            for code, label in values
        ]
    except Exception as error:  # noqa: BLE001
        arcpy.AddWarning("No se pudieron leer los valores del ambito: %s" % error)
        return []
    finally:
        reader.close()


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
        self.label = "2 - Empaquetar para QField"
        self.description = (
            "Genera la carpeta que se copia al dispositivo: un GeoPackage con "
            "los datos y el proyecto con los formularios, dominios, subtipos y "
            "relaciones ya traducidos. La exportacion se acota por alimentador, "
            "subestacion, poligono de sector o division politica."
        )
        self.canRunInBackground = False

    # -- parametros ----------------------------------------------------
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

        # --- ambito de exportacion ------------------------------------
        scope_kind = arcpy.Parameter(
            displayName="Acotar la exportacion por",
            name="scope_kind",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
            category="Ambito de exportacion",
        )
        scope_kind.filter.list = [SCOPE_ALL] + [
            ScopeKind.LABELS[kind] for kind in ScopeKind.ALL
        ]
        scope_kind.value = SCOPE_ALL

        scope_values = arcpy.Parameter(
            displayName="Valores (alimentadores, subestaciones, parroquias...)",
            name="scope_values",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True,
            category="Ambito de exportacion",
        )
        only_present = arcpy.Parameter(
            displayName="Ofrecer solo los valores presentes en esta clase",
            name="scope_present_in",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            category="Ambito de exportacion",
        )
        polygon = arcpy.Parameter(
            displayName="Poligono del sector (capa de poligonos)",
            name="scope_polygon",
            datatype="GPFeatureLayer",
            parameterType="Optional",
            direction="Input",
            category="Ambito de exportacion",
        )
        follow = arcpy.Parameter(
            displayName="Arrastrar las tablas Unidad de los Puestos exportados",
            name="scope_follow",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
            category="Ambito de exportacion",
        )
        follow.value = True

        # --- seleccion fina de clases ---------------------------------
        layers = arcpy.Parameter(
            displayName="Clases a incluir (vacio = todas)",
            name="layers",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True,
            category="Clases y campos",
        )
        read_only = arcpy.Parameter(
            displayName="Clases de solo consulta",
            name="read_only",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True,
            category="Clases y campos",
        )
        filters = arcpy.Parameter(
            displayName="Filtros adicionales por clase (se suman al ambito)",
            name="filters",
            datatype="GPValueTable",
            parameterType="Optional",
            direction="Input",
            category="Clases y campos",
        )
        filters.columns = [["GPString", "Clase"], ["GPSQLExpression", "WHERE"]]

        photos = arcpy.Parameter(
            displayName="Campos de fotografia (Clase / Campo)",
            name="photos",
            datatype="GPValueTable",
            parameterType="Optional",
            direction="Input",
            category="Clases y campos",
        )
        photos.columns = [["GPString", "Clase"], ["GPString", "Campo"]]

        related = arcpy.Parameter(
            displayName="Incluir las tablas relacionadas (Unidades)",
            name="include_related",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
            category="Clases y campos",
        )
        related.value = True

        threshold = arcpy.Parameter(
            displayName="Dominios con mas valores que este numero van como catalogo",
            name="threshold",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input",
            category="Opciones avanzadas",
        )
        threshold.value = 40

        force = arcpy.Parameter(
            displayName="Empaquetar aunque la verificacion encuentre errores",
            name="force",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
            category="Opciones avanzadas",
        )
        force.value = False

        config_out = arcpy.Parameter(
            displayName="Guardar la configuracion en (opcional)",
            name="config_out",
            datatype="DEFile",
            parameterType="Optional",
            direction="Output",
            category="Opciones avanzadas",
        )
        return [
            workspace,
            output,
            name,
            title,
            profile,
            scope_kind,
            scope_values,
            only_present,
            polygon,
            follow,
            layers,
            read_only,
            filters,
            photos,
            related,
            threshold,
            force,
            config_out,
        ]

    # -- comportamiento del dialogo ------------------------------------
    def updateParameters(self, parameters):
        """Rellena los desplegables leyendo la geodatabase elegida.

        Los alimentadores y las subestaciones se leen del dominio de la
        geodatabase activa, nunca de una lista fija: cambian en cada Unidad de
        Negocio.
        """
        values = _by_name(parameters)
        workspace = values["workspace"].valueAsText

        if values["workspace"].altered and workspace:
            names = _list_layer_names(workspace)
            for key in ("layers", "read_only", "scope_present_in"):
                values[key].filter.list = names

        kind = _scope_kind_from_label(values["scope_kind"].valueAsText)
        is_polygon = kind == ScopeKind.POLIGONO
        values["scope_polygon"].enabled = is_polygon
        values["scope_values"].enabled = bool(kind) and not is_polygon
        values["scope_present_in"].enabled = values["scope_values"].enabled

        if values["scope_values"].enabled and workspace:
            if (
                values["scope_kind"].altered
                or values["scope_present_in"].altered
                or values["workspace"].altered
            ):
                values["scope_values"].filter.list = _scope_value_labels(
                    workspace,
                    values["profile"].valueAsText,
                    kind,
                    values["scope_present_in"].valueAsText,
                )
        return

    def updateMessages(self, parameters):
        values = _by_name(parameters)
        kind = _scope_kind_from_label(values["scope_kind"].valueAsText)
        if kind == ScopeKind.POLIGONO and not values["scope_polygon"].valueAsText:
            values["scope_polygon"].setIDMessage("ERROR", 530)
        elif kind and kind != ScopeKind.POLIGONO and not values["scope_values"].value:
            values["scope_values"].setWarningMessage(
                "Sin valores elegidos se exportara la geodatabase completa."
            )
        return

    # -- ejecucion -----------------------------------------------------
    def execute(self, parameters, messages):  # noqa: ARG002, PLR0912
        values = _by_name(parameters)
        workspace = values["workspace"].valueAsText

        selected = _multi(values["layers"])
        read_only = _multi(values["read_only"])

        config = PackagingConfig(
            workspace=workspace,
            output_dir=values["output_dir"].valueAsText,
            project_name=values["project_name"].valueAsText,
            title=values["title"].valueAsText or values["project_name"].valueAsText,
            profile=values["profile"].valueAsText,
            include_related_tables=bool(values["include_related"].value),
            big_domain_threshold=int(values["threshold"].value or 40),
            scope=self._scope(values),
        )

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

            for row in values["filters"].value or []:
                config.layer_config(str(row[0])).where_clause = str(row[1] or "")
            for row in values["photos"].value or []:
                config.layer_config(str(row[0])).attachment_fields[str(row[1])] = (
                    "image"
                )

            result = WorkspaceChecker(info, config).check()
            _report(result)
            if result.has_errors and not values["force"].value:
                arcpy.AddError(
                    "Se encontraron errores. Corrijalos o marque la casilla "
                    "para empaquetar de todos modos."
                )
                return

            arcpy.SetProgressor("step", "Empaquetando para QField", 0, 100, 1)
            packaging = Packager(reader, config, progress=_progress).run()

            arcpy.AddMessage("")
            if packaging.scope_description:
                for line in packaging.scope_description.splitlines():
                    arcpy.AddMessage(line)
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
            if packaging.total_features == 0:
                arcpy.AddWarning(
                    "El paquete salio vacio: revise el ambito elegido."
                )
            arcpy.AddMessage("")
            arcpy.AddMessage(
                "Copie la carpeta completa al dispositivo (o publiquela con la "
                "herramienta 4) y abra el proyecto desde QField."
            )

            if values["config_out"].valueAsText:
                config.save(values["config_out"].valueAsText)
                arcpy.AddMessage(
                    "Configuracion guardada en %s" % values["config_out"].valueAsText
                )
        finally:
            reader.close()
            arcpy.ResetProgressor()

    def _scope(self, values):
        """Traduce lo elegido en el dialogo a un ambito de exportacion."""
        kind = _scope_kind_from_label(values["scope_kind"].valueAsText)
        follow = bool(values["scope_follow"].value)
        if not kind:
            return Scope()
        if kind == ScopeKind.POLIGONO:
            wkt, code = _aoi_to_wkt(values["scope_polygon"].valueAsText)
            return Scope(
                kind, polygon_wkt=wkt, polygon_crs=code, follow_relationships=follow
            )
        return Scope(
            kind,
            values=[_scope_code(item) for item in _multi(values["scope_values"])],
            follow_relationships=follow,
        )

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
