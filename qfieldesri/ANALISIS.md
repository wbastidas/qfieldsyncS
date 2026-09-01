# Análisis: de QFieldSync (QGIS) a qfieldESRI (ArcGIS Desktop)

Este documento explica **qué hace QFieldSync**, **qué de eso sirve tal cual**,
**qué no puede sobrevivir al cambio de plataforma** y **cómo se resolvió cada
pieza** en qfieldESRI, el complemento nuevo que lleva una geodatabase de ESRI a
QField y devuelve lo capturado en campo.

---

## 1. Qué es QFieldSync y de qué está hecho

QFieldSync es un complemento de **QGIS**. Su punto de partida es un proyecto
QGIS ya montado por el usuario, y su trabajo es *transformarlo* para que
funcione en un dispositivo móvil:

| Módulo de QFieldSync | Qué hace |
|---|---|
| `libqfieldsync.offline_converter` | Copia las capas a un GeoPackage y reescribe el proyecto para que apunte a él |
| `libqfieldsync.layer.LayerSource` | Acción por capa (copiar / offline / no tocar / quitar) y opciones de QField, guardadas como propiedades `QFieldSync/*` de la capa |
| `libqfieldsync.project.ProjectConfig` | Opciones del proyecto (mapa base, modo inicial, tamaño de foto…), guardadas en el grupo `qfieldsync` de las propiedades del proyecto |
| `libqfieldsync.project_checker` | Verificación previa: avisa de rutas absolutas, capas inválidas, nombres conflictivos… |
| `qfieldsync.core.cloud_api` / `cloud_transferrer` | Cliente de QFieldCloud sobre `QgsNetworkAccessManager` |
| `qfieldsync.gui.*` | Diálogos Qt integrados en el menú de QGIS |

La dependencia crítica es que **todo se apoya en la API de QGIS**: `QgsProject`,
`QgsVectorLayer`, `QgsNetworkAccessManager`, PyQt. Nada de eso existe dentro de
ArcGIS Desktop.

## 2. El otro extremo: qué hay en ArcGIS y en el modelo de datos

El origen no es un proyecto, es una **geodatabase** —File Geodatabase hoy,
geodatabase corporativa (SDE) mañana— con el modelo eléctrico homologado de
CNEL EP descrito en `docs/modelo/`:

- 47 clases (28 clases de entidad + 19 tablas) en los feature datasets
  `Electrico` y `Electrico_Complementos`, en **EPSG:32717**;
- **196 dominios**, algunos enormes (`UP_TRF_TODOS` tiene 1853 miembros) y tres
  de ellos —`Codigo Alimentador`, `Numero Estacion`, `Subestacion`— **distintos
  en cada Unidad de Negocio**;
- **subtipos** que cambian el dominio del mismo campo (`VOLTAJE` en `Barra` usa
  *Voltaje BT*, *MT* o *AT* según el subtipo);
- **79 relationship classes**, que materializan el patrón **Puesto / Unidad**
  del manual `MN-TEC-OPE-100`: el Puesto es el punto en el mapa, las Unidades
  son las filas de atributos constructivos que cuelgan de él;
- una **red geométrica** `Electrico_RedGeom` con 7 clases *edge* y 13
  *junction*, y la conectividad `CircuitSourceGUID` ↔ `ParentCircuitSourceGUID`
  que mantienen los auto-actualizadores de ArcFM.

QField, en cambio, solo sabe abrir **un proyecto QGIS con capas OGR**. Ese es el
hueco que qfieldESRI tiene que cubrir.

## 3. Decisión de fondo: no portar, reconstruir el eje

Portar QFieldSync módulo a módulo es imposible: su núcleo *es* la API de QGIS.
Pero su **arquitectura** sí se reutiliza, y es lo valioso:

```
QFieldSync                          qfieldESRI
──────────────────────────────────  ──────────────────────────────────────────
Proyecto QGIS (entrada)             Geodatabase de ESRI (entrada)
      │                                   │
      ├─ ProjectChecker                   ├─ core/checker.py
      ├─ LayerSource (acciones)           ├─ core/config.py (LayerAction)
      ├─ OfflineConverter                 ├─ core/packager.py
      │     └─ QGIS escribe el GPKG       │     ├─ writers/geopackage.py  (propio)
      │     └─ QGIS reescribe el .qgs     │     └─ writers/qgis_project.py (propio)
      ├─ cloud_api (Qt)                   ├─ core/cloudapi.py (urllib)
      ├─ Diálogos Qt                      ├─ QFieldESRI.pyt (Python Toolbox)
      └─ deltas de QFieldCloud            └─ core/synchronizer.py (línea base propia)
```

