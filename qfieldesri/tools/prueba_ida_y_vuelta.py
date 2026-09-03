# -*- coding: utf-8 -*-
"""Prueba de humo: el ciclo completo, en los dos origenes.

Ejecuta de punta a punta lo que hace qfieldESRI en produccion —analizar,
elegir que se exporta, empaquetar, simular la jornada de campo y devolver lo
capturado— **dos veces**: contra una File Geodatabase y contra una geodatabase
corporativa de Oracle con ArcSDE, con los nombres calificados que usa el
servidor (``SIGELEC.ESTRUCTURASOPORTE``).

No hace falta ArcGIS ni Oracle: los dos origenes son la misma geodatabase de
demostracion en memoria, con los nombres que le pondria cada uno. Lo que se
comprueba es el programa, no el motor de ESRI.

    python tools/prueba_ida_y_vuelta.py [carpeta]

Sirve para dos cosas: ver el resultado antes de conectar la geodatabase de
produccion, y comprobar despues de cada cambio que el ciclo sigue cerrando.
"""

from __future__ import print_function

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from qfieldesri.core.checker import WorkspaceChecker  # noqa: E402
from qfieldesri.core.config import PackagingConfig  # noqa: E402
from qfieldesri.core.packager import Packager  # noqa: E402
from qfieldesri.core.scope import Scope, ScopeKind  # noqa: E402
from qfieldesri.core.selection import Selection, SelectionResolver  # noqa: E402
from qfieldesri.core.synchronizer import Change, Synchronizer  # noqa: E402
from qfieldesri.demo import build_enterprise_reader, build_reader  # noqa: E402
from qfieldesri.profiles import load_profile  # noqa: E402
from qfieldesri.utils.sqlite_gpkg import connect  # noqa: E402

STYLE = os.path.join(ROOT, "qfieldesri", "profiles", "cnel_ep.estilo.json")


def title(text):
    print()
    print("=" * 74)
    print(text)
    print("=" * 74)


def step(text):
    print()
    print("-- %s" % text)


