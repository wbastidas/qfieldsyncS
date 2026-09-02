# -*- coding: utf-8 -*-
"""Aplicacion de escritorio de qfieldESRI.

Es un **programa externo**: se abre con doble clic, tiene su propia ventana y
no vive dentro de ningun otro software. Trabaja *contra* ArcGIS —usa ``arcpy``
para leer y escribir la geodatabase— pero no se instala en ArcGIS ni depende de
su interfaz.

La ventana esta hecha con **Tkinter**, que viene incluido en el Python que
instala ArcGIS (tanto el 2.7 de ArcMap como el 3.x de ArcGIS Pro). Esa es toda
la razon de la eleccion: cero instalaciones, cero permisos de administrador,
cero dependencias graficas.

Tres pestanas, que son los tres momentos del trabajo:

1. **Geodatabase** — abrirla y ver que tiene, con la verificacion previa.
2. **Exportar a QField** — elegir el ambito (alimentador, subestacion,
   poligono de sector, provincia, canton o parroquia) y generar el paquete.
3. **Traer de campo** — comparar lo que vuelve y aplicarlo a la geodatabase.

El trabajo pesado corre en un hilo aparte para que la ventana no se congele, y
el progreso llega a la interfaz por una cola, que es la unica forma segura de
tocar Tkinter desde otro hilo.
"""

import os
import sys
import threading
import traceback

try:  # Python 3 (ArcGIS Pro)
    import queue
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # pragma: no cover - Python 2.7 (ArcMap)
    import Queue as queue  # noqa: N813
    import tkFileDialog as filedialog  # noqa: N813
    import Tkinter as tk  # noqa: N813
    import tkMessageBox as messagebox  # noqa: N813
    import ttk

from .core.checker import WorkspaceChecker
from .core.config import PackagingConfig
from .core.packager import Packager, build_stylesheet, load_manifest
from .core.scope import Scope, ScopeKind, ScopeResolver
from .core.synchronizer import ConflictPolicy, Synchronizer
from .profiles import available_profiles, load_profile
from .readers import get_reader
from .symbology import StyleSheet, SymbologyResolver, load_symbology
from .version import __version__

APP_TITLE = "qfieldESRI %s" % __version__

#: Opcion "sin acotar" del desplegable de ambito.
SCOPE_ALL = "Toda la geodatabase"

#: Separador entre el codigo y su descripcion en las listas de valores.
CODE_SEPARATOR = " - "

#: De donde sacar la simbologia. Falta a proposito "el mapa abierto": este es
#: un programa aparte de ArcGIS y no tiene acceso al documento en pantalla; lo
#: que se exporta desde ArcGIS (una carpeta de .lyrx, un .lyr, un MXD) si.
SYMBOLOGY_AUTO = "Automatica (la decide qfieldESRI)"
SYMBOLOGY_FOLDER = "Carpeta de archivos de capa (.lyrx)"
SYMBOLOGY_DOCUMENT = "Documento de ArcGIS (.lyrx, .lyr, .mxd, .aprx)"
SYMBOLOGY_MODES = (SYMBOLOGY_AUTO, SYMBOLOGY_FOLDER, SYMBOLOGY_DOCUMENT)


def scope_label(kind):
    return ScopeKind.LABELS.get(kind, kind)


def scope_kind_from_label(label):
    if not label or label == SCOPE_ALL:
        return None
    for kind, text in ScopeKind.LABELS.items():
        if text == label:
            return kind
    return label


def scope_code(item):
    return item.split(CODE_SEPARATOR)[0].strip()


def format_value(code, label):
    """Como se muestra un valor del ambito en la lista."""
    return "%s%s%s" % (code, CODE_SEPARATOR, label) if label else str(code)


def build_scope(
    kind_label,
    selected=None,
    polygon_layer=None,
    polygon_where=None,
    follow_relationships=True,
):
    """Construye el ambito a partir de lo elegido en la ventana.

    Esta separado de los widgets a proposito: es la unica logica de la
    aplicacion que merece prueba automatica, y asi se prueba sin abrir una
    ventana.
    """
    kind = scope_kind_from_label(kind_label)
    if not kind:
        return Scope()
    if kind == ScopeKind.POLIGONO:
        if not polygon_layer:
            raise ValueError("Elija la clase de poligonos que delimita el sector.")
        return Scope(
            kind,
            polygon_layer=polygon_layer,
            polygon_where=polygon_where or None,
            follow_relationships=follow_relationships,
        )
    codes = [scope_code(item) for item in (selected or [])]
    if not codes:
        raise ValueError(
            "Elija al menos un valor, o cambie el ambito a '%s'." % SCOPE_ALL
        )
    return Scope(kind, values=codes, follow_relationships=follow_relationships)


