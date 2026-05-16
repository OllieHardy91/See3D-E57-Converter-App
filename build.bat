@echo off
setlocal
cd /d "%~dp0"
echo -- See3D E57 Converter -- PyInstaller build --

REM Pick a Python launcher: prefer venv if present, then py -3.11, then python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
where py >nul 2>&1 && set "PY=py -3.11"

echo Using Python: %PY%
%PY% --version
if errorlevel 1 ( echo ERROR: Python not found on PATH & pause & exit /b 1 )

echo.
echo [1/4] Cleaning previous build artifacts...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo.
echo [2/4] Installing dependencies from requirements.txt...
%PY% -m pip install -r requirements.txt --quiet
if errorlevel 1 ( echo ERROR: pip install failed & pause & exit /b 1 )

echo.
echo [3/4] Generating multi-size ICO from dark 512px favicon...
%PY% -c "from PIL import Image; src=Image.open('assets/favicon-dark-512.png').convert('RGBA'); sizes=[(s,s) for s in (16,32,48,64,128,256)]; imgs=[src.resize(s,Image.LANCZOS) for s in sizes]; imgs[0].save('assets/app_icon.ico',format='ICO',sizes=sizes,append_images=imgs[1:])"
if errorlevel 1 ( echo WARNING: icon conversion failed, building without icon )

echo.
echo [4/4] Building .exe with PyInstaller (onefile, no UPX)...
%PY% -m PyInstaller --onefile --windowed --noconfirm --noupx ^
  --name "See3D_E57_Converter" ^
  --icon "assets\app_icon.ico" ^
  --add-data "assets;assets" ^
  --hidden-import numpy ^
  --hidden-import scipy ^
  --hidden-import scipy.spatial ^
  --hidden-import scipy.spatial.transform ^
  --hidden-import scipy.spatial.transform._rotation_groups ^
  --hidden-import cv2 ^
  --hidden-import pye57 ^
  --hidden-import tqdm ^
  --hidden-import tqdm.auto ^
  --hidden-import PIL ^
  --hidden-import PIL.Image ^
  --hidden-import customtkinter ^
  --hidden-import tkinterdnd2 ^
  --collect-data customtkinter ^
  --collect-data tkinterdnd2 ^
  --collect-data cv2 ^
  --collect-binaries cv2 ^
  --collect-binaries pye57 ^
  --exclude-module torch ^
  --exclude-module torchvision ^
  --exclude-module torchaudio ^
  --exclude-module tensorflow ^
  --exclude-module jax ^
  --exclude-module matplotlib ^
  --exclude-module IPython ^
  --exclude-module sklearn ^
  --exclude-module pandas ^
  --exclude-module pytest ^
  --exclude-module notebook ^
  app.py
if errorlevel 1 ( echo ERROR: PyInstaller build failed & pause & exit /b 1 )

echo.
echo -- BUILD COMPLETE --
echo See3D_E57_Converter.exe is in dist\
echo (First launch unpacks to %%TEMP%% and may take 5-15 seconds.)
pause
endlocal