Los dos módulos que QFieldSync *no tiene que escribir* —el GeoPackage y el
`.qgs`— son justamente los que aquí hay que escribir a mano, porque en QGIS los
pone la propia aplicación. Son el 60 % del trabajo de este complemento.

## 4. Equivalencias, una por una

### 4.1 Datos: GeoPackage escrito con `sqlite3`

**Por qué no usar la herramienta nativa de ArcGIS.** ArcGIS trae
`CreateSQLiteDatabase` + `FeatureClassToFeatureClass`, pero su disponibilidad
depende de la versión (en ArcMap 10.x es irregular), no controla el nombre de
las tablas ni los tipos de columna, y no permite añadir la tabla auxiliar que
necesita la sincronización de vuelta.

`writers/geopackage.py` escribe el contenedor directamente con `sqlite3`
(biblioteca estándar, presente en cualquier Python de ArcGIS):

- tablas del estándar OGC: `gpkg_spatial_ref_sys`, `gpkg_contents`,
  `gpkg_geometry_columns`, `gpkg_extensions`;
- índice espacial **R-Tree** con el juego completo de disparadores del anexo F.3
  de la especificación —sin ellos QField dejaría el índice desincronizado al
  editar—;
- `gpkg_ogr_contents`, la extensión que usa GDAL (y por tanto QGIS y QField)
  para contar entidades sin recorrer la tabla;
- cabecera binaria de geometría con envolvente, para que el filtrado espacial
  del dispositivo sea rápido.

La geometría llega de `arcpy.Geometry.WKB`, que usa la convención antigua de
banderas de bits para Z (`0x80000001`); `utils/wkb.py` la **normaliza a WKB
ISO** (`1001`), que es lo que exige GeoPackage, calcula la envolvente sin
ninguna librería espacial y promociona a multiparte las líneas y polígonos de
una sola parte, porque una clase de ESRI admite ambas y GeoPackage exige que la
geometría coincida con el tipo declarado.

### 4.2 Formularios: el `.qgs` se genera desde el esquema

`writers/qgis_project.py` escribe el proyecto desde cero. La traducción del
modelo de ESRI al de QGIS es el corazón del complemento:

| En la geodatabase | En el proyecto de QField |
|---|---|
| Dominio de valores codificados (≤ umbral) | widget `ValueMap` |
| Dominio de valores codificados (> umbral) | tabla de catálogo `dom_*` en el GeoPackage + widget `ValueRelation` |
| Dominio de rango | widget `Range` con mínimo y máximo |
| Alias de campo | `<aliases>` |
| Valor por defecto del subtipo por defecto | `<defaults>` |
| Campo no anulable | `<constraints>` con `notnull_strength` |
| Subtipos | `ValueMap` en el campo de subtipo **y** renderizado categorizado |
| Relationship class | `<relations>` + pestaña de hijos en el formulario del padre |
| Categoría del campo (CORE / conectividad / sistema) | pestañas del formulario y visibilidad |
| Campo de fecha | widget `DateTime` con calendario |
| Campo de foto configurado | `ExternalResource` con la expresión de nombrado de QFieldSync |
| `OBJECTID`, `GlobalID` | campos ocultos y no editables (viajan porque hacen falta para volver) |

Tres decisiones merecen explicación:

**Dominios grandes → tabla de catálogo.** Volcar `UP_TRF_TODOS` (1853 valores)
como `ValueMap` dentro del XML haría el `.qgs` enorme y lento de abrir en un
teléfono. Como tabla del GeoPackage con un `ValueRelation` encima, QField ofrece
un desplegable con búsqueda. El umbral es configurable (40 por omisión).

**Dominios que dependen del subtipo → unión, y aviso.** QField no puede cambiar
la lista de valores según el subtipo del registro. Se ofrece la **unión** de los
dominios posibles y el verificador emite un aviso por cada campo donde esto
ocurre, para que el supervisor lo tenga presente al revisar lo capturado. Es una
limitación real de la plataforma destino, no un descuido: se documenta en vez de
ocultarse.

**Se exportan todos los campos, aunque el formulario oculte algunos.** Si un
campo no viajara al dispositivo, su valor se perdería al devolver el registro a
la geodatabase. Lo que la configuración decide es la **visibilidad**, no la
presencia del dato.

