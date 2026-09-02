# qfieldESRI

**Programa externo para ArcGIS que lleva una geodatabase de ESRI a QField y
devuelve lo capturado en campo.**

Se instala copiando una carpeta y se abre con doble clic. Trabaja *contra*
ArcGIS —usa `arcpy` para leer y escribir la geodatabase— pero **no depende de
QGIS ni de Qt en ninguna parte**, y no hay que instalar ningún paquete de
Python.

Funciona con una **File Geodatabase** o con una **geodatabase corporativa**
(SDE), sobre ArcMap 10.x o ArcGIS Pro, y viene preparado para el modelo de
datos eléctrico homologado de **CNEL EP** (`MN-TEC-OPE-100`) sin quedar atado a
él.

---

## Sobre QGIS, para que quede claro

QField guarda su proyecto en un archivo con extensión `.qgs`. Eso es un
**formato de archivo**, igual que un `.shp` o un `.gdb`: qfieldESRI lo escribe
como XML con la biblioteca estándar de Python. No se importa QGIS, no se
instala QGIS y no hace falta QGIS en ningún equipo, ni en el de la oficina ni en
el del técnico.

La prueba `tests/test_dependencias.py` recorre todo el código en cada ejecución
de la batería y **falla si aparece una sola importación** de `qgis`, `PyQt`,
`PySide` o cualquier dependencia externa no declarada. Las únicas dos externas
admitidas son `arcpy` (en su lector, en el lanzador y en la caja de
herramientas) y, opcionalmente, `osgeo` para leer sin ArcGIS.

## Tres formas de usarlo, el mismo motor detrás

| | Cuándo |
|---|---|
| **`QFieldESRI.py`** — aplicación de escritorio | El uso normal. Ventana propia con tres pestañas, no hace falta abrir ArcGIS. |
| **`QFieldESRI.pyt`** — caja de herramientas de ArcGIS | Cuando ya se está trabajando dentro de ArcMap o ArcGIS Pro, o para usarlo en ModelBuilder. |
| **`python -m qfieldesri`** — línea de comandos | Para automatizar (un empaquetado nocturno por alimentador, por ejemplo). |

En los tres casos el origen puede ser una **File Geodatabase** o una
**geodatabase corporativa con ArcSDE** (Oracle 11gR2): se pasa la `.gdb` o el
archivo de conexión `.sde` y el resto es idéntico.

## Qué hace

```
File Geodatabase / SDE  ──►  carpeta de proyecto QField  ──►  QField
        ▲                                                        │
        └──────────────  altas, cambios y conflictos  ◄───────────┘
```

Al empaquetar traduce, sin intervención manual:

- **dominios** → listas de valores válidos en el formulario (y catálogo
  buscable cuando el dominio es grande);
- **subtipos** → lista de subtipos y símbolo distinto por subtipo en el mapa;
- **relationship classes** → el par *Puesto / Unidad* aparece como una pestaña
  de registros hijos dentro de la ficha del padre;
- **alias, valores por defecto y campos obligatorios** → tal como los ve el
  editor en ArcMap;
- **categoría del campo** (obligatorio, conectividad, sistema) → pestañas del
  formulario, para que el técnico no tenga que bajar por 41 campos;
- **simbología y etiquetado** → colores, grosores, guiones, marcadores, flecha
  de sentido, etiquetas con halo y límites de escala, tomados del `.lyrx`, del
  MXD, de un archivo de estilo propio o resueltos automáticamente.

Al volver detecta qué se editó realmente en campo, avisa de los conflictos con
lo que se haya editado en la oficina mientras tanto, y escribe en la
geodatabase dentro de una sesión de edición.

## Qué se lleva a campo: el ámbito de exportación

Nadie sale a campo con toda la Unidad de Negocio. Se elige **un ámbito** y el
programa resuelve solo qué filtro le toca a cada clase:

| Ámbito | Cómo se resuelve |
|---|---|
| **Alimentador** | `ALIMENTADORID` (o el campo de alimentador que tenga cada clase) |
| **Subestación** | Se traduce a sus alimentadores con `CIRCUITOFUENTE` y se aplica como el anterior |
| **Polígono de sector** | Recorte espacial contra una capa de polígonos de la geodatabase |
| **Provincia · Cantón · Parroquia** | `PROVINCIA` / `CANTON` / `PARROQUIA` |

