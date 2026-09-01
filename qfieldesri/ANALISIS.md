# Análisis y decisiones de diseño de qfieldESRI

Este documento explica **por qué qfieldESRI está construido como está**: qué
problema resuelve, por qué es un programa externo y no un complemento, qué
había que escribir desde cero y qué se decidió dejar fuera.

---

## 1. El problema

Hay una geodatabase de ESRI con el modelo eléctrico homologado de CNEL EP, se
trabaja con **ArcGIS Desktop**, y hace falta que las brigadas salgan a campo con
**QField** y que lo que capturen vuelva a la geodatabase.

Las dos orillas no se hablan:

| | Origen | Destino |
|---|---|---|
| **Qué es** | File Geodatabase o geodatabase corporativa (SDE) | QField, aplicación móvil |
| **Qué entiende** | `arcpy`, dominios, subtipos, relationship classes, red geométrica | un archivo de proyecto y un GeoPackage |
| **Dónde vive** | Windows, con ArcGIS instalado | Android / iOS / escritorio |

## 2. Restricción de partida: nada de QGIS

QField guarda su proyecto en un XML con extensión `.qgs`. Es tentador concluir
que hace falta QGIS; **no es así**. `.qgs` es un formato de archivo, y un
formato de archivo se escribe con un escritor de XML.

qfieldESRI escribe ese archivo con `xml.etree` de la biblioteca estándar. No
importa QGIS, no lo instala y no lo necesita en ningún equipo. Lo mismo con la
interfaz: nada de Qt ni PyQt; la ventana está hecha con **Tkinter**, que ya
viene en el Python que instala ArcGIS.

Esto no queda en una promesa del README. `tests/test_dependencias.py` recorre
todos los archivos del proyecto en cada ejecución de la batería y falla si
aparece una sola importación de `qgis`, `PyQt`, `PySide`, `libqfieldsync` o
cualquier dependencia externa no declarada. Las dos únicas externas admitidas
son:

| Dependencia | Dónde puede aparecer | Por qué |
|---|---|---|
| `arcpy` | su lector, el lanzador, la caja de herramientas, y de forma perezosa en la aplicación y en los adjuntos | es la única forma de hablar con la geodatabase |
| `osgeo` | solo su lector | respaldo opcional para leer sin ArcGIS |

La misma prueba comprueba lo contrario de lo obvio: que **todo el núcleo se
importe sin arcpy instalado**. Eso es lo que permite probar y automatizar el
programa fuera de ArcGIS, y es la razón de que las 213 pruebas corran en
cualquier Python.

## 3. Programa externo, no complemento

Se descartó empotrarlo en ArcGIS como única vía:

- un complemento de ArcMap obliga a registrar componentes y a pelearse con
  permisos de administrador en cada equipo;
- el usuario tendría que abrir ArcGIS —lento y con licencia ocupada— solo para
  exportar un alimentador;
- y ataría el ciclo de trabajo a una versión concreta de ArcGIS.

qfieldESRI es una **carpeta que se copia y se abre con doble clic**. El
lanzador (`qfieldesri/launcher.py`) resuelve el único detalle incómodo: `arcpy`
solo funciona con el intérprete que instala ArcGIS, y ese intérprete no está en
el `PATH`. Lo busca en la variable `QFIELDESRI_PYTHON`, en el intérprete actual,
en el registro de Windows (ArcGIS Pro y ArcMap 10.5–10.8) y en las rutas
habituales; si no lo encuentra, explica exactamente qué definir en vez de fallar
con un `ImportError` a media ejecución.

Aun así se ofrecen las tres puertas de entrada, porque cada una tiene su
momento: la **aplicación** para el uso normal, la **caja de herramientas** para
quien ya está dentro de ArcGIS o quiere usarlo en ModelBuilder, y la **línea de
comandos** para automatizar.

## 4. Arquitectura

