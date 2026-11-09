@echo off
chcp 65001 >nul
rem 用法：
rem   set PFX_PATH=D:/certs/overtrans.pfx
rem   set PFX_PASS=密码
rem   signing\sign.bat dist\Overtrans\Overtrans.exe
if "%~1"=="" (
  echo 用法: sign.bat ^<要签名的文件...^>
  exit /b 1
)
if "%PFX_PATH%"=="" (
  echo 未设置 PFX_PATH 环境变量
  exit /b 1
)
where signtool >nul 2>nul
if errorlevel 1 (
  echo 找不到 signtool，请安装 Windows SDK 或从 Visual Studio 开发者命令行运行
  exit /b 1
)
signtool sign /f "%PFX_PATH%" /p "%PFX_PASS%" /fd SHA256 ^
  /tr http://timestamp.digicert.com /td SHA256 %*
if errorlevel 1 exit /b 1
echo 签名完成，开始校验...
signtool verify /pa %*