Esto no es un simple `WHERE` repetido: **en el modelo de CNEL EP el campo de
alimentador solo existe en 26 de las 47 clases**. Las otras 21 son casi todas
tablas *Unidad* (los transformadores de un puesto, las estructuras de un poste)
que no tienen alimentador porque lo heredan de su *Puesto*. qfieldESRI las
arrastra automáticamente filtrándolas por las claves de los Puestos que de
verdad se exportaron, así que el técnico nunca se queda sin el material montado
en lo que sí viajó.

Antes de generar nada, el botón **Ver qué se exportaría** explica clase por
clase cómo quedó cada una:

```
Ambito: Alimentador: 04BH070T11
  Filtradas por atributo (3): EstructuraSoporte, PuestoTransfDistribucion, TramoDistribucionAereo
  Filtradas por relacion con su Puesto (1): UNIDADTRANSFDISTRIBUCION
  Se exportan completas (1): CATALOGOESTRUCTURA
```

Los valores elegibles (los 246 alimentadores, las 139 subestaciones…) se leen
**del dominio de la geodatabase que se abrió**, nunca de una lista fija: el
catálogo del modelo advierte que cambian en cada Unidad de Negocio. Además se
puede pedir que solo se ofrezcan los que de verdad aparecen en los datos.

## Cómo se ve en el dispositivo: la simbología

ArcGIS no guarda la simbología dentro de la geodatabase. Vive **fuera**: en el
MXD, en el proyecto de Pro, en un `.lyr` o en un `.lyrx`. Por eso una migración
que solo lea la geodatabase llega a campo en gris: los datos están bien y el
mapa es ilegible. qfieldESRI resuelve la simbología aparte, con cuatro fuentes
en orden de precedencia:

| Orden | Fuente | Qué traslada |
|---|---|---|
| 1 | **Archivo de estilo** de qfieldESRI (`--estilo`) | Todo, y manda sobre lo demás |
| 2 | **`.lyrx`** de ArcGIS Pro (una carpeta o uno suelto) | Fidelidad completa: colores, grosores, guiones, marcadores, etiquetas, escalas |
| 3 | **Mapa abierto**, `.lyr` o MXD (requiere `arcpy`) | En **Pro**, todo (se lee la definición CIM). En **ArcMap**, solo la clasificación: el campo, los valores y sus rótulos — *ArcGIS no publica los colores de un MXD*, y el programa lo avisa |
| 4 | **Automática** | Forma según el papel de la clase, color estable derivado del nombre, etiqueta del primer campo con sentido y límite de escala en las clases densas |

Al terminar, el empaquetado dice de dónde salió cada capa
(`Simbología: 24 archivos de capa de ArcGIS Pro, 3 automática.`), para que quien
reciba el paquete sepa si está viendo la simbología de la oficina o un color
inventado.

### La vía recomendada: exportar `.lyrx`

Un `.lyrx` es **JSON** y qfieldESRI lo lee sin ArcGIS, con los colores exactos.
En ArcGIS Pro: clic derecho sobre la capa → **Compartir → Guardar como archivo
de capa**. Deje todos los `.lyrx` en una carpeta y páseles esa carpeta. El
nombre del archivo casa con el de la clase (también sin distinguir mayúsculas y
sin el esquema de la geodatabase corporativa).

### El archivo de estilo

Para no depender de ArcGIS —y para poder revisar la simbología en un control de
versiones— existe un archivo de estilo propio, en JSON y en castellano:

```json
{
  "capas": {
    "TramoDistribucionAereo": {
      "simbologia": {
        "tipo": "subtipos",
        "colores": ["#d81e05", "#e8663d", "#f4a261"],
        "simbolo": {"ancho": 0.8, "flecha": true, "flecha_intervalo": 18}
      },
      "etiqueta": {"campo": "ALIMENTADORID", "escala_minima": 4000, "halo": 1.0}
    },
    "PuestoTransfDistribucion": {
      "simbologia": {
        "tipo": "simple",
        "simbolo": {"forma": "cuadrado", "color": "#2e8b57", "tamano": 3.2}
      }
    }
  }
}
```