```
                    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
                    │ QFieldESRI.py│  │QFieldESRI.pyt│  │ python -m ...│
                    │  (ventana)   │  │  (ArcGIS)    │  │   (consola)  │
                    └───────┬──────┘  └──────┬───────┘  └──────┬───────┘
                            └────────────────┼─────────────────┘
                                             ▼
   readers/          ┌───────────────── core/ ──────────────────┐      writers/
 ┌────────────┐      │ scope     qué se lleva a campo           │  ┌──────────────┐
 │ arcpy      │─────►│ checker   verificación previa            │─►│ geopackage   │
 │ ogr        │      │ packager  geodatabase -> paquete         │  │ qfield_project│
 │ memoria    │◄─────│ synchron. paquete -> geodatabase         │  └──────────────┘
 └────────────┘      │ cloudapi  QFieldCloud (urllib)           │
                     └──────────────────────────────────────────┘
                                     profiles/  utils/
```

Los tres frentes comparten el mismo motor. Los lectores están detrás de una
interfaz (`readers/base.py`), que es lo que hace que una File Geodatabase y una
conexión SDE se traten igual y que exista un lector en memoria para pruebas.

## 5. Las piezas que había que escribir

### 5.1 GeoPackage con `sqlite3`

ArcGIS trae `CreateSQLiteDatabase`, pero su disponibilidad depende de la versión
(en ArcMap 10.x es irregular), no controla el nombre de las tablas ni los tipos
de columna, y no permite añadir la tabla auxiliar que necesita la sincronización
de vuelta.

`writers/geopackage.py` escribe el contenedor con `sqlite3`, que está en
cualquier Python:

- tablas del estándar OGC (`gpkg_spatial_ref_sys`, `gpkg_contents`,
  `gpkg_geometry_columns`, `gpkg_extensions`);
- índice espacial **R-Tree** con el juego completo de disparadores del anexo F.3
  de la especificación —sin ellos el índice quedaría desincronizado en cuanto se
  editara en campo—;
- `gpkg_ogr_contents`, para contar entidades sin recorrer la tabla;
- envolvente en la cabecera de cada geometría, para que el filtrado espacial del
  dispositivo sea rápido.

La geometría llega de `arcpy.Geometry.WKB`, que usa la convención antigua de
banderas de bits para Z (`0x80000001`). `utils/wkb.py` la **normaliza a WKB
ISO** (`1001`), que es lo que exige GeoPackage, calcula la envolvente sin
ninguna librería espacial y promociona a multiparte las líneas y polígonos de
una sola parte, porque una clase de ESRI admite ambas y GeoPackage exige que la
geometría coincida con el tipo declarado.

### 5.2 El archivo de proyecto

`writers/qfield_project.py` lo construye entero desde el esquema. La traducción
del modelo de ESRI es el corazón del programa:

| En la geodatabase | En el proyecto de QField |
|---|---|
| Dominio de valores codificados (≤ umbral) | widget `ValueMap` |
| Dominio de valores codificados (> umbral) | tabla de catálogo `dom_*` + `ValueRelation` |
| Dominio de rango | `Range` con mínimo y máximo |
| Alias de campo | `aliases` |
| Valor por defecto del subtipo por defecto | `defaults` |
| Campo no anulable | `constraints` |
| Subtipos | símbolo por subtipo + `ValueMap` |
| Relationship class | `relations` + pestaña de hijos en el formulario del padre |
| Categoría del campo | pestañas del formulario y visibilidad |
| Campo de fecha | `DateTime` con calendario |
| Campo de foto configurado | `ExternalResource` con expresión de nombrado |
| `OBJECTID`, `GlobalID` | ocultos y no editables (viajan porque hacen falta para volver) |

Tres decisiones merecen explicación:

**Dominios grandes → tabla de catálogo.** Volcar `UP_TRF_TODOS` (1853 valores)
dentro del XML lo haría enorme y lento de abrir en un teléfono. Como tabla del
GeoPackage con un `ValueRelation` encima, QField ofrece un desplegable con
búsqueda. El umbral es configurable (40 por omisión).

**Dominios que dependen del subtipo → unión, y aviso.** QField no puede cambiar
la lista de valores según el subtipo del registro. Se ofrece la **unión** de los
dominios posibles y el verificador emite un aviso por cada campo donde esto
ocurre. Es una limitación real de la plataforma destino: se documenta en vez de
ocultarse.

**Se exportan todos los campos, aunque el formulario oculte algunos.** Si un
campo no viajara, su valor se perdería al devolver el registro. Lo que la
configuración decide es la **visibilidad**, no la presencia del dato.

