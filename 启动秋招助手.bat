@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
  py -3 app.py %*
  goto :done
)
python -c "import sys" >nul 2>nul
if not errorlevel 1 (
  python app.py %*
  goto :done
)

set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PY%" (
  "%CODEX_PY%" app.py %*
  goto :done
)

echo.
echo 启动失败：没有找到 Python 3。
echo 请安装 Python 3，或从 Codex 中启动本程序。
pause

:done