### 4.3 Vocabulario de QFieldSync: se conserva tal cual

Las propiedades de capa se escriben con **las mismas claves** que usa
`libqfieldsync` (`QFieldSync/action`, `QFieldSync/is_feature_addition_locked`,
`QFieldSync/attachment_naming`…) y las opciones de proyecto van al grupo
`qfieldsync`. Consecuencia práctica: un proyecto generado por qfieldESRI se
puede abrir en QGIS y seguir manteniendo con QFieldSync, sin traducción
intermedia. Las acciones por capa (`copy`, `read_only`, `empty`, `remove`)
siguen la nomenclatura de `SyncAction`.

### 4.4 Verificación previa

`core/checker.py` es el equivalente de `project_checker`, con los chequeos que
importan en este contexto: colisiones de nombre de tabla, clases sin sistema de
referencia, campos que chocan con columnas reservadas del GeoPackage, clases sin
GlobalID (la sincronización quedaría atada a `OBJECTID`, que cambia si la clase
se comprime), dominios que dependen del subtipo, capas demasiado grandes para un
teléfono, y desviaciones entre el esquema real y el catálogo del perfil.

### 4.5 Interfaz: Python Toolbox en vez de diálogos Qt

Los diálogos Qt de QFieldSync no tienen equivalente ni sentido en ArcGIS. La
forma nativa de extender ArcMap y ArcGIS Pro es una **caja de herramientas**:
`QFieldESRI.pyt` expone cinco herramientas (analizar, empaquetar, sincronizar,
publicar, recuperar) que además quedan disponibles en ModelBuilder y en la
ventana de Python. Todo lo que hacen está también en `python -m qfieldesri`,
para automatizar.

### 4.6 QFieldCloud sin Qt

`core/cloudapi.py` reimplementa sobre `urllib` lo que `cloud_api.py` hace sobre
`QgsNetworkAccessManager`: login, proyectos, subida y bajada de archivos. Sin
dependencias externas, porque instalar paquetes en el Python de ArcGIS suele
requerir permisos de administrador. Al subir se omite el manifiesto por omisión:
contiene rutas de servidor y el nombre de la conexión, que no tienen por qué
salir de la organización.

### 4.7 La vuelta: línea base propia en lugar de deltas de QFieldCloud

QFieldSync delega el regreso de los datos en el mecanismo de *deltas* de
QFieldCloud. Aquí no hay servidor obligatorio, así que el empaquetador guarda
dentro del propio GeoPackage una tabla **`qfe_baseline`** con la huella
(`md5` de los campos reescribibles + la geometría normalizada) de cada entidad
tal como salió de la geodatabase. Esa tabla **no se registra en
`gpkg_contents`**, de modo que QGIS, QField y GDAL no la ven.

Al volver, `core/synchronizer.py` compara tres cosas y distingue:

- **altas**: filas que no estaban en la línea base;
- **modificaciones**: filas cuya huella cambió;
- **bajas**: filas de la línea base que ya no están;
- **conflictos**: el registro *también* cambió en la geodatabase desde el
  empaquetado. Por omisión no se aplican: se informan para que decida una
  persona (`--conflictos campo` permite que gane lo capturado).

Las **bajas no se aplican salvo petición expresa** (`--aplicar-bajas`): borrar
un elemento de una red eléctrica desde un teléfono es una decisión seria.

Toda la escritura ocurre dentro de una **sesión de edición de arcpy**
(`arcpy.da.Editor`), que en una geodatabase corporativa versionada es
obligatoria y además permite revertir el lote completo si algo falla a mitad.

## 5. Lo que hace posible la geodatabase corporativa

El requisito de "dejarlo abierto para geodatabase corporativa" no se resolvió
con un parámetro, sino con una separación:

```
readers/base.py      contrato: describir, iterar, escribir
readers/arcpy_reader.py   File GDB, Personal GDB y SDE (mismo código)
readers/ogr_reader.py     respaldo con GDAL, sin ArcGIS (solo lectura)
readers/memory.py         pruebas y demostración
```

Ni `core`, ni `writers`, ni `profiles`, ni `utils` importan arcpy. El
empaquetador solo habla con la interfaz del lector. Para el lector de arcpy, una
File Geodatabase y una conexión `.sde` se abren igual: lo único que cambia es
que en SDE se detecta el versionado, se abre la sesión de edición con `undo` y
el recorte por área de interés aprovecha el índice espacial del servidor a
través de `SelectLayerByLocation`. Los nombres calificados (`GYE.SDE.Barra`) se
normalizan al escribir las tablas del GeoPackage.