Las propiedades de capa se escriben con las claves `QFieldSync/*` porque **son
las que lee QField en el dispositivo** (bloqueo de geometría, nombrado de fotos,
seguimiento GPS). Es el vocabulario del destino, no una dependencia.

### 5.3 El ámbito de exportación

Es la pieza que decide **qué trozo de la red se lleva a campo**, y la que más
lógica esconde.

El dato que lo explica todo: en el modelo de CNEL EP, **el campo de alimentador
existe en 26 de las 47 clases**. Las 21 restantes son casi todas tablas *Unidad*
—los transformadores de un puesto, las estructuras de un poste— y catálogos. No
tienen alimentador porque lo heredan de su *Puesto*.

Por eso un `WHERE` por clase escrito a mano no sirve: sería imposible de
mantener y, peor, dejaría al técnico sin el material montado en lo que sí viajó.
`core/scope.py` resuelve cada clase por uno de tres caminos:

1. **por atributo**, cuando la clase tiene el campo del ámbito;
2. **por relación**, cuando no lo tiene pero cuelga de una clase que sí: la
   Unidad se filtra con las claves de los Puestos **realmente exportados**, que
   el empaquetador va recogiendo al copiarlos (por eso ordena los padres antes
   que los hijos);
3. **completa**, dejando constancia, cuando no hay ni campo ni relación.

La **subestación** se resuelve en dos pasos, como manda el modelo: la tabla
`CIRCUITOFUENTE` (un registro por alimentador, con `IDSUBESTACION` y
`CODIGOALIMENTADOR`) da los alimentadores de esa subestación, y a partir de ahí
el ámbito se comporta como uno por alimentador.

Detalles que importan en producción:

- las listas `IN` se **trocean en bloques de 900** valores y la clase se recorre
  una vez por bloque: Oracle corta en 1000 y SQL Server se degrada antes;
- un filtro resuelto pero **sin valores** produce `1 = 0`, no una exportación
  completa: es preferible un paquete vacío que un paquete con toda la Unidad de
  Negocio por descuido;
- el plan se puede **ver antes de generar nada**, clase por clase;
- los valores elegibles salen del **dominio de la geodatabase abierta**, nunca
  de una lista fija, porque el propio catálogo advierte que `Codigo
  Alimentador`, `Numero Estacion` y `Subestacion` cambian en cada Unidad de
  Negocio.

### 5.4 Verificación previa

`core/checker.py` revisa antes de generar: colisiones de nombre de tabla, clases
sin sistema de referencia, campos que chocan con columnas reservadas del
GeoPackage, clases sin GlobalID (la sincronización quedaría atada a `OBJECTID`,
que cambia si la clase se comprime), dominios que dependen del subtipo, capas
demasiado grandes para un teléfono y desviaciones entre el esquema real y el
catálogo del perfil.

### 5.5 La vuelta

El empaquetador guarda dentro del propio GeoPackage una tabla **`qfe_baseline`**
con la huella (`md5` de los campos reescribibles + la geometría normalizada) de
cada entidad tal como salió. Esa tabla **no se registra en `gpkg_contents`**, de
modo que ninguna herramienta la ve.

Al volver, `core/synchronizer.py` compara y distingue:

- **altas**: filas que no estaban en la línea base;
- **modificaciones**: filas cuya huella cambió;
- **bajas**: filas de la línea base que ya no están;
- **conflictos**: el registro *también* cambió en la geodatabase desde el
  empaquetado. Por omisión no se aplican: se informan para que decida una
  persona.

Las **bajas no se aplican salvo petición expresa**: borrar un elemento de una
red eléctrica desde un teléfono es una decisión seria.

Toda la escritura ocurre dentro de una **sesión de edición de arcpy**
(`arcpy.da.Editor`), obligatoria en SDE versionado y que además permite revertir
el lote completo si algo falla a mitad.

### 5.6 QFieldCloud

`core/cloudapi.py` habla con QFieldCloud sobre `urllib`: login, proyectos,
subida y bajada. Sin dependencias externas, porque instalar paquetes en el
Python de ArcGIS suele requerir permisos de administrador. Al subir se omite el
manifiesto por omisión: contiene rutas de servidor y el nombre de la conexión,
que no tienen por qué salir de la organización.

