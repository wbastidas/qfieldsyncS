# -*- coding: utf-8 -*-
r"""Linea de comandos de qfieldESRI.

Todo lo que hace el Python Toolbox de ArcGIS se puede hacer tambien desde aqui,
que es lo que permite automatizar el ciclo (por ejemplo, un empaquetado nocturno
por alimentador que se publica en QFieldCloud).

Ejecutar con el Python de ArcGIS para tener arcpy::

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" \\
        -m qfieldesri analizar --gdb C:/datos/GYE.gdb

Subcomandos:

``analizar``      inventario de la geodatabase y verificacion previa
``ambitos``       lista los alimentadores, subestaciones o parroquias elegibles
``estilo``        exporta la simbologia como archivo editable, o la revisa
``configurar``    genera un archivo de configuracion editable
``empaquetar``    geodatabase -> carpeta de proyecto QField
``sincronizar``   carpeta de QField -> geodatabase
``publicar``      sube el paquete a QFieldCloud
``recuperar``     descarga de QFieldCloud lo capturado en campo
``demo``          genera un paquete de ejemplo sin necesidad de ArcGIS
"""

import argparse
import io
import json
import os
import sys

from .core.checker import WorkspaceChecker
from .core.config import LayerAction, PackagingConfig
from .core.packager import Packager, load_manifest
from .core.scope import Scope, ScopeKind, ScopeResolver
from .core.selection import Selection, SelectionResolver
from .core.synchronizer import ConflictPolicy, Synchronizer
from .profiles import available_profiles, load_profile
from .readers import get_reader
from .symbology import StyleSheet, SymbologyResolver, load_symbology

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CHECK_FAILED = 2


def _out(text=""):
    stream = sys.stdout
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:  # pragma: no cover - consolas de Windows
        stream.write(text.encode("ascii", "replace").decode("ascii") + "\n")
    stream.flush()


def _progress(message, percent=None):
    if percent is None:
        _out("    %s" % message)
    else:
        _out("[%3d%%] %s" % (percent, message))


def _open_reader(workspace, engine):
    reader = get_reader(workspace, prefer=engine)
    reader.open()
    return reader


# ----------------------------------------------------------------------
def cmd_analizar(args):
    reader = _open_reader(args.gdb, args.motor)
    try:
        workspace = reader.describe_workspace()
        config = _config_from_args(args, workspace)
        result = WorkspaceChecker(workspace, config).check()

        _out("Geodatabase: %s (%s)" % (workspace.path, workspace.workspace_type))
        _out("Motor de lectura: %s" % reader.name)
        _out(
            "Clases: %d  ·  dominios: %d  ·  relaciones: %d"
            % (
                len(workspace.layers),
                len(workspace.domains),
                len(workspace.relationships),
            )
        )
        if workspace.feature_datasets:
            _out("Feature datasets: %s" % ", ".join(workspace.feature_datasets))
        _out("")
        for layer in workspace.layers:
            _out(
                "  %-40s %-12s %3d campos %s"
                % (
                    layer.name,
                    layer.geometry_type or "tabla",
                    len(layer.fields),
                    "%d subtipos" % len(layer.subtypes) if layer.subtypes else "",
                )
            )
        _out("")
        _out(result.format())

        if args.json:
            _write_json(
                args.json,
                {
                    "workspace": workspace.path,
                    "workspace_type": workspace.workspace_type,
                    "layers": [
                        {
                            "name": layer.name,
                            "geometry_type": layer.geometry_type,
                            "fields": len(layer.fields),
                            "subtypes": len(layer.subtypes),
                            "globalid": layer.globalid_field,
                        }
                        for layer in workspace.layers
                    ],
                    "domains": sorted(workspace.domains),
                    "relationships": [
                        relationship.name for relationship in workspace.relationships
                    ],
                    "feedback": result.to_list(),
                },
            )
        return EXIT_CHECK_FAILED if result.has_errors else EXIT_OK
    finally:
        reader.close()


