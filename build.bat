@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [1/5] cleaning...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist release rmdir /s /q release

echo [2/5] building with PyInstaller (onedir)...
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name Overtrans ^
  --contents-directory runtime ^
  --icon "assets\overtrans.ico" ^
  --add-data "app\default_config.json;app" ^
  --add-data "assets;assets" ^
  --collect-all rapidocr ^
  --exclude-module matplotlib --exclude-module pandas --exclude-module scipy ^
  --exclude-module PyQt5 --exclude-module PySide2 --exclude-module torch ^
  main.py
if errorlevel 1 goto fail

echo [3/5] pruning unused models/dlls...
python tools\prune_dist.py dist\Overtrans
if errorlevel 1 goto fail

echo [4/5] copying docs...
copy /y README.md dist\Overtrans\README.md >nul
if exist dist\Overtrans\models echo   models/ 就绪
if not exist dist\Overtrans\logs mkdir dist\Overtrans\logs

echo [5/5] zipping...
mkdir release 2>nul
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Overtrans\*' -DestinationPath 'release\Overtrans-win64.zip' -Force"
if errorlevel 1 goto fail

echo.
echo Done: release\Overtrans-win64.zip
pause
exit /b 0

:fail
echo.
echo Build FAILED
pause
exit /b 1