Tipos de simbología: `simple`, `categorizado`, `graduado`, `reglas`, `ninguno` y
**`subtipos`**, que clasifica por los subtipos **que declare la geodatabase** —
los códigos no se escriben a mano, se leen en caliente, porque cambian de una
Unidad de Negocio a otra. `flecha` dibuja el sentido del flujo sobre la línea.

No hace falta escribirlo desde cero: se **genera** y luego se retoca.

```bat
REM el estilo que se aplicaría ahora mismo, listo para editar
%PY% -m qfieldesri estilo --gdb C:\datos\GYE.gdb --salida estilo.json

REM partiendo de los .lyrx de la oficina
%PY% -m qfieldesri estilo --gdb C:\datos\GYE.gdb ^
     --simbologia C:\simbologia\lyrx --salida estilo.json

REM empaquetar con él
%PY% -m qfieldesri empaquetar --gdb C:\datos\GYE.gdb --salida C:\salida ^
     --nombre alimentador_04BH --ambito alimentador --valores 04BH070T11 ^
     --estilo estilo.json
```

En la ventana, el apartado **Cómo se verá en el dispositivo** hace lo mismo con
un botón; en ArcGIS, la herramienta **2 · Preparar simbología**, que además
puede tomarla del **mapa que se tenga abierto** (esa opción no está en la
ventana: es un programa aparte y no ve el documento de ArcGIS).

El modelo CNEL EP trae un estilo de arranque
(`qfieldesri/profiles/cnel_ep.estilo.json`) que se aplica solo: media tensión en
rojo, baja tensión en azul, subtransmisión en negro, aéreo continuo y subterráneo
a trazos, flecha de sentido en media tensión y subtransmisión, y etiquetas con
límite de escala. **No es la simbología oficial de la empresa**: es un punto de
partida legible, pensado para copiarse y editarse.

### Qué se comprueba antes de generar

- Si la simbología clasifica por un campo que **no viaja en el paquete**, se
  degrada a símbolo único conservando el color, y se avisa.
- Si una etiqueta usa un campo que no viaja, se desactiva, y se avisa.
- Las clases con muchas entidades reciben **límite de escala** para que el
  dispositivo no se ahogue dibujando doscientas mil acometidas a escala de
  provincia.

## Requisitos

| | |
|---|---|
| **Objetivo principal** | **ArcGIS Desktop (ArcMap) 10.4 a 10.8**, con su Python **2.7**. Nada más: ni paquetes de Python, ni permisos de administrador. |
| **También** | ArcGIS Pro 2.x/3.x (Python 3). El mismo código sirve para las dos. |
| **Origen** | File Geodatabase, o geodatabase corporativa con ArcSDE — probado contra el modelo de **Oracle 11gR2**. |
| **Para automatizar sin ArcGIS** | Python 2.7 o 3.x. Con GDAL instalado se puede leer una File Geodatabase en modo solo lectura. |
| **En el dispositivo** | QField 2.x (Android, iOS, Windows, Linux, macOS). |

La ventana usa **Tkinter**, que viene incluido en el Python que instala ArcGIS.
Esa es toda la razón de la elección: cero instalaciones.

### Python 2.7 no es una nota al pie

ArcMap 10.x ejecuta Python 2.7 y no va a cambiar. Una sola *f-string*, una
anotación de tipo o un `super()` sin argumentos y el programa deja de arrancar
—no aquí, sino en el equipo del técnico—. Por eso hay una prueba que recorre
**todo el árbol** y falla si aparece sintaxis que 2.7 no sabe leer, o un import
de un módulo que solo existe en Python 3 sin respaldo.

Lo mismo con `arcpy`: hay llamadas que solo existen en ArcGIS Pro
(`arcpy.FromWKT`, el módulo `arcpy.management`). El código usa la forma que
funciona en las dos versiones (`arcpy.Delete_management`), y otra prueba lo
vigila. Cuando algo solo existe en una versión —`arcpy.mp` en Pro,
`arcpy.mapping` en ArcMap— se pregunta antes de usarlo.

