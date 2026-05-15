@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_ACTIVATE=%SCRIPT_DIR%.venv\Scripts\activate.bat"

if not exist "%VENV_ACTIVATE%" (
    echo Virtual environment activation script not found:
    echo %VENV_ACTIVATE%
    exit /b 1
)

if defined CONDA_SHLVL (
    for /L %%I in (1,1,%CONDA_SHLVL%) do call conda deactivate >nul 2>&1
)

endlocal & call "%VENV_ACTIVATE%"