def cmd_ambitos(args):
    """Lista los valores elegibles de un ambito, leidos de la geodatabase."""
    reader = _open_reader(args.gdb, args.motor)
    try:
        workspace = reader.describe_workspace()
        profile = load_profile(args.perfil)
        resolver = ScopeResolver(workspace, profile, reader)

        if not args.ambito:
            _out("Ambitos que este perfil sabe resolver:")
            for kind in profile.supported_scopes():
                _out("  %-14s %s" % (kind, ScopeKind.LABELS.get(kind, "")))
            _out("  %-14s %s" % (ScopeKind.POLIGONO, "Poligono de sector"))
            return EXIT_OK

        values = resolver.available_values(
            args.ambito, only_present_in=args.presentes_en
        )
        if not values:
            _out(
                "No hay valores para el ambito '%s'. Puede que la geodatabase "
                "no tenga el dominio correspondiente." % args.ambito
            )
            return EXIT_OK
        _out(
            "%s (%d valores):"
            % (ScopeKind.LABELS.get(args.ambito, args.ambito), len(values))
        )
        for code, label in values:
            _out("  %-14s %s" % (code, label))
        return EXIT_OK
    finally:
        reader.close()


def cmd_conjuntos(args):
    """Lista los conjuntos tematicos que se pueden exportar.

    Responde a la pregunta previa a cualquier exportacion: "¿que puedo
    llevarme?". Los conjuntos del perfil son conocimiento del modelo; los de
    geometria salen de la propia geodatabase y valen para cualquiera.
    """
    reader = _open_reader(args.gdb, args.motor)
    try:
        workspace = reader.describe_workspace()
        resolver = SelectionResolver(workspace, load_profile(args.perfil))
        sets = resolver.available_sets()

        if args.conjunto:
            chosen = resolver.set_by_id(args.conjunto)
            if chosen is None:
                _out(
                    "No existe el conjunto '%s'. Disponibles: %s"
                    % (args.conjunto, ", ".join(item.id for item in sets))
                )
                return EXIT_ERROR
            _out("%s (%d clases)" % (chosen.name, len(chosen)))
            if chosen.description:
                _out("  %s" % chosen.description)
            for name in sorted(chosen.classes):
                _out("  %s" % name)
            return EXIT_OK

        _out("Conjuntos disponibles en %s:" % workspace.path)
        _out("")
        for item in sets:
            _out(
                "  %-16s %-34s %3d clases  (%s)"
                % (item.id, item.name, len(item), item.source)
            )
        _out("")
        _out(
            "Use '--conjunto <id>' para ver sus clases, o pase "
            "'--conjunto' a 'empaquetar' para exportar solo ese."
        )
        return EXIT_OK
    finally:
        reader.close()


def cmd_estilo(args):
    """Exporta la simbologia como archivo de estilo editable.

    Es la forma comoda de empezar: se genera el estilo que qfieldESRI usaria
    ahora mismo —con lo que haya deducido de la geodatabase o importado de
    ArcGIS— y se abre el archivo para retocar los colores a mano.
    """
    from .core.packager import build_stylesheet

    reader = _open_reader(args.gdb, args.motor)
    try:
        workspace = reader.describe_workspace()
        profile = load_profile(args.perfil)

        imported = {}
        if args.simbologia:
            imported, warnings = load_symbology(args.simbologia)
            _out("Simbologia importada: %d capas." % len(imported))
            for warning in warnings:
                _out("  AVISO: %s" % warning)

        base = StyleSheet.load(args.estilo) if args.estilo else None
        resolver = SymbologyResolver(
            profile=profile, stylesheet=base, imported=imported
        )

        sheet = build_stylesheet(
            workspace,
            resolver,
            description=(
                "Estilos de qfieldESRI para %s. Edite colores, formas, "
                "etiquetas y escalas, y pase este archivo con --estilo."
                % workspace.path
            ),
        )

        for warning in resolver.warnings:
            _out("AVISO: %s" % warning)
        _out(resolver.summary())

        if args.salida:
            sheet.save(args.salida)
            _out("")
            _out(
                "Estilo escrito en %s (%d capas). Editelo y pasele "
                "'--estilo %s' al empaquetar." % (args.salida, len(sheet), args.salida)
            )
        else:
            for name in sorted(sheet.layers):
                _out("  %-34s %s" % (name, resolver.sources.get(name, "")))
        return EXIT_OK
    finally:
        reader.close()


def cmd_configurar(args):
    # Aqui --salida es el archivo de configuracion, no una carpeta.
    destination = args.salida or "qfieldesri_config.json"
    reader = _open_reader(args.gdb, args.motor)
    try:
        workspace = reader.describe_workspace()
        config = _config_from_args(args, workspace)
        for layer in workspace.layers:
            layer_config = config.layer_config(layer.name)
            if args.solo and layer.name not in args.solo:
                layer_config.action = LayerAction.REMOVE
        config.save(destination)
        _out(
            "Configuracion escrita en %s (%d clases). Editela y pasela a "
            "'empaquetar --config'." % (destination, len(config.layers))
        )
        return EXIT_OK
    finally:
        reader.close()


