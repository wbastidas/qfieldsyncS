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
en el registro de Windows y en las rutas habituales; si no lo encuentra, explica
exactamente qué definir en vez de fallar con un `ImportError` a media ejecución.

En el registro se consultan las claves donde ESRI publica de verdad la ruta:
`SOFTWARE\ESRI\Python10.x` → `PythonDir` para ArcGIS Desktop (que apunta a
`C:\Python27\`, con `ArcGIS10.x` y `ArcGISx6410.x` dentro) y
`SOFTWARE\ESRI\ArcGISPro` → `InstallDir` para Pro. Se miran las dos vistas del
registro, la nativa y la de 32 bits, porque ArcMap es una aplicación de 32 bits
y sus claves viven en la vista de 32 bits de un Windows de 64.

### 3.1 ArcMap manda, Pro se admite

El objetivo es **ArcGIS Desktop**, cuyo Python es **2.7** y no va a cambiar.
Pro se admite, pero no dicta el código: una sola *f-string* o una llamada que
solo exista en Pro y el programa deja de arrancar en la mitad de las
instalaciones a las que va dirigido —y el fallo no aparece en desarrollo, sino
en el equipo del técnico—.

Esa restricción se sostiene con dos decisiones y dos pruebas. Las decisiones:
escribir el árbol entero en el subconjunto que 2.7 entiende (sin f-strings, sin
anotaciones, `class X(object)`, `io.open`, formateo con `%`) y llamar a las
herramientas de arcpy por la forma que existe en las dos versiones
(`arcpy.Delete_management`, no `arcpy.management.Delete`). Las pruebas
—descritas en la sección 9— recorren todo el código y fallan si algo de eso se
rompe. Donde una función solo existe en una versión, se pregunta antes: por eso
`arcpy.FromWKT` (que es de Pro) tiene alternativa para ArcMap, construida con
un cursor sobre una clase temporal en memoria.

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
  symbology/                         profiles/  utils/
 ┌────────────┐
 │ .lyrx (CIM)│──┐
 │ MXD/.lyr   │──┤ modelo neutro de símbolos ──► simbología del proyecto
 │ estilo JSON│──┤
 │ automática │──┘
 └────────────┘
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

### 5.4 Qué clases viajan

El ámbito responde "qué trozo de la red"; esta pieza responde "qué clases", que
es una pregunta distinta y tan necesaria como aquella. Se combinan: *los
clientes **del** alimentador 04BH070T11*.

La decisión de diseño está en **dónde vive el conocimiento**. Que
`CONEXIONCONSUMIDOR` sea cosa de clientes y no de la red no lo sabe la
geodatabase: lo sabe el modelo. Por eso los conjuntos temáticos se declaran en
el perfil (`class_sets`, generados junto al resto desde el catálogo) y no se
deducen en caliente. Lo que sí sale de la geodatabase son los conjuntos por
geometría —solo puntos, solo líneas, solo tablas—, que valen para cualquier
modelo, incluido uno que no sea el de CNEL EP.

Dos detalles que hacen la diferencia entre una opción usable y una que genera
llamadas de teléfono:

- **Los conjuntos vacíos no se ofrecen.** Ofrecer "solo alumbrado" en una
  geodatabase sin luminarias solo produce un paquete vacío.
- **Se arrastra lo que cuelga de lo elegido**, siguiendo las *relationship
  classes* y de forma transitiva. Un poste sin sus estructuras montadas no
  sirve para revisarlo en campo. Pero lo que el usuario desmarca a mano no
  vuelve a entrar por el arrastre: lo que se ve marcado es lo que se recibe, y
  esa previsibilidad vale más que la comodidad de rellenar huecos.

### 5.5 La simbología

Es el punto donde más se nota que ArcGIS y QField no guardan las cosas en el
mismo sitio. **ArcGIS no guarda la simbología en la geodatabase**: vive en el
MXD, en el proyecto de Pro, en un `.lyr` o en un `.lyrx`. Una migración que solo
leyera la geodatabase llegaría a campo en gris.

Lo primero fue averiguar qué se puede leer y con qué fidelidad:

| Origen | Formato | Qué expone |
|---|---|---|
| `.lyrx` | **JSON** (CIM) | Todo: colores, grosores, guiones, marcadores, etiquetas, escalas. Se lee **sin ArcGIS**, con la biblioteca estándar |
| Capa en **ArcGIS Pro** | CIM vía `layer.getDefinition("V3")` | Todo, porque devuelve el mismo JSON |
| `.lyr` / MXD en **ArcMap** | Binario, vía `arcpy.mapping` | Solo la **clasificación**: el campo, los valores y sus rótulos. **Los colores no los publica la API** |

Esa asimetría es de ArcGIS, no del programa; por eso la vía recomendada es
exportar `.lyrx`, y por eso la lectura de un MXD avisa expresamente de que los
colores que se ven salen de la paleta de qfieldESRI.

La arquitectura que salió de ahí es un **modelo neutro** (`symbology/model.py`)
con cuatro productores y un consumidor:

```
.lyrx (CIM, JSON)  ─┐
mapa abierto/MXD   ─┤
archivo de estilo  ─┼──►  Symbol / Renderer / Label  ──►  proyecto de QField
resolución auto    ─┘        (mm, RGBA 0-255)
```

Todo el modelo está **en milímetros** y en RGBA 0-255, que es lo que espera el
destino; la conversión desde los puntos de ArcGIS (`25.4/72`) se hace una sola
vez, al leer. Ni el lector conoce el XML del destino ni el escritor conoce el
CIM.

Las fuentes se ordenan por **precedencia explícita** (`symbology/__init__.py`):
archivo de estilo del usuario → simbología importada de ArcGIS → estilo del
perfil → automática. Cada capa **registra de dónde salió su estilo** y el
empaquetado lo informa: quien recibe el paquete tiene que poder saber si está
viendo la simbología de la oficina o un color inventado.

El **archivo de estilo** (`symbology/stylesheet.py`) es JSON en castellano y
existe por tres razones: no depender de ArcGIS para fijar la simbología, poder
revisarla en un control de versiones, y poder editarla sin abrir nada. No se
escribe a mano desde cero: se **genera** el estilo que se aplicaría ahora mismo
y se retoca. Junto a los tipos habituales (`simple`, `categorizado`, `graduado`,
`reglas`) tiene uno propio, `subtipos`, que clasifica por los subtipos **que
declare la geodatabase**: los códigos no se escriben en el archivo, se leen en
caliente, porque cambian de una Unidad de Negocio a otra —la misma razón por la
que los valores de los dominios no están en el perfil.

La **resolución automática** (`symbology/defaults.py`) es la última red: forma
del marcador según el papel de la clase en el modelo, color derivado del nombre
de la clase con una mezcla posicional (no del orden de empaquetado, para que la
misma clase salga siempre igual), etiqueta del primer campo con sentido —
`TEXTOETIQUETA` está en 27 de las 47 clases— y límite de escala en las clases
densas, porque un teléfono no dibuja doscientas mil acometidas a escala de
provincia.

Antes de escribir, se **valida contra lo que de verdad viaja**: si el
renderizador clasifica por un campo que no se exportó, se degrada a símbolo
único conservando el color; si una etiqueta usa un campo que no viaja, se
desactiva. Las dos cosas con aviso: degradar en silencio sería peor que fallar.

### 5.6 Verificación previa

`core/checker.py` revisa antes de generar: colisiones de nombre de tabla, clases
sin sistema de referencia, campos que chocan con columnas reservadas del
GeoPackage, clases sin GlobalID (la sincronización quedaría atada a `OBJECTID`,
que cambia si la clase se comprime), dominios que dependen del subtipo, capas
demasiado grandes para un teléfono y desviaciones entre el esquema real y el
catálogo del perfil.

### 5.7 La vuelta

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

### 5.8 QFieldCloud

`core/cloudapi.py` habla con QFieldCloud sobre `urllib`: login, proyectos,
subida y bajada. Sin dependencias externas, porque instalar paquetes en el
Python de ArcGIS suele requerir permisos de administrador. Al subir se omite el
manifiesto por omisión: contiene rutas de servidor y el nombre de la conexión,
que no tienen por qué salir de la organización.

## 6. La geodatabase corporativa: Oracle 11gR2 con ArcSDE

El requisito de "dejarlo abierto a SDE" no se resolvió con un parámetro sino con
una separación. Para el lector de arcpy, una `.gdb` y un `.sde` se abren igual.
Lo que de verdad cambia son dos cosas, y las dos importan.

### 6.1 Cómo se llama una clase

Oracle guarda los nombres en mayúsculas y ArcSDE los califica con el usuario
propietario: `EstructuraSoporte` llega como `SIGELEC.ESTRUCTURASOPORTE`, y con otra
conexión como `SDE.ESTRUCTURASOPORTE`. Es la misma clase con otra etiqueta.

Si esa etiqueta se tratara como identidad, contra una base corporativa fallaría
todo en cadena y en silencio: el perfil no reconocería ninguna clase (ni
categorías de campo, ni pares Puesto/Unidad), el ámbito no encontraría el campo
de alimentador, la simbología no casaría y —lo más grave— la sincronización de
vuelta no encontraría la clase de destino del material capturado.

Por eso hay un único sitio (`core/naming.py`) que decide cuándo dos nombres
designan la misma clase, y por él pasan todas las comparaciones: el perfil, la
configuración por capa, el ámbito, la simbología, el arrastre de tablas
relacionadas y la vuelta. La coincidencia exacta tiene prioridad —si hay dos
esquemas cargados hay que respetar el que se pidió— y solo después se afloja.

Efecto práctico: **un paquete generado con una conexión se sincroniza con
otra**, que es exactamente lo que pasa cuando se empaqueta en campo y se aplica
desde la oficina.

### 6.2 Cómo se abre la sesión de edición

`arcpy.da.Editor.startEditing(with_undo, multiuser_mode)`. El segundo argumento
no significa "hay varios usuarios": significa que los datos están **registrados
como versionados**. Si se edita una clase no versionada de Oracle en modo
versionado —o al revés— ArcGIS no avisa: falla.

El versionado es una propiedad de cada dataset, no del workspace, así que se
lee de las clases (`Describe.isVersioned`) y la verificación previa lo dice
antes de empezar: con clases versionadas hay que reconciliar y publicar después
de sincronizar; sin ellas se escribe directo en las tablas base y conviene
respaldar antes.

Un fallo puntual —un registro bloqueado por otro editor, un valor que el
dominio rechaza— se anota y no tumba el lote: descartar quinientas capturas
buenas por una mala no le sirve a nadie. Lo que sí es todo o nada es el cierre:
si la base rechaza guardar la sesión, no queda nada aplicado y el informe lo
dice con esas palabras.

### 6.3 Lo demás

El recorte por área de interés aprovecha el índice espacial del servidor; los
nombres calificados se normalizan al crear las tablas del paquete (en el
dispositivo nadie quiere ver `SIGELEC.ESTRUCTURASOPORTE`); las listas del ámbito se
trocean en bloques de 900 porque el `IN` de Oracle corta en 1000; y una clase
ilegible por permisos no tumba el análisis completo.

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
| Colores de un MXD o un `.lyr` en ArcMap | Solo la clasificación | `arcpy.mapping` no publica los colores de un documento binario. Se traslada la estructura y se avisa; para los colores exactos, `.lyrx` o archivo de estilo. |
| Símbolos de fuente e imágenes de marcador | Forma equivalente | El destino no tiene esos símbolos instalados: se sustituyen por la forma geométrica más parecida, con aviso. |
| Representaciones y simbología de ArcFM | No incluida | Son un motor de dibujo propio de ESRI/ArcFM, sin equivalente en el destino. |
| Seguimiento GPS, geovallado | Propiedades escritas, sin interfaz | Las claves están soportadas por el escritor; falta exponerlas. |
| Valores M | Se conservan si no se edita | QField no edita medidas. Se avisa en la verificación. |
| Red geométrica y trazado de ArcFM | No se replica | `ParentCircuitSourceGUID` lo calcula el trace; en campo se captura y el trace se vuelve a correr en ArcGIS. Los campos viajan de solo lectura. |
| Adjuntos existentes de la geodatabase | Ida no, vuelta sí | Las fotos capturadas se registran con `AddAttachments`; llevar los adjuntos existentes dispararía el tamaño del paquete. |

## 9. Verificación

352 pruebas que se ejecutan **sin ArcGIS instalado**, sobre una geodatabase de
demostración en memoria que reproduce un fragmento real del modelo (poste, tramo
MT con subtipos, puesto de transformación con sus transformadores y la tabla de
alimentador cabecera con tres alimentadores repartidos en tres subestaciones):

```
python -m unittest discover -s tests -t .
```

Cubren el contenedor GeoPackage (cabeceras, índice espacial, disparadores), la
normalización de WKB, la estructura del archivo de proyecto, el perfil, el
ámbito de exportación en sus seis formas, la simbología (lectura de CIM,
archivo de estilo, precedencia entre fuentes y serialización), el empaquetado
completo, los adjuntos,
el ciclo de vuelta con detección de conflictos, la caja de herramientas de
ArcGIS (cargada con un `arcpy` simulado), la lógica de la aplicación de
escritorio, el lanzador y el camino completo contra una geodatabase corporativa
con los nombres calificados de Oracle.

Tres de esas pruebas no comprueban comportamiento sino **superficie**, porque
es donde un error no se ve hasta que el programa está en el equipo del técnico:

- ninguna importación de QGIS, Qt ni dependencias externas no declaradas;
- ninguna sintaxis que el **Python 2.7 de ArcMap** no sepa leer (f-strings,
  anotaciones, `super()` sin argumentos, desempaquetado PEP 448, `yield from`,
  `raise ... from`) ni módulos de Python 3 sin respaldo;
- ninguna llamada de `arcpy` que solo exista en **ArcGIS Pro** —`arcpy.FromWKT`
  o el módulo `arcpy.management`—, y `arcpy.mp` / `arcpy.mapping` siempre
  consultados con `hasattr` antes de usarse.

Para editar un GeoPackage con `sqlite3` puro —lo que hacen las pruebas, y lo que
puede necesitar cualquier script— hace falta registrar las funciones `ST_*` que
usan los disparadores del índice espacial: `utils/sqlite_gpkg.connect()` las
proporciona.

## 10. Ciclo de trabajo resultante

```
Oficina                              Campo                    Oficina
───────                              ─────                    ───────
1 Abrir y analizar la geodatabase
2 Elegir qué clases se llevan
  y preparar la simbología
3 Elegir ámbito y exportar   ─────►  QField (sin cobertura)
  (o publicar en QFieldCloud)          captura y edición
                                            │
                                   QFieldCloud o cable
                                            │
                                            └──────────────►  4 Comparar
                                                                 y aplicar
                                                              + volver a correr
                                                                el trace de ArcFM
```

---

*Publicado bajo GPL v2 o posterior.*