## 6. El perfil: lo que la geodatabase no sabe de sí misma

La geodatabase sabe qué dominios y subtipos tiene, pero **no** sabe qué campos
son obligatorios según el manual, ni cuáles son de auditoría, ni qué tabla es la
*Unidad* de qué *Puesto*. Eso está en el catálogo del modelo, y por eso existe
`profiles/cnel_ep.json`, generado desde `docs/modelo/` con
`tools/build_profile.py`: 47 clases, 79 relaciones documentadas, y la categoría
(CORE ✅ / conectividad 🔌 / sistema 🔧 / otro ▫️) de **1 981 campos**.

Lo que **no** está en el perfil, deliberadamente, son los dominios y sus valores:
el propio catálogo advierte que `Codigo Alimentador`, `Numero Estacion` y
`Subestacion` cambian en cada Unidad de Negocio. Se leen siempre en caliente de
la geodatabase activa. Así el mismo perfil sirve para Guayaquil, Manabí o
Milagro.

Para una geodatabase que no sea la de CNEL EP existe el perfil `generico`, que
clasifica los campos por heurística de nombre —el mismo criterio con el que el
catálogo clasificó los campos comunes—.

## 7. Lo que no se trasladó, y por qué

| Función de QFieldSync | Estado en qfieldESRI | Motivo |
|---|---|---|
| Generación de mapa base (mbtiles) | No incluida | Depende del motor de renderizado de QGIS y de sus algoritmos de procesamiento. Se puede referenciar un mapa base ya existente. |
| Temas de mapa | No incluida | No hay equivalente en el modelo de datos de ESRI; se emula parcialmente con los grupos de capas. |
| Simbología de ArcGIS (capas .lyr) | Renderizado propio | Se genera un renderizado por subtipo con una paleta legible en pantalla al sol. Trasladar la simbología completa de ArcFM excede el alcance. |
| Seguimiento GPS, geovallado | Se escriben las propiedades, no hay interfaz | Las claves `QFieldSync/tracking_*` están soportadas por el escritor; falta exponerlas en el Toolbox. |
| Valores M | Se conservan si no se edita | QField no edita medidas. Se avisa en la verificación. |
| Red geométrica y trazado de ArcFM | No se replica | `ParentCircuitSourceGUID` lo calcula el trace de ArcFM; en campo se captura, y el trace se vuelve a correr en ArcGIS tras sincronizar. Los campos viajan de solo lectura. |
| Adjuntos binarios de la geodatabase | Ida no, vuelta sí | Las fotos se capturan en campo y se registran con `AddAttachments`; llevar los adjuntos existentes al dispositivo dispararía el tamaño del paquete. |

## 8. Verificación

133 pruebas automatizadas que se ejecutan **sin ArcGIS ni QGIS instalados**,
sobre una geodatabase de demostración en memoria que reproduce un fragmento real
del modelo (poste, tramo MT con subtipos, puesto de transformación y su unidad):

```
python -m unittest discover -s tests -t .
```

Cubren el contenedor GeoPackage (cabeceras, índice espacial, disparadores), la
normalización de WKB, la estructura del `.qgs` (widgets, relaciones, pestañas,
restricciones), el perfil, el empaquetado completo y el ciclo de vuelta con
detección de conflictos.

Para editar un GeoPackage con `sqlite3` puro —lo que hacen las pruebas, y lo que
puede necesitar cualquier script— hace falta registrar las funciones `ST_*` que
usan los disparadores del índice espacial: `utils/sqlite_gpkg.connect()` las
proporciona.

## 9. Ciclo de trabajo resultante

```
ArcGIS Desktop                        Campo                     ArcGIS Desktop
──────────────                        ─────                     ──────────────
1 Analizar geodatabase
2 Empaquetar para QField  ──────►  QField (sin cobertura)
  (o publicar en QFieldCloud)          captura y edición
                                            │
                                   4/5 QFieldCloud o cable
                                            │
                                            └──────────────►  3 Sincronizar
                                                                 (simula → aplica)
                                                              + volver a correr el
                                                                trace de ArcFM
```

---

*qfieldESRI reutiliza la arquitectura y el vocabulario de QFieldSync
(OPENGIS.ch, GPL v2+) y se publica bajo la misma licencia.*