def cmd_empaquetar(args):
    reader = _open_reader(args.gdb, args.motor)
    try:
        if args.config:
            config = PackagingConfig.load(args.config)
            config.workspace = args.gdb or config.workspace
            if args.salida:
                config.output_dir = args.salida
        else:
            config = _config_from_args(args, reader.describe_workspace())

        if not args.omitir_verificacion:
            workspace = reader.describe_workspace()
            result = WorkspaceChecker(workspace, config).check()
            _out(result.format())
            if result.has_errors and not args.forzar:
                _out("")
                _out(
                    "Se encontraron errores. Corrijalos o use --forzar para "
                    "empaquetar de todos modos."
                )
                return EXIT_CHECK_FAILED
            _out("")

        result = Packager(reader, config, progress=_progress).run()
        _out("")
        if result.selection_description:
            _out(result.selection_description)
            _out("")
        if result.scope_description:
            _out(result.scope_description)
            _out("")
        if result.symbology_description:
            _out(result.symbology_description)
        _out("Paquete: %s" % result.project_dir)
        _out("Proyecto: %s" % os.path.basename(result.project_file))
        for name in sorted(result.layer_counts):
            _out("  %-40s %8d entidades" % (name, result.layer_counts[name]))
        _out("  %-40s %8d entidades" % ("TOTAL", result.total_features))
        return EXIT_OK
    finally:
        reader.close()


def cmd_sincronizar(args):
    manifest = load_manifest(args.paquete)
    workspace = args.gdb or manifest.get("workspace")
    if not workspace:
        _out("No se sabe a que geodatabase volver: indique --gdb.")
        return EXIT_ERROR

    reader = _open_reader(workspace, args.motor)
    try:
        synchronizer = Synchronizer(
            args.paquete,
            reader,
            conflict_policy=args.conflictos,
            apply_deletes=args.aplicar_bajas,
            progress=_progress,
        )
        report = synchronizer.detect()
        _out("")
        _out(report.format())

        if args.aplicar:
            _out("")
            _out("Aplicando cambios en %s ..." % workspace)
            report = synchronizer.apply(report)
            _out(report.format())
        else:
            _out("")
            _out("Simulacion: no se ha escrito nada. Use --aplicar para confirmar.")

        if args.informe:
            report.write(args.informe)
            _out("Informe: %s" % args.informe)
        return EXIT_OK
    finally:
        reader.close()


def cmd_publicar(args):
    from .core.cloudapi import QFieldCloudClient

    client = QFieldCloudClient(
        args.servidor, token=args.token, verify_ssl=not args.sin_verificar_ssl
    )
    if not args.token:
        password = args.password or _prompt_password()
        client.login(args.usuario, password)
    user = client.user()
    owner = args.propietario or user.get("username")

    project = client.ensure_project(
        args.proyecto, owner, description=args.descripcion or ""
    )
    _out("Proyecto en la nube: %s/%s (%s)" % (owner, project["name"], project["id"]))
    uploaded = client.upload_package(project["id"], args.paquete, progress=_progress)
    _out("Archivos subidos: %d" % len(uploaded))
    return EXIT_OK


def cmd_recuperar(args):
    from .core.cloudapi import QFieldCloudClient

    client = QFieldCloudClient(
        args.servidor, token=args.token, verify_ssl=not args.sin_verificar_ssl
    )
    if not args.token:
        password = args.password or _prompt_password()
        client.login(args.usuario, password)
    user = client.user()
    owner = args.propietario or user.get("username")

    project = client.find_project(args.proyecto, owner)
    if project is None:
        _out("No existe el proyecto '%s' para '%s'." % (args.proyecto, owner))
        return EXIT_ERROR
    downloaded = client.download_package(
        project["id"], args.destino, progress=_progress
    )
    _out("Archivos descargados: %d en %s" % (len(downloaded), args.destino))
    return EXIT_OK


def cmd_demo(args):
    from .demo import build_reader

    reader = build_reader()
    config = PackagingConfig(
        workspace="demo.gdb",
        output_dir=args.salida,
        project_name=args.nombre,
        title="Demostracion qfieldESRI",
        profile="cnel_ep",
    )
    config.layer_config("EstructuraSoporte").attachment_fields = {"FOTO": "image"}
    result = Packager(reader, config, progress=_progress).run()
    _out("")
    _out("Paquete de demostracion en %s" % result.project_dir)
    _out("Copie la carpeta al dispositivo y abra el proyecto desde QField.")
    return EXIT_OK