## Instalación

1. Copie la carpeta completa a una ruta accesible, por ejemplo `C:\SIG\qfieldesri`.
2. Doble clic en **`QFieldESRI.bat`** (o en `QFieldESRI.py`).

No hay instalador ni registro en el sistema. Si ArcGIS está en una ruta poco
habitual y el lanzador no lo encuentra, defina la variable de entorno
`QFIELDESRI_PYTHON` con la ruta completa de `python.exe`; el propio mensaje de
error lo explica.

Para usarlo además dentro de ArcGIS: panel **Catálogo** → **Conectar a
carpeta** → elija esa ruta y abra `QFieldESRI.pyt`.

## La aplicación, paso a paso

### 1 · Geodatabase

Se elige la `.gdb` o la conexión `.sde` y se pulsa **Abrir y analizar**. El
programa inventaría clases, dominios, subtipos y relaciones, y avisa de lo que
puede dar problemas (clases sin GlobalID, dominios que cambian según el
subtipo, capas demasiado grandes…). **No modifica nada.**

### 2 · Exportar a QField

Se elige el ámbito, los valores, cómo se verá en el dispositivo (ver
[simbología](#cómo-se-ve-en-el-dispositivo-la-simbología)), la carpeta de salida
y el nombre del proyecto. Produce una carpeta autocontenida:

```
mi_proyecto/
├── mi_proyecto.qgs             archivo de proyecto que abre QField
├── data.gpkg                   todos los datos
├── DCIM/ audio/ video/ files/  adjuntos que se capturen en campo
└── qfieldesri_manifest.json    cómo volver a la geodatabase
```

Se copia la carpeta completa al dispositivo (cable, tarjeta o QFieldCloud) y se
abre el proyecto desde QField.

### 3 · Traer de campo

Se apunta a la carpeta que vuelve del dispositivo. **Comparar** enumera altas,
modificaciones, bajas y conflictos sin tocar nada; **Aplicar** los escribe.

- Las **bajas** hechas en campo no se aplican salvo que se marque la casilla.
- Un **conflicto** (el registro también cambió en la geodatabase) no se aplica
  solo: queda en la lista para que decida una persona.
- Tras sincronizar, **vuelva a ejecutar el trace de ArcFM**: los campos
  `ParentCircuitSourceGUID` los calcula el trazado, no la captura.

## Línea de comandos

Ejecute con el Python de ArcGIS para disponer de `arcpy`:

```bat
set PY="C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"

REM inventario y verificación
%PY% -m qfieldesri analizar --gdb C:\datos\GYE.gdb

REM qué alimentadores hay para elegir
%PY% -m qfieldesri ambitos --gdb C:\datos\GYE.gdb --ambito alimentador

REM empaquetar un alimentador
%PY% -m qfieldesri empaquetar ^
     --gdb C:\datos\GYE.gdb ^
     --salida C:\salida --nombre alimentador_04BH ^
     --ambito alimentador --valores 04BH070T11 ^
     --foto EstructuraSoporte:FOTO

REM empaquetar una subestación completa
%PY% -m qfieldesri empaquetar --gdb C:\datos\GYE.gdb --salida C:\salida ^
     --nombre se_samanes --ambito subestacion --valores 04SM32

REM empaquetar el sector delimitado por un polígono
%PY% -m qfieldesri empaquetar --gdb C:\datos\GYE.gdb --salida C:\salida ^
     --nombre sector_norte --ambito poligono ^
     --poligono SECTORES --poligono-donde "CODIGO = 'N-12'"

REM simular el regreso, luego aplicarlo
%PY% -m qfieldesri sincronizar C:\salida\alimentador_04BH --informe informe.json
%PY% -m qfieldesri sincronizar C:\salida\alimentador_04BH --aplicar

REM publicar en la nube
%PY% -m qfieldesri publicar C:\salida\alimentador_04BH ^
     --proyecto alimentador-04BH --usuario mi.usuario
```

Para repetir el mismo empaquetado cada mes, guarde la configuración una vez y
reutilícela:

```bat
%PY% -m qfieldesri configurar --gdb C:\datos\GYE.gdb --salida ruta.json
REM edite ruta.json a mano si hace falta
%PY% -m qfieldesri empaquetar --config ruta.json
```

### Verlo funcionando sin ArcGIS

```bash
python -m qfieldesri demo --salida /tmp
```

Genera un paquete real con un fragmento del modelo eléctrico (poste, tramo de
media tensión con subtipos, puesto de transformación con sus transformadores y
la tabla de alimentador cabecera). Sirve para ver el resultado antes de conectar
la geodatabase de producción.

## Geodatabase corporativa: Oracle 11gR2 con ArcSDE

Todo lo anterior funciona igual pasando la conexión `.sde` en vez de la `.gdb`.
El origen es intercambiable: **la misma exportación, el mismo proyecto de QField
y la misma vuelta**, contra una File Geodatabase o contra la corporativa.

### Lo que Oracle cambia: cómo se llaman las clases

En una File Geodatabase la clase es `EstructuraSoporte`. La misma clase en
Oracle con ArcSDE llega como **`GYE.ESTRUCTURASOPORTE`**: en mayúsculas, porque
así la guarda Oracle, y calificada con el usuario propietario del esquema. Con
otra conexión, la misma clase llega como `SDE.ESTRUCTURASOPORTE`.

Eso no cambia la clase, cambia la etiqueta. qfieldESRI compara **clases**, no
cadenas, así que:

- el perfil del modelo reconoce `GYE.BARRA` igual que `Barra`;
- en la configuración, en `--solo` y en el archivo de estilo se escribe el
  nombre corto (`Barra`) aunque el servidor la llame de otro modo;
- las tablas del GeoPackage pierden el esquema: en el dispositivo se ve
  `ESTRUCTURASOPORTE`, no `GYE.ESTRUCTURASOPORTE`;
- **un paquete generado con una conexión se puede sincronizar con otra**, que
  es lo normal cuando se empaqueta en campo y se aplica desde la oficina.

### La sesión de edición, que es donde ArcGIS no perdona

`arcpy.da.Editor.startEditing(with_undo, multiuser_mode)` necesita saber si los
datos están **registrados como versionados**. Acertar no es opcional: editar
una clase no versionada en modo versionado —o al revés— no da un aviso, da un
error. qfieldESRI lo deduce de la propia geodatabase (`Describe.isVersioned`) y
la verificación previa lo dice antes de empezar:

| Situación | Qué hace | Qué le toca a usted |
|---|---|---|
| Clases **versionadas** | Sesión versionada, con deshacer | Reconciliar y publicar (*Reconcile / Post*) para que lo capturado llegue a `DEFAULT` |
| Clases **no versionadas** | Sesión no versionada | Respaldar antes: se escribe directo en las tablas base, sin versión que revisar |
| File Geodatabase | Sesión normal | Nada |

Se escribe en la **versión a la que apunte el archivo de conexión**: apunte a la
versión de trabajo que corresponda, no a `SDE.DEFAULT`.

### El resto de lo que cambia por dentro

- un fallo puntual (un registro bloqueado por otro editor, un valor que el
  dominio rechaza) se anota en el informe y **no tumba el lote**; si la base
  rechaza cerrar la sesión, no queda nada aplicado y se dice con esas palabras;
- las listas de valores del ámbito se trocean en bloques de 900 para no
  reventar el `IN` de Oracle, que corta en 1000;
- el recorte por área de interés se resuelve con el índice espacial del
  servidor;
- una clase ilegible (permisos, bloqueo de esquema) no tumba el análisis: se
  avisa y se sigue;
- las capas llevadas como **contexto de solo lectura** nunca se escriben de
  vuelta, ni aunque el GeoPackage vuelva modificado.

## Recomendaciones para el modelo CNEL EP

- **Active GlobalIDs** en las clases que vayan a campo (*Datos → Administrar →
  Añadir GlobalIDs*). Sin ellos la sincronización se ata a `OBJECTID`, que puede
  cambiar si la clase se comprime o se reconstruye. La verificación lo avisa.
- **Exporte por alimentador**, no la Unidad de Negocio entera: el paquete baja
  de gigabytes a decenas de megabytes.
- **Revise los avisos de dominio por subtipo.** Cuando un campo usa dominios
  distintos según el subtipo (`VOLTAJE` en `Barra`: BT / MT / AT), QField ofrece
  la unión de todos: hay que validar después que el valor corresponda al subtipo
  del registro.
- **Digitalice siempre de la fuente hacia la carga.** Un tramo dibujado al revés
  no lo reconoce el trace de ArcGIS, y qfieldESRI no puede corregirlo por usted.

## Estructura del proyecto

```
QFieldESRI.py / .bat        aplicación de escritorio (doble clic)
QFieldESRI.pyt              caja de herramientas de ArcGIS
qfieldesri/
├── app.py      ventana de escritorio (Tkinter)
├── launcher.py localiza el Python de ArcGIS y arranca la aplicación
├── cli.py      línea de comandos
├── core/       metadatos, nombres de clase, configuración, ámbito,
│               empaquetado, verificación, sincronización, adjuntos,
│               QFieldCloud
├── readers/    arcpy (File GDB y SDE), OGR, memoria
├── writers/    GeoPackage y archivo de proyecto de QField
├── symbology/  modelo neutro de símbolos, lector de .lyrx (CIM), lector de
│               MXD/.lyr con arcpy, archivo de estilo y estilos automáticos
├── profiles/   curaduría del modelo (cnel_ep.json, cnel_ep.estilo.json,
│               genérico)
├── utils/      WKB, huellas de entidad, funciones ST_* para SQLite
└── demo.py     geodatabase de ejemplo en memoria, local y corporativa
docs/modelo/    catálogo del modelo eléctrico CNEL EP (origen del perfil)
tools/          generador del perfil desde el catálogo
tests/          316 pruebas que corren sin ArcGIS
```

`arcpy` solo se importa en su lector, en el lanzador, en la caja de
herramientas y —de forma perezosa, dentro de la función que lo usa— en la
aplicación y en el módulo de adjuntos. Por eso todo el núcleo se puede probar y
automatizar fuera de ArcGIS.

## Pruebas

```bash
cd qfieldesri
python -m unittest discover -s tests -t .
```

316 pruebas, sin ArcGIS ni ningún otro software instalado. Cubren el contenedor
GeoPackage, la normalización de WKB, el archivo de proyecto, el perfil, el
ámbito de exportación, la simbología (lectura de CIM, archivo de estilo,
precedencia entre fuentes y serialización), el empaquetado completo, los
adjuntos, el ciclo de vuelta con detección de conflictos, la caja de
herramientas (con `arcpy` simulado), la aplicación, el camino completo contra
una geodatabase corporativa con nombres calificados de Oracle, y los guardias
de dependencias, de sintaxis 2.7 y de superficie de `arcpy`.

## Actualizar el perfil del modelo

Si cambia el catálogo del modelo en `docs/modelo/`:

```bash
python tools/build_profile.py
```

Regenera `qfieldesri/profiles/cnel_ep.json` (47 clases, 79 relaciones, 1 981
campos categorizados). El archivo se versiona; no se regenera en cada
empaquetado.

## Limitaciones conocidas

- No genera mapas base en mbtiles; sí puede referenciar uno existente.
- De un **MXD o un `.lyr` en ArcMap**, ArcGIS solo publica la clasificación, no
  los colores: de esa vía sale la estructura de la simbología con la paleta de
  qfieldESRI. Para trasladar los colores exactos, exporte las capas como
  `.lyrx` desde ArcGIS Pro (o use el archivo de estilo).
- No se trasladan los símbolos de fuente ni las imágenes de marcador de ArcGIS:
  se sustituyen por la forma geométrica más parecida y se avisa.
- No replica la red geométrica ni los auto-actualizadores de ArcFM. Los campos
  de conectividad viajan de solo lectura y el trace se vuelve a correr en ArcGIS
  después de sincronizar.
- Los valores M se conservan solo si la entidad no se edita en campo.
- El lector de GDAL no ve subtipos y no puede escribir de vuelta: para
  sincronizar hace falta `arcpy`.

## Licencia

GPL v2 o posterior.
