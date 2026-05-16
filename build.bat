@echo off
cd /d "%~dp0"
echo ── See3D E57 Converter — PyInstaller build ──────────────────────────────

echo.
echo [1/3] Installing dependencies...
pip install customtkinter pillow pyinstaller numpy scipy opencv-python pye57 tqdm tkinterdnd2 --quiet
if errorlevel 1 ( echo ERROR: pip install failed & pause & exit /b 1 )

echo.
echo [2/3] Generating multi-size ICO from dark 512px favicon...
python -c "from PIL import Image; src=Image.open('assets/favicon-dark-512.png').convert('RGBA'); sizes=[(s,s) for s in (16,32,48,64,128,256)]; imgs=[src.resize(s,Image.LANCZOS) for s in sizes]; imgs[0].save('assets/app_icon.ico',format='ICO',sizes=sizes,append_images=imgs[1:])"
if errorlevel 1 ( echo WARNING: icon conversion failed, building without icon )

echo.
echo [3/3] Building .exe with PyInstaller...
pyinstaller --onefile --windowed ^
  --name "See3D_E57_Converter" ^
  --icon "assets\app_icon.ico" ^
  --add-data "assets;assets" ^
  --hidden-import numpy ^
  --hidden-import numpy.core ^
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
  --collect-data customtkinter ^
  --hidden-import tkinterdnd2 ^
  --collect-data tkinterdnd2 ^
  --exclude-module torch ^
  --exclude-module torchvision ^
  --exclude-module torchaudio ^
  --exclude-module tensorflow ^
  --exclude-module jax ^
  --exclude-module matplotlib ^
  --exclude-module IPython ^
  --exclude-module sklearn ^
  app.py
if errorlevel 1 ( echo ERROR: PyInstaller build failed & pause & exit /b 1 )

echo.
echo Copying .exe to project root...
copy /Y "dist\See3D_E57_Converter.exe" "..\See3D_E57_Converter.exe"
if errorlevel 1 ( echo ERROR: copy failed & pause & exit /b 1 )

echo.
echo ── BUILD COMPLETE ───────────────────────────────────────────────────────
echo See3D_E57_Converter.exe is in the project root.
pause