# ----------------------------------------------------------------------
def _scope_from_args(args):
    """Arma el ambito de exportacion a partir de los argumentos."""
    kind = getattr(args, "ambito", None)
    if not kind:
        return Scope()
    if kind == ScopeKind.POLIGONO:
        return Scope(
            kind,
            polygon_wkt=getattr(args, "poligono_wkt", None),
            polygon_layer=getattr(args, "poligono", None),
            polygon_where=getattr(args, "poligono_donde", None),
            follow_relationships=not getattr(args, "sin_unidades", False),
        )
    return Scope(
        kind,
        values=getattr(args, "valores", None) or [],
        follow_relationships=not getattr(args, "sin_unidades", False),
    )


def _selection_from_args(args):
    """Que clases se exportan, segun lo pedido en la linea de comandos."""
    return Selection(
        sets=getattr(args, "conjunto", None) or [],
        classes=getattr(args, "clases", None) or [],
        exclude=getattr(args, "sin_clases", None) or [],
        include_related=not getattr(args, "sin_relacionadas", False),
    )


def _config_from_args(args, workspace=None):
    config = PackagingConfig(
        workspace=getattr(args, "gdb", None) or "",
        output_dir=getattr(args, "salida", None) or ".",
        project_name=getattr(args, "nombre", None) or "qfieldesri",
        title=getattr(args, "titulo", None),
        profile=getattr(args, "perfil", "cnel_ep"),
        area_of_interest=getattr(args, "area", None),
        crs_code=getattr(args, "crs", None),
        include_related_tables=not getattr(args, "sin_tablas_relacionadas", False),
        big_domain_threshold=getattr(args, "umbral_dominio", 40),
        scope=_scope_from_args(args),
        selection=_selection_from_args(args),
        symbology_source=getattr(args, "simbologia", None),
        style_file=getattr(args, "estilo", None),
    )
    solo = getattr(args, "solo", None)
    if solo and workspace is not None:
        for layer in workspace.layers:
            config.layer_config(layer.name).action = (
                LayerAction.COPY if layer.name in solo else LayerAction.REMOVE
            )
    where = getattr(args, "filtro", None)
    if where:
        for item in where:
            if "=" not in item:
                continue
            name, clause = item.split("=", 1)
            config.layer_config(name.strip()).where_clause = clause.strip()
    solo_lectura = getattr(args, "solo_lectura", None)
    for name in solo_lectura or []:
        config.layer_config(name).action = LayerAction.READ_ONLY
    fotos = getattr(args, "foto", None)
    for item in fotos or []:
        if ":" not in item:
            continue
        name, field = item.split(":", 1)
        config.layer_config(name.strip()).attachment_fields[field.strip()] = "image"
    return config


def _write_json(path, payload):
    with io.open(path, "w", encoding="utf-8") as handle:
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        try:
            handle.write(unicode(text))
        except NameError:
            handle.write(text)


def _prompt_password():
    import getpass

    return getpass.getpass("Contrasena de QFieldCloud: ")


def _add_common(parser, with_output=True):
    parser.add_argument("--gdb", help="Ruta a la File Geodatabase o al .sde")
    parser.add_argument(
        "--motor",
        choices=("arcpy", "ogr", "memory"),
        default=None,
        help="Motor de lectura (por omision: arcpy si esta disponible)",
    )
    if with_output:
        # Sin valor por omision: asi ``empaquetar --config`` conserva la
        # carpeta de salida que trae la configuracion salvo que se pida otra.
        parser.add_argument(
            "--salida", help="Carpeta de salida (por omision, la carpeta actual)"
        )
        parser.add_argument(
            "--nombre",
            default="qfieldesri",
            help="Nombre del proyecto (y de la carpeta)",
        )
        parser.add_argument("--titulo", help="Titulo del proyecto en QField")
    parser.add_argument(
        "--perfil",
        default="cnel_ep",
        help="Perfil de modelo de datos (%s)" % ", ".join(available_profiles()),
    )


