@echo off
echo ========================================
echo   VASILY AI AGENT
echo ========================================
echo.

REM Переходим в папку проекта
cd /d "%~dp0"

REM Запускаем PowerShell и внутри него активируем venv и запускаем агента
powershell.exe -ExecutionPolicy Bypass -Command "& { .\.venv\Scripts\Activate.ps1; python -m core.agent }"

echo.
echo ========================================
echo   Агент завершил работу
echo ========================================
pause
