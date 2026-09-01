# qfieldESRI

**Lleva una geodatabase de ESRI a QField y devuelve lo capturado en campo.**

Funciona sobre **ArcGIS Desktop** (ArcMap 10.x y ArcGIS Pro), con una **File
Geodatabase** o con una **geodatabase corporativa** (SDE), y está preparado para
el modelo de datos eléctrico homologado de **CNEL EP** (`MN-TEC-OPE-100`), sin
quedar atado a él.

Es la contraparte de [QFieldSync](https://github.com/opengisch/QFieldSync) para
el mundo ESRI: reutiliza su arquitectura y su vocabulario, pero parte de la
geodatabase en vez de un proyecto QGIS. El razonamiento completo de la
conversión está en [ANALISIS.md](ANALISIS.md).

---

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

## Requisitos

| | |
|---|---|
| **Para el uso normal** | ArcGIS Desktop 10.4+ o ArcGIS Pro 2.x/3.x (trae `arcpy`). Nada más: no hay que instalar paquetes de Python. |
| **Para automatizar sin ArcGIS** | Python 2.7 o 3.x. Con GDAL instalado se puede leer una File Geodatabase en modo solo lectura. |
| **En el dispositivo** | QField 2.x (Android, iOS, Windows, Linux, macOS). |

## Instalación

1. Copie la carpeta `qfieldesri/` completa a una ruta accesible, por ejemplo
   `C:\SIG\qfieldesri`.
2. En ArcGIS, panel **Catálogo** → **Conectar a carpeta** → elija esa ruta.
3. Abra **QFieldESRI.pyt**: aparecerán cinco herramientas.

No hay instalador ni registro en el sistema: el `.pyt` añade su propia carpeta
al `sys.path`, así que siempre usa la copia que tiene al lado.

## Uso desde ArcGIS

### 1 · Analizar geodatabase

Inventaría clases, dominios, subtipos y relaciones, y avisa de lo que puede dar
problemas. **No modifica nada.** Empiece siempre por aquí.

### 2 · Empaquetar para QField

| Parámetro | Para qué sirve |
|---|---|
| Geodatabase de origen | La `.gdb` o la conexión `.sde` |
| Carpeta de salida y nombre | Dónde y con qué nombre se crea el paquete |
| Perfil | `cnel_ep` para el modelo eléctrico, `generico` para cualquier otro |
| Clases a incluir | Vacío = todas. Las tablas relacionadas se arrastran solas |
| Clases de solo consulta | Capas de contexto que no se editan en campo |
| Filtros por clase | Cláusula `WHERE`, p. ej. `ALIMENTADORID = '04BH070T11'` |
| Campos de fotografía | Convierte un campo de texto en cámara + galería |
| Área de interés | Una capa de polígonos: solo se lleva lo que la interseca |

Produce una carpeta autocontenida:

```
mi_proyecto/
├── mi_proyecto.qgs             proyecto que abre QField
├── data.gpkg                   todos los datos
├── DCIM/ audio/ video/ files/  adjuntos que se capturen en campo
└── qfieldesri_manifest.json    cómo volver a la geodatabase
```

Cópiela al dispositivo (cable, tarjeta o QFieldCloud) y abra el `.qgs` desde
QField.

### 3 · Sincronizar desde QField

Apunte a la carpeta que vuelve del dispositivo. **Por omisión solo simula**:
enumera altas, modificaciones, bajas y conflictos sin tocar nada. Revise el
resultado y vuelva a ejecutar marcando *Aplicar los cambios*.

- Las **bajas** hechas en campo no se aplican salvo que se marque la casilla
  correspondiente.
- Un **conflicto** (el registro también cambió en la geodatabase) no se aplica
  solo: queda en el informe para que decida una persona.
- Tras sincronizar, **vuelva a ejecutar el trace de ArcFM**: los campos
  `ParentCircuitSourceGUID` los calcula el trazado, no la captura.

### 4 · Publicar en QFieldCloud · 5 · Recuperar de QFieldCloud

Suben y bajan el paquete de QFieldCloud, para trabajar sin cable. El manifiesto
no se sube (contiene rutas internas de la organización).

## Uso desde la línea de comandos

Ejecute con el Python de ArcGIS para disponer de `arcpy`:

```bat
set PY="C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"

REM inventario y verificación
%PY% -m qfieldesri analizar --gdb C:\datos\GYE.gdb

REM empaquetar un alimentador concreto
%PY% -m qfieldesri empaquetar ^
     --gdb C:\datos\GYE.gdb ^
     --salida C:\salida --nombre alimentador_04BH ^
     --filtro "TramoDistribucionAereo=ALIMENTADORID = '04BH070T11'" ^
     --foto EstructuraSoporte:FOTO

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
media tensión con subtipos, puesto de transformación y sus transformadores).
Ábralo con QGIS o con QField para ver el resultado antes de conectar la
geodatabase de producción.

## Geodatabase corporativa (SDE)

Todo lo anterior funciona igual pasando la conexión `.sde` en vez de la `.gdb`.
Lo que cambia por dentro:

- se detecta si el workspace está versionado y se avisa en la verificación;
- la escritura de vuelta se hace dentro de una sesión de edición con deshacer,
  de modo que un fallo a mitad revierte el lote completo;
- el recorte por área de interés se resuelve con el índice espacial del
  servidor;
- los nombres calificados (`GYE.SDE.Barra`) se normalizan al crear las tablas.

Se escribe en la **versión a la que apunte el archivo de conexión**: apunte a la
versión de trabajo que corresponda, no a `SDE.DEFAULT`, y concilie después con
las herramientas de ArcGIS.

## Recomendaciones para el modelo CNEL EP

- **Active GlobalIDs** en las clases que vayan a campo (*Datos → Administrar →
  Añadir GlobalIDs*). Sin ellos la sincronización se ata a `OBJECTID`, que puede
  cambiar si la clase se comprime o se reconstruye. La verificación lo avisa.
- **Empaquete por alimentador**, no la Unidad de Negocio entera: con un filtro
  `ALIMENTADORID` el paquete baja de gigabytes a decenas de megabytes.
- **Revise los avisos de dominio por subtipo.** Cuando un campo usa dominios
  distintos según el subtipo (`VOLTAJE` en `Barra`: BT / MT / AT), QField ofrece
  la unión de todos: hay que validar después que el valor corresponda al subtipo
  del registro.
- **Digitalice siempre de la fuente hacia la carga.** Un tramo dibujado al revés
  no lo reconoce el trace de ArcGIS, y qfieldESRI no puede corregirlo por usted.

## Estructura del proyecto

```
QFieldESRI.pyt              caja de herramientas de ArcGIS
qfieldesri/
├── core/       modelo de metadatos, configuración, empaquetado,
│               verificación, sincronización, adjuntos, QFieldCloud
├── readers/    arcpy (File GDB y SDE), OGR, memoria
├── writers/    GeoPackage y proyecto QGIS
├── profiles/   curaduría del modelo (cnel_ep.json, genérico)
├── utils/      WKB, huellas de entidad, funciones ST_* para SQLite
├── cli.py      línea de comandos
└── demo.py     geodatabase de ejemplo en memoria
docs/modelo/    catálogo del modelo eléctrico CNEL EP (origen del perfil)
tools/          generador del perfil desde el catálogo
tests/          133 pruebas que corren sin ArcGIS ni QGIS
```

Ningún módulo fuera de `readers/arcpy_reader.py` importa `arcpy`: por eso el
complemento se puede probar y automatizar fuera de ArcGIS.

## Pruebas

```bash
cd qfieldesri
python -m unittest discover -s tests -t .
```

## Actualizar el perfil del modelo

Si cambia el catálogo del modelo en `docs/modelo/`:

```bash
python tools/build_profile.py
```

Regenera `qfieldesri/profiles/cnel_ep.json` (47 clases, 79 relaciones, 1 981
campos categorizados). El archivo se versiona; no se regenera en cada
empaquetado.

## Limitaciones conocidas

- No genera mapas base en mbtiles (depende del motor de QGIS); sí puede
  referenciar uno existente.
- No traslada la simbología de ArcGIS/ArcFM: genera un renderizado propio por
  subtipo.
- No replica la red geométrica ni los auto-actualizadores de ArcFM. Los campos
  de conectividad viajan de solo lectura y el trace se vuelve a correr en ArcGIS
  después de sincronizar.
- Los valores M se conservan solo si la entidad no se edita en campo.
- El lector de GDAL no ve subtipos y no puede escribir de vuelta: para
  sincronizar hace falta `arcpy`.

## Licencia

GPL v2 o posterior, la misma que QFieldSync, cuya arquitectura y vocabulario
reutiliza este complemento.