# ----------------------------------------------------------------------
# trabajo en segundo plano
# ----------------------------------------------------------------------
class BackgroundTask(object):
    """Una tarea larga corriendo fuera del hilo de la interfaz.

    Tkinter no admite que otro hilo toque los widgets, asi que el hilo de
    trabajo solo deja mensajes en una cola y la ventana la vacia cada 100 ms.
    """

    def __init__(self, widget, target, on_done=None):
        self.widget = widget
        self.target = target
        self.on_done = on_done
        self.queue = queue.Queue()
        self.thread = None
        self.result = None
        self.error = None

    def progress(self, message, percent=None):
        self.queue.put(("progreso", (message, percent)))

    def start(self):
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()
        self.widget.after(100, self._drain)

    def _run(self):
        try:
            self.result = self.target(self.progress)
        except Exception as error:
            self.error = error
            self.queue.put(("traza", traceback.format_exc()))
        finally:
            self.queue.put(("fin", None))

    def _drain(self):
        finished = False
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "fin":
                finished = True
            elif self.on_message:
                self.on_message(kind, payload)
        if finished:
            if self.on_done:
                self.on_done(self.result, self.error)
        else:
            self.widget.after(100, self._drain)

    #: la ventana lo sustituye por su propio manejador
    on_message = None


