@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo  Overtrans -^> GitHub 一键上传
echo ============================================
echo.

set OWNER=523343871
set REPO=Overtrans
set DESC=用于overfield快速识别翻译与回复小工具，更好的与你的国际小朋友交流

if not "%~1"=="" (
  set PAT=%~1
) else (
  echo 需要一个 Personal Access Token（不是账号密码，GitHub 早就不认密码了）
  echo 生成地址: https://github.com/settings/tokens/new  勾选 repo 权限
  echo.
  set /p PAT=粘贴 PAT 后回车:
)
if "%PAT%"=="" (
  echo 没有 PAT，退出
  pause & exit /b 1
)

echo.
echo [1/4] 创建仓库（已存在会跳过）
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$h=@{Authorization='token %PAT%';Accept='application/vnd.github+json'};" ^
  "try{ $r=Invoke-RestMethod -Uri 'https://api.github.com/user/repos' -Method Post -Headers $h -Body (@{name='%REPO%';description='%DESC%';private=$false;has_issues=$true;has_wiki=$false}|ConvertTo-Json); Write-Host '  已创建:' $r.html_url }" ^
  "catch{ Write-Host '  已存在或创建失败（继续推送）:' $_.Exception.Message }"

echo.
echo [2/4] 推送代码
git remote remove origin 2>nul
git remote add origin https://%OWNER%:%PAT%@github.com/%OWNER%/%REPO%.git
git branch -M main
git push -u origin main
if errorlevel 1 (
  echo 推送失败，检查 PAT 权限 / 网络 / 仓库名
  git remote set-url origin https://github.com/%OWNER%/%REPO%.git
  pause & exit /b 1
)
git remote set-url origin https://github.com/%OWNER%/%REPO%.git

echo.
echo [3/4] 写仓库简介和标签
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$h=@{Authorization='token %PAT%';Accept='application/vnd.github+json'};" ^
  "$body=@{description='%DESC%';homepage='';topics=@('ocr','translate','rapidocr','overfield','game-tools','windows');has_issues=$true}|ConvertTo-Json;" ^
  "try{ Invoke-RestMethod -Uri 'https://api.github.com/repos/%OWNER%/%REPO%' -Method Patch -Headers $h -Body $body | Out-Null;" ^
  " Invoke-RestMethod -Uri 'https://api.github.com/repos/%OWNER%/%REPO%/topics' -Method Put -Headers $h -Body (@{names=@('ocr','translate','rapidocr','overfield','game-tools','windows')}|ConvertTo-Json) | Out-Null;" ^
  " Write-Host '  简介与标签已写入' } catch { Write-Host '  更新简介失败:' $_.Exception.Message }"

echo.
echo [4/4] 完成
echo   仓库地址: https://github.com/%OWNER%/%REPO%
echo   接下来手动做三件事:
echo     1. Releases -^> Draft a new release -^> 上传 Overtrans-1.0.0-win64.zip
echo     2. 打 tag: git tag v1.0.0 ^&^& git push origin v1.0.0   （会自动触发 Actions 构建）
echo     3. Settings -^> 确认 Issues / Actions 已开启
echo.
echo 提示: PAT 用完后可以到 GitHub 设置里撤销，本地没有保存它。
pause
