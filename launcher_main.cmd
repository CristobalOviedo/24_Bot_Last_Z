@echo off
setlocal

REM Ruta del repo
set "WS=D:\Cristobal\OneDrive - Universidad de Chile\24_Bot_Last_Z"

REM Rutas de conda (segun tu comando ejemplo)
set "CONDA_ROOT=C:\Users\crist\anaconda3"
set "CONDA_ACT=%CONDA_ROOT%\Scripts\activate.bat"
set "ENV_NAME=lastZ"

REM Comando de activacion de conda + entorno (sin comillas escapadas)
set "ACTIVATE=call %CONDA_ACT% %CONDA_ROOT% && conda activate %ENV_NAME%"

REM --- Ventana 1: LOG del bot (ajusta args a tu flujo)
start "LOG Bot" cmd /k "cd /d %WS% && %ACTIVATE% && python -m lastz_bot.cli --pending --pause-key p"

REM --- Ventana 2: Monitor
start "Monitor" cmd /k "cd /d %WS% && %ACTIVATE% && python monitor.py"

endlocal