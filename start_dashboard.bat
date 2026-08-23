@echo off
echo ========================================
echo   Vasily AI Dashboard
echo ========================================
echo.
cd /d "%~dp0"
call .venv\Scripts\Activate.ps1
streamlit run ui/dashboard.py
pause