## 6. La geodatabase corporativa

El requisito de "dejarlo abierto a SDE" no se resolvió con un parámetro sino con
una separación. Para el lector de arcpy, una `.gdb` y un `.sde` se abren igual;
lo único que cambia es que en SDE se detecta el versionado, la sesión de edición
se abre con deshacer, el recorte por área de interés aprovecha el índice
espacial del servidor y los nombres calificados (`GYE.SDE.Barra`) se normalizan
al crear las tablas.

## 7. El perfil: lo que la geodatabase no sabe de sí misma

La geodatabase sabe qué dominios y subtipos tiene, pero **no** sabe qué campos
son obligatorios según el manual, ni cuáles son de auditoría, ni qué tabla es la
*Unidad* de qué *Puesto*. Eso está en el catálogo del modelo, y por eso existe
`profiles/cnel_ep.json`, generado desde `docs/modelo/` con
`tools/build_profile.py`: 47 clases, 79 relaciones documentadas y la categoría
(CORE / conectividad / sistema / otro) de **1 981 campos**.

Lo que **no** está en el perfil, deliberadamente, son los valores de los
dominios. Así el mismo perfil sirve para Guayaquil, Manabí o Milagro. Para una
geodatabase que no sea la de CNEL EP existe el perfil `generico`, que clasifica
los campos por heurística de nombre.

## 8. Lo que no se trasladó, y por qué

| Función | Estado | Motivo |
|---|---|---|
| Mapa base en mbtiles | No incluida | Depende de un motor de renderizado. Se puede referenciar uno existente. |
| Temas de mapa | No incluida | No hay equivalente en el modelo de ESRI; se emula con los grupos de capas. |
| Simbología de ArcGIS/ArcFM | Renderizado propio | Se genera un símbolo por subtipo con una paleta legible al sol. Trasladar la simbología completa de ArcFM excede el alcance. |
| Seguimiento GPS, geovallado | Propiedades escritas, sin interfaz | Las claves están soportadas por el escritor; falta exponerlas. |
| Valores M | Se conservan si no se edita | QField no edita medidas. Se avisa en la verificación. |
| Red geométrica y trazado de ArcFM | No se replica | `ParentCircuitSourceGUID` lo calcula el trace; en campo se captura y el trace se vuelve a correr en ArcGIS. Los campos viajan de solo lectura. |
| Adjuntos existentes de la geodatabase | Ida no, vuelta sí | Las fotos capturadas se registran con `AddAttachments`; llevar los adjuntos existentes dispararía el tamaño del paquete. |

## 9. Verificación

213 pruebas que se ejecutan **sin ArcGIS instalado**, sobre una geodatabase de
demostración en memoria que reproduce un fragmento real del modelo (poste, tramo
MT con subtipos, puesto de transformación con sus transformadores y la tabla de
alimentador cabecera con tres alimentadores repartidos en tres subestaciones):

```
python -m unittest discover -s tests -t .
```

Cubren el contenedor GeoPackage (cabeceras, índice espacial, disparadores), la
normalización de WKB, la estructura del archivo de proyecto, el perfil, el
ámbito de exportación en sus seis formas, el empaquetado completo, los adjuntos,
el ciclo de vuelta con detección de conflictos, la caja de herramientas de
ArcGIS (cargada con un `arcpy` simulado), la lógica de la aplicación de
escritorio, el lanzador y el guardia de dependencias.

Para editar un GeoPackage con `sqlite3` puro —lo que hacen las pruebas, y lo que
puede necesitar cualquier script— hace falta registrar las funciones `ST_*` que
usan los disparadores del índice espacial: `utils/sqlite_gpkg.connect()` las
proporciona.

## 10. Ciclo de trabajo resultante

```
Oficina                              Campo                    Oficina
───────                              ─────                    ───────
1 Abrir y analizar la geodatabase
2 Elegir ámbito y exportar   ─────►  QField (sin cobertura)
  (o publicar en QFieldCloud)          captura y edición
                                            │
                                   QFieldCloud o cable
                                            │
                                            └──────────────►  3 Comparar
                                                                 y aplicar
                                                              + volver a correr
                                                                el trace de ArcFM
```

---

*Publicado bajo GPL v2 o posterior.*
