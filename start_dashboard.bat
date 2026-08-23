@echo off
echo ========================================
echo   VASILY AI DASHBOARD
echo ========================================
echo.

REM Переходим в папку проекта
cd /d "%~dp0"

REM Проверяем, что Streamlit установлен
if not exist ".venv\Scripts\streamlit.exe" (
    echo [ОШИБКА] Streamlit не найден в виртуальном окружении
    echo Установите: uv add streamlit
    pause
    exit /b 1
)

REM Запускаем PowerShell, активируем venv и запускаем дашборд
powershell.exe -ExecutionPolicy Bypass -Command "& { .\.venv\Scripts\Activate.ps1; streamlit run ui/dashboard.py }"

echo.
echo ========================================
echo   Дашборд завершил работу
echo ========================================
pause
