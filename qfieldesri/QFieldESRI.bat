@echo off
REM Abre qfieldESRI con el Python de ArcGIS.
REM
REM Si ArcGIS esta instalado en una ruta poco habitual, defina la variable
REM QFIELDESRI_PYTHON con la ruta completa de python.exe antes de llamar a este
REM archivo, o aqui mismo:
REM   set QFIELDESRI_PYTHON=C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe

setlocal
set "AQUI=%~dp0"

REM 1) el interprete que el usuario haya indicado
if defined QFIELDESRI_PYTHON if exist "%QFIELDESRI_PYTHON%" (
    "%QFIELDESRI_PYTHON%" "%AQUI%QFieldESRI.py" %*
    goto :fin
)

REM 2) ArcGIS Pro
set "PRO=C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
if exist "%PRO%" (
    "%PRO%" "%AQUI%QFieldESRI.py" %*
    goto :fin
)

REM 3) ArcMap 10.x (el lanzador afina la version concreta)
for /d %%D in ("C:\Python27\ArcGIS*") do (
    if exist "%%D\python.exe" (
        "%%D\python.exe" "%AQUI%QFieldESRI.py" %*
        goto :fin
    )
)

REM 4) cualquier Python del PATH: el lanzador buscara el de ArcGIS
python "%AQUI%QFieldESRI.py" %*

:fin
if errorlevel 1 pause
endlocal