def build_parser():  # noqa: PLR0915
    parser = argparse.ArgumentParser(
        prog="qfieldesri",
        description="Migracion de geodatabases de ESRI a QField y de vuelta.",
    )
    subparsers = parser.add_subparsers(dest="comando")

    analizar = subparsers.add_parser(
        "analizar", help="Inventario y verificacion previa de la geodatabase"
    )
    _add_common(analizar, with_output=False)
    analizar.add_argument("--json", help="Escribe el resultado en un archivo JSON")
    analizar.set_defaults(func=cmd_analizar)

    ambitos = subparsers.add_parser(
        "ambitos",
        help="Lista los alimentadores, subestaciones o parroquias elegibles",
    )
    _add_common(ambitos, with_output=False)
    ambitos.add_argument(
        "--ambito",
        choices=ScopeKind.ALL,
        help="Ambito a listar; sin esto se listan los ambitos disponibles",
    )
    ambitos.add_argument(
        "--presentes-en",
        dest="presentes_en",
        help="Devolver solo los valores que aparecen en esa clase",
    )
    ambitos.set_defaults(func=cmd_ambitos)

    conjuntos = subparsers.add_parser(
        "conjuntos",
        help="Lista los conjuntos tematicos que se pueden exportar",
    )
    _add_common(conjuntos, with_output=False)
    conjuntos.add_argument(
        "--conjunto", help="Ver las clases de ese conjunto en vez de la lista"
    )
    conjuntos.set_defaults(func=cmd_conjuntos)

    estilo = subparsers.add_parser(
        "estilo",
        help="Exporta la simbologia como archivo de estilo editable",
    )
    _add_common(estilo, with_output=False)
    estilo.add_argument(
        "--salida", help="Archivo de estilo a escribir; sin esto solo se lista"
    )
    estilo.add_argument(
        "--simbologia", help="Carpeta de .lyrx o archivo .lyrx/.lyr/.mxd de origen"
    )
    estilo.add_argument("--estilo", help="Estilo de partida al que anadir")
    estilo.set_defaults(func=cmd_estilo)

    configurar = subparsers.add_parser(
        "configurar", help="Genera un archivo de configuracion editable"
    )
    _add_common(configurar)
    configurar.add_argument("--solo", nargs="+", help="Clases a incluir")
    configurar.set_defaults(func=cmd_configurar)

    empaquetar = subparsers.add_parser(
        "empaquetar", help="Genera el proyecto de QField"
    )
    _add_common(empaquetar)
    empaquetar.add_argument("--config", help="Archivo de configuracion JSON")
    empaquetar.add_argument("--solo", nargs="+", help="Clases a incluir")
    empaquetar.add_argument(
        "--solo-lectura",
        nargs="+",
        dest="solo_lectura",
        help="Clases que van como contexto no editable",
    )
    empaquetar.add_argument(
        "--filtro",
        nargs="+",
        help="Clausulas WHERE por clase, p. ej. Barra=\"ALIMENTADORID='04BH070T11'\"",
    )
    empaquetar.add_argument(
        "--foto", nargs="+", help="Campos de fotografia, p. ej. EstructuraSoporte:FOTO"
    )
    empaquetar.add_argument(
        "--ambito",
        choices=ScopeKind.ALL,
        help="Acotar la exportacion por alimentador, subestacion, division "
        "politica o poligono de sector",
    )
    empaquetar.add_argument(
        "--valores",
        nargs="+",
        help="Codigos del ambito, p. ej. --ambito alimentador "
        "--valores 04BH070T11 04SM320T22",
    )
    empaquetar.add_argument(
        "--poligono", help="Clase de poligonos que delimita el sector"
    )
    empaquetar.add_argument(
        "--poligono-donde",
        dest="poligono_donde",
        help="Clausula WHERE para elegir que poligonos del sector se usan",
    )
    empaquetar.add_argument(
        "--poligono-wkt", dest="poligono_wkt", help="Poligono del sector en WKT"
    )
    empaquetar.add_argument(
        "--sin-unidades",
        dest="sin_unidades",
        action="store_true",
        help="No arrastrar las tablas Unidad de los Puestos exportados",
    )
    empaquetar.add_argument(
        "--area",
        help="Area de interes en WKT (equivale a --ambito poligono --poligono-wkt)",
    )
    empaquetar.add_argument(
        "--conjunto",
        nargs="+",
        help="Conjuntos tematicos a exportar (vea el subcomando 'conjuntos')",
    )
    empaquetar.add_argument(
        "--clases",
        nargs="+",
        help="Clases sueltas que se anaden a lo elegido",
    )
    empaquetar.add_argument(
        "--sin-clases",
        nargs="+",
        dest="sin_clases",
        help="Clases que se quitan aunque vengan en un conjunto",
    )
    empaquetar.add_argument(
        "--sin-relacionadas",
        action="store_true",
        dest="sin_relacionadas",
        help="No arrastrar las clases que dependen de las elegidas",
    )
    empaquetar.add_argument(
        "--simbologia",
        help="De donde tomar la simbologia de ArcGIS: una carpeta de .lyrx, un "
        ".lyrx/.lyr/.mxd/.aprx, o CURRENT para el mapa abierto",
    )
    empaquetar.add_argument(
        "--estilo",
        help="Archivo de estilo de qfieldESRI; manda sobre lo importado de "
        "ArcGIS (se genera con el subcomando 'estilo')",
    )
    empaquetar.add_argument("--crs", type=int, help="Codigo EPSG de salida")
    empaquetar.add_argument(
        "--umbral-dominio",
        type=int,
        default=40,
        dest="umbral_dominio",
        help="Dominios con mas valores van como catalogo",
    )
    empaquetar.add_argument(
        "--sin-tablas-relacionadas", action="store_true", dest="sin_tablas_relacionadas"
    )
    empaquetar.add_argument(
        "--omitir-verificacion", action="store_true", dest="omitir_verificacion"
    )
    empaquetar.add_argument(
        "--forzar", action="store_true", help="Empaquetar aunque haya errores"
    )
    empaquetar.set_defaults(func=cmd_empaquetar)

    sincronizar = subparsers.add_parser(
        "sincronizar", help="Devuelve a la geodatabase lo capturado en QField"
    )
    sincronizar.add_argument("paquete", help="Carpeta del proyecto de QField")
    sincronizar.add_argument(
        "--gdb", help="Geodatabase destino (por omision, la que indique el manifiesto)"
    )
    sincronizar.add_argument("--motor", choices=("arcpy", "ogr", "memory"))
    sincronizar.add_argument(
        "--aplicar",
        action="store_true",
        help="Escribe en la geodatabase (sin esto, simula)",
    )
    sincronizar.add_argument(
        "--aplicar-bajas",
        action="store_true",
        dest="aplicar_bajas",
        help="Tambien borra lo borrado en campo",
    )
    sincronizar.add_argument(
        "--conflictos", choices=ConflictPolicy.ALL, default=ConflictPolicy.REPORT
    )
    sincronizar.add_argument("--informe", help="Archivo JSON con el detalle")
    sincronizar.set_defaults(func=cmd_sincronizar)

    publicar = subparsers.add_parser("publicar", help="Sube el paquete a QFieldCloud")
    publicar.add_argument("paquete", help="Carpeta del proyecto de QField")
    publicar.add_argument("--proyecto", required=True, help="Nombre en la nube")
    publicar.add_argument("--usuario")
    publicar.add_argument("--password")
    publicar.add_argument("--token", help="Token de sesion, en vez de usuario")
    publicar.add_argument("--propietario", help="Usuario u organizacion propietaria")
    publicar.add_argument("--descripcion")
    publicar.add_argument("--servidor", default="https://app.qfield.cloud")
    publicar.add_argument(
        "--sin-verificar-ssl", action="store_true", dest="sin_verificar_ssl"
    )
    publicar.set_defaults(func=cmd_publicar)

    recuperar = subparsers.add_parser(
        "recuperar", help="Descarga de QFieldCloud lo capturado en campo"
    )
    recuperar.add_argument("--proyecto", required=True)
    recuperar.add_argument("--destino", required=True)
    recuperar.add_argument("--usuario")
    recuperar.add_argument("--password")
    recuperar.add_argument("--token")
    recuperar.add_argument("--propietario")
    recuperar.add_argument("--servidor", default="https://app.qfield.cloud")
    recuperar.add_argument(
        "--sin-verificar-ssl", action="store_true", dest="sin_verificar_ssl"
    )
    recuperar.set_defaults(func=cmd_recuperar)

    demo = subparsers.add_parser(
        "demo", help="Genera un paquete de ejemplo sin necesidad de ArcGIS"
    )
    demo.add_argument("--salida", default=".")  # la demo siempre necesita una
    demo.add_argument("--nombre", default="demo_qfieldesri")
    demo.set_defaults(func=cmd_demo)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_ERROR
    try:
        return args.func(args)
    except Exception as error:
        _out("")
        _out("ERROR: %s" % error)
        if os.environ.get("QFIELDESRI_DEBUG"):
            raise
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