# ----------------------------------------------------------------------
class Application(tk.Frame):
    """Ventana principal."""

    def __init__(self, master=None):
        tk.Frame.__init__(self, master)
        self.master.title(APP_TITLE)
        self.master.geometry("980x680")
        self.master.minsize(820, 560)

        self.workspace_info = None
        self.reader = None
        self.scope_values = []
        self.busy = False

        self.pack(fill="both", expand=True)
        self._build()
        self._log(
            "qfieldESRI %s. Empiece abriendo una geodatabase en la primera "
            "pestana." % __version__
        )
        if not self._arcpy_available():
            self._log(
                "AVISO: arcpy no esta disponible en este Python. Abra la "
                "aplicacion con el Python de ArcGIS (el lanzador lo hace solo) "
                "o use el motor 'ogr' para una lectura limitada."
            )

    # ------------------------------------------------------------------
    # construccion de la interfaz
    # ------------------------------------------------------------------
    def _build(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.tab_gdb = ttk.Frame(self.notebook)
        self.tab_export = ttk.Frame(self.notebook)
        self.tab_sync = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_gdb, text=" 1 · Geodatabase ")
        self.notebook.add(self.tab_export, text=" 2 · Exportar a QField ")
        self.notebook.add(self.tab_sync, text=" 3 · Traer de campo ")

        self._build_gdb_tab()
        self._build_export_tab()
        self._build_sync_tab()
        self._build_console()

    def _build_console(self):
        frame = ttk.LabelFrame(self, text="Actividad")
        frame.pack(fill="both", expand=False, padx=8, pady=(0, 4))

        self.console = tk.Text(frame, height=10, wrap="word")
        scrollbar = ttk.Scrollbar(frame, command=self.console.yview)
        self.console.configure(yscrollcommand=scrollbar.set, state="disabled")
        self.console.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        status = ttk.Frame(self)
        status.pack(fill="x", padx=8, pady=(0, 8))
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.status = ttk.Label(status, text="Listo", width=32, anchor="e")
        self.status.pack(side="right", padx=(8, 0))

    # -- pestana 1 ------------------------------------------------------
    def _build_gdb_tab(self):
        frame = self.tab_gdb

        top = ttk.LabelFrame(frame, text="Origen")
        top.pack(fill="x", padx=8, pady=8)

        ttk.Label(top, text="Geodatabase (.gdb) o conexion (.sde):").grid(
            row=0, column=0, sticky="w", padx=6, pady=(6, 2)
        )
        self.var_workspace = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.var_workspace, width=80)
        entry.grid(row=1, column=0, sticky="we", padx=6)
        ttk.Button(top, text="Carpeta .gdb...", command=self._pick_gdb).grid(
            row=1, column=1, padx=4
        )
        ttk.Button(top, text="Archivo .sde...", command=self._pick_sde).grid(
            row=1, column=2, padx=(0, 6)
        )

        ttk.Label(top, text="Perfil de modelo de datos:").grid(
            row=2, column=0, sticky="w", padx=6, pady=(8, 2)
        )
        self.var_profile = tk.StringVar(value="cnel_ep")
        ttk.Combobox(
            top,
            textvariable=self.var_profile,
            values=available_profiles(),
            state="readonly",
            width=24,
        ).grid(row=3, column=0, sticky="w", padx=6, pady=(0, 8))

        ttk.Button(top, text="Abrir y analizar", command=self.on_open_workspace).grid(
            row=3, column=1, columnspan=2, sticky="e", padx=6, pady=(0, 8)
        )
        top.columnconfigure(0, weight=1)

        listing = ttk.LabelFrame(frame, text="Clases y tablas de la geodatabase")
        listing.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = ("geometria", "campos", "subtipos", "clave")
        self.tree = ttk.Treeview(listing, columns=columns, show="tree headings")
        self.tree.heading("#0", text="Clase")
        self.tree.column("#0", width=320)
        for column, title, width in (
            ("geometria", "Geometria", 110),
            ("campos", "Campos", 70),
            ("subtipos", "Subtipos", 80),
            ("clave", "Clave de sincronizacion", 190),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="center")
        scrollbar = ttk.Scrollbar(listing, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    # -- pestana 2 ------------------------------------------------------
    def _build_export_tab(self):
        frame = self.tab_export

        scope = ttk.LabelFrame(frame, text="Que se lleva a campo")
        scope.pack(fill="x", padx=8, pady=8)

        ttk.Label(scope, text="Acotar por:").grid(
            row=0, column=0, sticky="w", padx=6, pady=6
        )
        self.var_scope_kind = tk.StringVar(value=SCOPE_ALL)
        self.combo_scope = ttk.Combobox(
            scope,
            textvariable=self.var_scope_kind,
            values=[SCOPE_ALL] + [scope_label(kind) for kind in ScopeKind.ALL],
            state="readonly",
            width=24,
        )
        self.combo_scope.grid(row=0, column=1, sticky="w", pady=6)
        self.combo_scope.bind("<<ComboboxSelected>>", self.on_scope_changed)

        self.var_only_present = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            scope,
            text="Ofrecer solo los valores presentes en los datos",
            variable=self.var_only_present,
            command=self.on_scope_changed,
        ).grid(row=0, column=2, sticky="w", padx=12)

        self.var_follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            scope,
            text="Arrastrar las tablas Unidad de los Puestos exportados",
            variable=self.var_follow,
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 4))

        self.frame_values = ttk.Frame(scope)
        self.frame_values.grid(
            row=2, column=0, columnspan=3, sticky="nsew", padx=6, pady=(4, 8)
        )
        self.list_values = tk.Listbox(
            self.frame_values, selectmode="extended", height=7, exportselection=False
        )
        values_scroll = ttk.Scrollbar(self.frame_values, command=self.list_values.yview)
        self.list_values.configure(yscrollcommand=values_scroll.set)
        self.list_values.pack(side="left", fill="both", expand=True)
        values_scroll.pack(side="right", fill="y")

        self.frame_polygon = ttk.Frame(scope)
        ttk.Label(self.frame_polygon, text="Clase de poligonos del sector:").pack(
            side="left", padx=(6, 4)
        )
        self.var_polygon_layer = tk.StringVar()
        self.combo_polygon = ttk.Combobox(
            self.frame_polygon, textvariable=self.var_polygon_layer, width=36
        )
        self.combo_polygon.pack(side="left")
        ttk.Label(self.frame_polygon, text="  WHERE:").pack(side="left")
        self.var_polygon_where = tk.StringVar()
        ttk.Entry(
            self.frame_polygon, textvariable=self.var_polygon_where, width=32
        ).pack(side="left", padx=(2, 6))

        scope.columnconfigure(2, weight=1)
        scope.rowconfigure(2, weight=1)

        self._build_symbology_panel(frame)

        destination = ttk.LabelFrame(frame, text="Destino")
        destination.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(destination, text="Carpeta de salida:").grid(
            row=0, column=0, sticky="w", padx=6, pady=(6, 2)
        )
        self.var_output = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "QField")
        )
        ttk.Entry(destination, textvariable=self.var_output, width=64).grid(
            row=1, column=0, sticky="we", padx=6
        )
        ttk.Button(destination, text="Elegir...", command=self._pick_output).grid(
            row=1, column=1, padx=(0, 6)
        )

        ttk.Label(destination, text="Nombre del proyecto:").grid(
            row=2, column=0, sticky="w", padx=6, pady=(8, 2)
        )
        self.var_project = tk.StringVar(value="qfield_proyecto")
        ttk.Entry(destination, textvariable=self.var_project, width=40).grid(
            row=3, column=0, sticky="w", padx=6, pady=(0, 8)
        )
        destination.columnconfigure(0, weight=1)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", padx=8, pady=(0, 8))
        self.button_export = ttk.Button(
            actions, text="Exportar a QField", command=self.on_export
        )
        self.button_export.pack(side="right")
        ttk.Button(
            actions, text="Ver que se exportaria", command=self.on_preview_scope
        ).pack(side="right", padx=6)

    def _build_symbology_panel(self, frame):
        """Panel de simbologia: de donde sale y con que se retoca."""
        symbology = ttk.LabelFrame(frame, text="Como se vera en el dispositivo")
        symbology.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(symbology, text="Simbologia:").grid(
            row=0, column=0, sticky="w", padx=6, pady=6
        )
        self.var_symbology_mode = tk.StringVar(value=SYMBOLOGY_AUTO)
        self.combo_symbology = ttk.Combobox(
            symbology,
            textvariable=self.var_symbology_mode,
            values=list(SYMBOLOGY_MODES),
            state="readonly",
            width=38,
        )
        self.combo_symbology.grid(row=0, column=1, sticky="w", pady=6)
        self.combo_symbology.bind("<<ComboboxSelected>>", self.on_symbology_changed)

        self.var_symbology_path = tk.StringVar()
        self.entry_symbology = ttk.Entry(
            symbology, textvariable=self.var_symbology_path, width=48
        )
        self.entry_symbology.grid(row=0, column=2, sticky="we", padx=(8, 2))
        self.button_symbology = ttk.Button(
            symbology, text="Elegir...", command=self._pick_symbology
        )
        self.button_symbology.grid(row=0, column=3, padx=(0, 6))

        ttk.Label(symbology, text="Archivo de estilo:").grid(
            row=1, column=0, sticky="w", padx=6, pady=(0, 8)
        )
        self.var_style_file = tk.StringVar()
        ttk.Entry(symbology, textvariable=self.var_style_file, width=48).grid(
            row=1, column=1, columnspan=2, sticky="we", padx=(0, 2), pady=(0, 8)
        )
        ttk.Button(symbology, text="Elegir...", command=self._pick_style_file).grid(
            row=1, column=3, padx=(0, 6), pady=(0, 8)
        )
        ttk.Button(
            symbology,
            text="Generar archivo de estilo...",
            command=self.on_write_style,
        ).grid(row=2, column=1, columnspan=2, sticky="w", padx=(0, 6), pady=(0, 8))
        ttk.Label(
            symbology,
            text=(
                "El archivo de estilo manda sobre lo importado de ArcGIS. "
                "Generelo, editelo y vuelva a cargarlo aqui."
            ),
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6))
        symbology.columnconfigure(2, weight=1)
        self.on_symbology_changed()

    # -- pestana 3 ------------------------------------------------------
    def _build_sync_tab(self):
        frame = self.tab_sync

        top = ttk.LabelFrame(frame, text="Carpeta devuelta por el dispositivo")
        top.pack(fill="x", padx=8, pady=8)

        self.var_package = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_package, width=80).grid(
            row=0, column=0, sticky="we", padx=6, pady=8
        )
        ttk.Button(top, text="Elegir...", command=self._pick_package).grid(
            row=0, column=1, padx=(0, 6)
        )
        top.columnconfigure(0, weight=1)

        options = ttk.LabelFrame(frame, text="Como aplicar los cambios")
        options.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(options, text="Ante un conflicto:").grid(
            row=0, column=0, sticky="w", padx=6, pady=6
        )
        self.var_conflicts = tk.StringVar(value=ConflictPolicy.REPORT)
        ttk.Combobox(
            options,
            textvariable=self.var_conflicts,
            values=list(ConflictPolicy.ALL),
            state="readonly",
            width=18,
        ).grid(row=0, column=1, sticky="w", pady=6)

        self.var_deletes = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="Aplicar tambien las bajas hechas en campo",
            variable=self.var_deletes,
        ).grid(row=0, column=2, sticky="w", padx=16)

        actions = ttk.Frame(frame)
        actions.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(
            actions, text="Comparar (no escribe nada)", command=self.on_detect
        ).pack(side="right", padx=6)
        self.button_apply = ttk.Button(
            actions, text="Aplicar a la geodatabase", command=self.on_apply
        )
        self.button_apply.pack(side="right")

        result = ttk.LabelFrame(frame, text="Diferencias detectadas")
        result.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        columns = ("tipo", "clase", "clave", "estado")
        self.tree_changes = ttk.Treeview(result, columns=columns, show="headings")
        for column, title, width in (
            ("tipo", "Cambio", 110),
            ("clase", "Clase", 240),
            ("clave", "Registro", 300),
            ("estado", "Estado", 220),
        ):
            self.tree_changes.heading(column, text=title)
            self.tree_changes.column(column, width=width)
        scrollbar = ttk.Scrollbar(result, command=self.tree_changes.yview)
        self.tree_changes.configure(yscrollcommand=scrollbar.set)
        self.tree_changes.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    # ------------------------------------------------------------------
    # utilidades de la interfaz
    # ------------------------------------------------------------------
    def _log(self, text):
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _set_status(self, text, percent=None):
        self.status.configure(text=text[:40])
        if percent is not None:
            self.progress.configure(value=percent)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.button_export, self.button_apply):
            button.configure(state=state)
        self.master.configure(cursor="watch" if busy else "")

    def _arcpy_available(self):
        try:
            import arcpy  # noqa: F401
        except ImportError:
            return False
        return True

    def _pick_gdb(self):
        path = filedialog.askdirectory(title="Elija la File Geodatabase (.gdb)")
        if path:
            self.var_workspace.set(path)

    def _pick_sde(self):
        path = filedialog.askopenfilename(
            title="Elija el archivo de conexion",
            filetypes=[("Conexion de geodatabase", "*.sde"), ("Todos", "*.*")],
        )
        if path:
            self.var_workspace.set(path)

    def _pick_output(self):
        path = filedialog.askdirectory(title="Carpeta donde dejar el paquete")
        if path:
            self.var_output.set(path)

    def _pick_symbology(self):
        mode = self.var_symbology_mode.get()
        if mode == SYMBOLOGY_FOLDER:
            path = filedialog.askdirectory(
                title="Carpeta con los archivos de capa (.lyrx)"
            )
        else:
            path = filedialog.askopenfilename(
                title="Documento de ArcGIS del que leer la simbologia",
                filetypes=[
                    ("Archivo de capa de ArcGIS Pro", "*.lyrx"),
                    ("Archivo de capa de ArcMap", "*.lyr"),
                    ("Documento de ArcMap", "*.mxd"),
                    ("Proyecto de ArcGIS Pro", "*.aprx"),
                    ("Todos", "*.*"),
                ],
            )
        if path:
            self.var_symbology_path.set(path)

    def _pick_style_file(self):
        path = filedialog.askopenfilename(
            title="Archivo de estilo de qfieldESRI",
            filetypes=[("Estilo de qfieldESRI", "*.json"), ("Todos", "*.*")],
        )
        if path:
            self.var_style_file.set(path)

    def on_symbology_changed(self, event=None):
        """Solo pide ruta cuando el modo elegido la necesita."""
        needs_path = self.var_symbology_mode.get() != SYMBOLOGY_AUTO
        state = "normal" if needs_path else "disabled"
        self.entry_symbology.configure(state=state)
        self.button_symbology.configure(state=state)

    def _symbology_source(self):
        if self.var_symbology_mode.get() == SYMBOLOGY_AUTO:
            return ""
        return self.var_symbology_path.get().strip()

    def _pick_package(self):
        path = filedialog.askdirectory(title="Carpeta del proyecto de QField")
        if path:
            self.var_package.set(path)

    def _run_task(self, target, on_done):
        """Lanza una tarea larga y conecta su progreso con la ventana."""
        if self.busy:
            messagebox.showinfo(APP_TITLE, "Ya hay una operacion en curso.")
            return None
        self._set_busy(True)
        self._set_status("Trabajando...", 0)

        task = BackgroundTask(self, target)

        def on_message(kind, payload):
            if kind == "progreso":
                message, percent = payload
                self._log(message)
                self._set_status(message, percent)
            else:
                self._log(payload)

        def done(result, error):
            self._set_busy(False)
            self._set_status("Listo", 100 if error is None else 0)
            if error is not None:
                self._log("ERROR: %s" % error)
                messagebox.showerror(APP_TITLE, str(error))
                return
            on_done(result)

        task.on_message = on_message
        task.on_done = done
        task.start()
        return task

    def _open_reader(self):
        workspace = self.var_workspace.get().strip()
        if not workspace:
            raise ValueError("Indique la geodatabase de origen.")
        reader = get_reader(workspace)
        reader.open()
        return reader

    # ------------------------------------------------------------------
    # pestana 1: abrir y analizar
    # ------------------------------------------------------------------
    def on_open_workspace(self):
        def work(progress):
            progress("Abriendo %s" % self.var_workspace.get(), 5)
            reader = self._open_reader()
            try:
                progress("Leyendo el esquema...", 20)
                info = reader.describe_workspace()
                config = PackagingConfig(
                    workspace=info.path,
                    output_dir=self.var_output.get(),
                    profile=self.var_profile.get(),
                )
                progress("Verificando...", 80)
                feedback = WorkspaceChecker(info, config).check()
            finally:
                reader.close()
            return info, feedback

        def done(result):
            info, feedback = result
            self.workspace_info = info
            self._fill_tree(info)
            self._fill_polygon_layers(info)
            self._log("")
            self._log(
                "Geodatabase: %s (%s) - %d clases, %d dominios, %d relaciones"
                % (
                    info.path,
                    info.workspace_type,
                    len(info.layers),
                    len(info.domains),
                    len(info.relationships),
                )
            )
            for item in feedback.feedbacks:
                self._log(item.format())
            if feedback.has_errors:
                messagebox.showwarning(
                    APP_TITLE,
                    "La verificacion encontro errores. Revise la actividad "
                    "antes de exportar.",
                )
            self.on_scope_changed()
            self.notebook.select(self.tab_export)

        self._run_task(work, done)

    def _fill_tree(self, info):
        self.tree.delete(*self.tree.get_children())
        profile = load_profile(self.var_profile.get())
        groups = {}
        for layer in sorted(info.layers, key=lambda item: item.name.lower()):
            group_name = profile.group_of(layer.name) or "Otras clases"
            if group_name not in groups:
                groups[group_name] = self.tree.insert(
                    "", "end", text=group_name, open=True
                )
            self.tree.insert(
                groups[group_name],
                "end",
                text=layer.name,
                values=(
                    layer.geometry_type or "tabla",
                    len(layer.fields),
                    len(layer.subtypes) or "",
                    layer.globalid_field or layer.oid_field,
                ),
            )

    def _fill_polygon_layers(self, info):
        names = [
            layer.name for layer in info.layers if layer.geometry_type == "Polygon"
        ]
        self.combo_polygon["values"] = sorted(names)

    # ------------------------------------------------------------------
    # pestana 2: ambito y exportacion
    # ------------------------------------------------------------------
    def on_scope_changed(self, event=None):
        kind = scope_kind_from_label(self.var_scope_kind.get())
        self.frame_polygon.grid_forget()
        self.frame_values.grid_forget()

        if kind == ScopeKind.POLIGONO:
            self.frame_polygon.grid(
                row=2, column=0, columnspan=3, sticky="we", padx=6, pady=(4, 8)
            )
            return

        self.frame_values.grid(
            row=2, column=0, columnspan=3, sticky="nsew", padx=6, pady=(4, 8)
        )
        self.list_values.delete(0, "end")
        if not kind or self.workspace_info is None:
            return

        def work(progress):
            progress("Leyendo los valores de %s..." % scope_label(kind))
            reader = self._open_reader()
            try:
                resolver = ScopeResolver(
                    self.workspace_info, load_profile(self.var_profile.get()), reader
                )
                present_in = None
                if self.var_only_present.get():
                    present_in = self._best_layer_for(kind)
                return resolver.available_values(kind, only_present_in=present_in)
            finally:
                reader.close()

        def done(values):
            self.scope_values = values
            self.list_values.delete(0, "end")
            for code, label in values:
                self.list_values.insert("end", format_value(code, label))
            self._log("%s: %d valores disponibles." % (scope_label(kind), len(values)))
            if not values:
                self._log(
                    "  No hay valores. Puede que la geodatabase no tenga el "
                    "dominio correspondiente, o que ninguna clase use ese campo."
                )

        self._run_task(work, done)

    def _best_layer_for(self, kind):
        """Clase de la que sacar los valores realmente presentes.

        Se prefiere una clase de red con el campo del ambito y con datos; leer
        los valores distintos de una clase pequena es rapido y evita ofrecer
        246 alimentadores cuando la geodatabase solo tiene doce.
        """
        if self.workspace_info is None:
            return None
        profile = load_profile(self.var_profile.get())
        resolver = ScopeResolver(self.workspace_info, profile)
        candidates = [
            layer
            for layer in self.workspace_info.layers
            if resolver.field_for(layer, kind)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda layer: (not layer.is_spatial, layer.name.lower()))
        return candidates[0].name

    def _build_scope(self):
        return build_scope(
            self.var_scope_kind.get(),
            selected=[
                self.list_values.get(index) for index in self.list_values.curselection()
            ],
            polygon_layer=self.var_polygon_layer.get().strip(),
            polygon_where=self.var_polygon_where.get().strip(),
            follow_relationships=self.var_follow.get(),
        )

    def _build_config(self):
        return PackagingConfig(
            workspace=self.var_workspace.get().strip(),
            output_dir=self.var_output.get().strip(),
            project_name=self.var_project.get().strip() or "qfield_proyecto",
            profile=self.var_profile.get(),
            scope=self._build_scope(),
            symbology_source=self._symbology_source(),
            style_file=self.var_style_file.get().strip(),
        )

    def on_preview_scope(self):
        """Explica que se llevaria, sin generar nada."""
        try:
            config = self._build_config()
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return

        def work(progress):
            progress("Resolviendo el ambito...", 20)
            reader = self._open_reader()
            try:
                info = reader.describe_workspace()
                resolver = ScopeResolver(info, load_profile(config.profile), reader)
                return resolver.resolve(config.scope, info.layers)
            finally:
                reader.close()

        def done(plan):
            self._log("")
            self._log(plan.describe())

        self._run_task(work, done)

    def on_write_style(self):
        """Escribe el estilo que se aplicaria ahora, para editarlo a mano.

        Es la respuesta practica a que ArcGIS guarde la simbologia en el MXD:
        se resuelve una vez, se deja en un archivo legible y a partir de ahi la
        decision es del usuario, no del programa.
        """
        workspace_path = self.var_workspace.get().strip()
        if not workspace_path:
            messagebox.showwarning(APP_TITLE, "Elija primero la geodatabase.")
            return
        destination = filedialog.asksaveasfilename(
            title="Guardar el archivo de estilo",
            defaultextension=".json",
            initialfile="qfieldesri_estilo.json",
            filetypes=[("Estilo de qfieldESRI", "*.json"), ("Todos", "*.*")],
        )
        if not destination:
            return

        source = self._symbology_source()
        base_path = self.var_style_file.get().strip()
        profile_name = self.var_profile.get()

        def work(progress):
            imported = {}
            warnings = []
            if source:
                progress("Leyendo la simbologia de ArcGIS...", 15)
                imported, warnings = load_symbology(source)

            progress("Leyendo el esquema de la geodatabase...", 40)
            reader = self._open_reader()
            try:
                info = reader.describe_workspace()
                resolver = SymbologyResolver(
                    profile=load_profile(profile_name),
                    stylesheet=StyleSheet.load(base_path) if base_path else None,
                    imported=imported,
                )
                progress("Resolviendo la simbologia de cada clase...", 70)
                sheet = build_stylesheet(
                    info,
                    resolver,
                    description=(
                        "Estilos de qfieldESRI para %s. Edite colores, formas, "
                        "etiquetas y escalas, y cargue este archivo en "
                        "'Archivo de estilo'." % info.path
                    ),
                )
            finally:
                reader.close()
            sheet.save(destination)
            return sheet, resolver, warnings

        def done(payload):
            sheet, resolver, warnings = payload
            self._log("")
            for warning in warnings + resolver.warnings:
                self._log("AVISO: %s" % warning)
            for name in sorted(sheet.layers):
                self._log("  %-40s %s" % (name, resolver.sources.get(name, "")))
            self._log("")
            self._log(resolver.summary())
            self.var_style_file.set(destination)
            messagebox.showinfo(
                APP_TITLE,
                "Estilo de %d capas escrito en:\n%s\n\nEditelo y exporte: "
                "queda cargado como archivo de estilo." % (len(sheet), destination),
            )

        self._run_task(work, done)

    def on_export(self):
        try:
            config = self._build_config()
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return

        def work(progress):
            reader = self._open_reader()
            try:
                return Packager(reader, config, progress=progress).run()
            finally:
                reader.close()

        def done(result):
            self._log("")
            if result.scope_description:
                self._log(result.scope_description)
            for name in sorted(result.layer_counts):
                self._log("  %-40s %8d entidades" % (name, result.layer_counts[name]))
            self._log("  %-40s %8d entidades" % ("TOTAL", result.total_features))
            if result.symbology_description:
                self._log("")
                self._log(result.symbology_description)
            for warning in result.warnings:
                self._log("AVISO: %s" % warning)
            if result.total_features == 0:
                messagebox.showwarning(
                    APP_TITLE,
                    "El paquete salio vacio. Revise el ambito elegido.",
                )
                return
            self.var_package.set(result.project_dir)
            messagebox.showinfo(
                APP_TITLE,
                "Paquete generado en:\n%s\n\n%d entidades en %d capas.\n\n"
                "Copie la carpeta completa al dispositivo y abra el proyecto "
                "desde QField."
                % (
                    result.project_dir,
                    result.total_features,
                    len(result.layer_counts),
                ),
            )

        self._run_task(work, done)

    # ------------------------------------------------------------------
    # pestana 3: traer de campo
    # ------------------------------------------------------------------
    def _synchronizer(self, reader, progress=None):
        return Synchronizer(
            self.var_package.get().strip(),
            reader,
            conflict_policy=self.var_conflicts.get(),
            apply_deletes=self.var_deletes.get(),
            progress=progress,
        )

    def _sync_workspace(self):
        package = self.var_package.get().strip()
        if not package:
            raise ValueError("Elija la carpeta devuelta por el dispositivo.")
        manifest = load_manifest(package)
        workspace = self.var_workspace.get().strip() or manifest.get("workspace")
        if not workspace:
            raise ValueError(
                "No se sabe a que geodatabase volver: abrala en la primera pestana."
            )
        return workspace

    def on_detect(self):
        self._sync(apply_changes=False)

    def on_apply(self):
        if not messagebox.askyesno(
            APP_TITLE,
            "Se van a escribir los cambios en la geodatabase.\n\n"
            "Los conflictos y (salvo que lo haya marcado) las bajas no se "
            "aplican.\n\n Continuar?",
        ):
            return
        self._sync(apply_changes=True)

    def _sync(self, apply_changes):
        try:
            workspace = self._sync_workspace()
        except Exception as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return

        def work(progress):
            reader = get_reader(workspace)
            reader.open()
            try:
                synchronizer = self._synchronizer(reader, progress)
                report = synchronizer.detect()
                if apply_changes:
                    report = synchronizer.apply(report)
                return report
            finally:
                reader.close()

        def done(report):
            self._fill_changes(report)
            self._log("")
            self._log(report.format())
            summary = report.summary()
            if apply_changes:
                messagebox.showinfo(
                    APP_TITLE,
                    "Aplicados %d cambios.\nConflictos sin resolver: %d.\n"
                    "Errores: %d."
                    % (
                        summary["aplicados"],
                        summary["conflictos"],
                        summary["errores"],
                    ),
                )
            else:
                messagebox.showinfo(
                    APP_TITLE,
                    "Comparacion terminada (no se escribio nada).\n\n"
                    "Altas: %d\nModificaciones: %d\nBajas: %d\nConflictos: %d"
                    % (
                        summary["altas"],
                        summary["modificaciones"],
                        summary["bajas"],
                        summary["conflictos"],
                    ),
                )

        self._run_task(work, done)

    def _fill_changes(self, report):
        self.tree_changes.delete(*self.tree_changes.get_children())
        for change in report.changes:
            if change.conflict:
                state = "CONFLICTO: %s" % change.message
            elif change.applied:
                state = "aplicado"
            else:
                state = change.message or "pendiente"
            self.tree_changes.insert(
                "",
                "end",
                values=(change.kind, change.layer, change.key_value or "", state),
            )


def main(argv=None):
    """Abre la ventana. Devuelve el codigo de salida del proceso."""
    argv = list(sys.argv[1:] if argv is None else argv)
    root = tk.Tk()
    application = Application(master=root)
    if argv:
        # Comodidad: ``QFieldESRI.py C:/datos/GYE.gdb`` abre esa geodatabase.
        application.var_workspace.set(argv[0])
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
