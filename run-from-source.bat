@echo off
rem Developer launcher for a source checkout. If you just want to use the
rem macro, install it instead: toolsuild_installer.py builds a real
rem installer, and released builds are on the GitHub releases page.
rem
rem This creates the project venv if it is missing, installs requirements.txt
rem into it, and opens the window built by "dotnet build ui-csharp".
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Could not create a venv - is Python installed and on PATH?
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed - check your internet connection.
        pause
        exit /b 1
    )
)

rem A Release build targets win-x64 because it is published self-contained;
rem a plain Debug build does not.
set "UI=ui-csharp\bin\Release\net8.0-windows\win-x64\GPO Fishing Macro.exe"
if not exist "%UI%" set "UI=ui-csharp\bin\Release\net8.0-windows\GPO Fishing Macro.exe"
if not exist "%UI%" set "UI=ui-csharp\bin\Debug\net8.0-windows\GPO Fishing Macro.exe"
if not exist "%UI%" (
    echo The interface has not been built yet. Build it once with:
    echo     dotnet build ui-csharp -c Release
    echo Needs the .NET 8 SDK: https://dotnet.microsoft.com/download/dotnet/8.0
    pause
    exit /b 1
)

start "" "%UI%"
endlocal