def run(reader, output_dir, label):
    """El ciclo completo contra un origen."""
    title(label)
    workspace = reader.describe_workspace()
    profile = load_profile("cnel_ep")

    step("1. Analizar")
    print("   Origen:  %s (%s)" % (workspace.path, workspace.workspace_type))
    print(
        "   Clases:  %d  ·  dominios: %d  ·  relaciones: %d"
        % (len(workspace.layers), len(workspace.domains), len(workspace.relationships))
    )
    for layer in workspace.layers:
        print(
            "     %-34s %-9s %d campos"
            % (layer.name, layer.geometry_type or "tabla", len(layer.fields))
        )

    config = PackagingConfig(
        workspace=workspace.path,
        output_dir=output_dir,
        project_name="campo",
        profile="cnel_ep",
        style_file=STYLE,
    )
    checked = WorkspaceChecker(workspace, config).check()
    for feedback in checked.feedbacks:
        print("   %s" % feedback.format().replace("\n", "\n   "))

    step("2. Que se puede exportar")
    resolver = SelectionResolver(workspace, profile)
    for item in resolver.available_sets():
        print("     %-16s %-32s %d clases" % (item.id, item.name, len(item)))

    step("3. Empaquetar: los transformadores del alimentador 04BH070T11")
    config.selection = Selection(sets=["transformadores"])
    config.scope = Scope(ScopeKind.ALIMENTADOR, ["04BH070T11"])
    result = Packager(reader, config).run()
    print("   %s" % result.selection_description.replace("\n", "\n   "))
    print("   %s" % result.scope_description.replace("\n", "\n   "))
    print("   %s" % result.symbology_description)
    for name in sorted(result.layer_counts):
        print("     %-34s %4d entidades" % (name, result.layer_counts[name]))
    print("   Paquete: %s" % result.project_dir)

    step("4. La jornada de campo (se edita el GeoPackage como lo haria QField)")
    table = "UNIDADTRANSFDISTRIBUCION"
    connection = connect(result.gpkg_file)
    connection.execute("UPDATE %s SET POTENCIA = 50 WHERE fid = 1" % table)
    connection.execute(
        "INSERT INTO %s (PUESTOTRANSFDISTGLOBALID, NUMEROSERIE, POTENCIA, FASE) "
        "VALUES ('{U0000001-0000-0000-0000-000000000000}', 'SN-99999', 37.5, 1)" % table
    )
    connection.execute("DELETE FROM %s WHERE fid = 3" % table)
    connection.commit()
    connection.close()
    print("   1 transformador modificado, 1 nuevo, 1 borrado.")

    step("5. Comparar (sin tocar la geodatabase)")
    synchronizer = Synchronizer(result.project_dir, reader, apply_deletes=True)
    report = synchronizer.detect()
    print("   %s" % report.format().replace("\n", "\n   "))

    step("6. Aplicar en el origen")
    applied = synchronizer.apply(report)
    print("   %s" % applied.format().replace("\n", "\n   "))
    print()
    print("   Escrito en la geodatabase:")
    for name, key_field, key_value, attributes, _wkb in reader.updated:
        print(
            "     MODIFICA %-30s %s=%s  POTENCIA=%s"
            % (name, key_field, key_value, attributes.get("POTENCIA"))
        )
    for name, attributes, _wkb in reader.inserted:
        print(
            "     ALTA     %-30s NUMEROSERIE=%s" % (name, attributes.get("NUMEROSERIE"))
        )
    for name, key_field, key_value in reader.deleted:
        print("     BAJA     %-30s %s=%s" % (name, key_field, key_value))

    return {
        "clases": sorted(result.layer_counts),
        "entidades": result.total_features,
        "altas": len(report.of_kind(Change.INSERT)),
        "modificaciones": len(report.of_kind(Change.UPDATE)),
        "bajas": len(report.of_kind(Change.DELETE)),
        "errores": list(applied.errors),
        "escrituras": (
            len(reader.updated),
            len(reader.inserted),
            len(reader.deleted),
        ),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    base = argv[0] if argv else tempfile.mkdtemp(prefix="qfieldesri_prueba_")
    temporary = not argv
    if not os.path.isdir(base):
        os.makedirs(base)

    try:
        local = run(
            build_reader(),
            os.path.join(base, "file_geodatabase"),
            "ORIGEN 1 · File Geodatabase  (C:\\datos\\red.gdb)",
        )
        corporate = run(
            build_enterprise_reader(),
            os.path.join(base, "corporativa"),
            "ORIGEN 2 · Corporativa Oracle 11gR2 + ArcSDE  (SIGELEC.*)",
        )

        title("RESULTADO")
        rows = [("", "File Geodatabase", "Corporativa SIGELEC")]
        rows.append(
            (
                "Clases exportadas",
                str(len(local["clases"])),
                str(len(corporate["clases"])),
            )
        )
        rows.append(
            (
                "Entidades",
                str(local["entidades"]),
                str(corporate["entidades"]),
            )
        )
        for key in ("altas", "modificaciones", "bajas"):
            rows.append((key.capitalize(), str(local[key]), str(corporate[key])))
        rows.append(
            (
                "Escrituras (mod/alta/baja)",
                "%d/%d/%d" % local["escrituras"],
                "%d/%d/%d" % corporate["escrituras"],
            )
        )
        rows.append(
            (
                "Errores",
                str(len(local["errores"])),
                str(len(corporate["errores"])),
            )
        )
        print()
        for row in rows:
            print("  %-28s %-20s %s" % row)

        same = (
            local["entidades"] == corporate["entidades"]
            and local["escrituras"] == corporate["escrituras"]
            and not local["errores"]
            and not corporate["errores"]
        )
        print()
        if same:
            print("  Los dos origenes se comportan igual y el ciclo cierra.")
        else:
            print("  ATENCION: los dos origenes NO se comportaron igual.")
        print()
        if temporary:
            print("  Paquetes generados en %s" % base)
        return 0 if same else 1
    finally:
        if temporary and os.environ.get("QFIELDESRI_CONSERVAR") != "1":
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
