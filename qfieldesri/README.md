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
  formulario, para que el técnico no tenga que bajar por 41 campos.

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

## Requisitos

| | |
|---|---|
| **Para el uso normal** | ArcGIS Desktop 10.4+ o ArcGIS Pro 2.x/3.x. Nada más: ni paquetes de Python, ni permisos de administrador. |
| **Para automatizar sin ArcGIS** | Python 2.7 o 3.x. Con GDAL instalado se puede leer una File Geodatabase en modo solo lectura. |
| **En el dispositivo** | QField 2.x (Android, iOS, Windows, Linux, macOS). |

La ventana usa **Tkinter**, que viene incluido en el Python que instala ArcGIS.
Esa es toda la razón de la elección: cero instalaciones.

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

Se elige el ámbito, los valores, la carpeta de salida y el nombre del proyecto.
Produce una carpeta autocontenida:

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

## Geodatabase corporativa (SDE)

Todo lo anterior funciona igual pasando la conexión `.sde` en vez de la `.gdb`.
Lo que cambia por dentro:

- se detecta si el workspace está versionado y se avisa en la verificación;
- la escritura de vuelta se hace dentro de una sesión de edición con deshacer,
  de modo que un fallo a mitad revierte el lote completo;
- el recorte por área de interés se resuelve con el índice espacial del
  servidor;
- los nombres calificados (`GYE.SDE.Barra`) se normalizan al crear las tablas;
- las listas de valores del ámbito se trocean para no reventar el `IN` del
  gestor (Oracle corta en 1000).

Se escribe en la **versión a la que apunte el archivo de conexión**: apunte a la
versión de trabajo que corresponda, no a `SDE.DEFAULT`, y concilie después con
las herramientas de ArcGIS.

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
├── core/       metadatos, configuración, ámbito, empaquetado,
│               verificación, sincronización, adjuntos, QFieldCloud
├── readers/    arcpy (File GDB y SDE), OGR, memoria
├── writers/    GeoPackage y archivo de proyecto de QField
├── profiles/   curaduría del modelo (cnel_ep.json, genérico)
├── utils/      WKB, huellas de entidad, funciones ST_* para SQLite
└── demo.py     geodatabase de ejemplo en memoria
docs/modelo/    catálogo del modelo eléctrico CNEL EP (origen del perfil)
tools/          generador del perfil desde el catálogo
tests/          213 pruebas que corren sin ArcGIS
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

213 pruebas, sin ArcGIS ni ningún otro software instalado. Cubren el contenedor
GeoPackage, la normalización de WKB, el archivo de proyecto, el perfil, el
ámbito de exportación, el empaquetado completo, los adjuntos, el ciclo de vuelta
con detección de conflictos, la caja de herramientas (con `arcpy` simulado), la
aplicación y el guardia de dependencias.

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
- No traslada la simbología de ArcGIS/ArcFM: genera un renderizado propio por
  subtipo.
- No replica la red geométrica ni los auto-actualizadores de ArcFM. Los campos
  de conectividad viajan de solo lectura y el trace se vuelve a correr en ArcGIS
  después de sincronizar.
- Los valores M se conservan solo si la entidad no se edita en campo.
- El lector de GDAL no ve subtipos y no puede escribir de vuelta: para
  sincronizar hace falta `arcpy`.

## Licencia

GPL v2 o posterior.
