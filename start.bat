@echo off
rem GPO Fishing Macro launcher - double-click me.
rem Starts the C# interface, which runs the Python engine as a child process.
rem Uses the project venv (creating it on first run), never the system Python.
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

rem A downloaded release ships the built window in MacroUI\; a source checkout
rem has it under ui-csharp\bin\ once "dotnet build" has run.
set "UI=MacroUI\MacroUI.exe"
if not exist "%UI%" set "UI=ui-csharp\bin\Release\net8.0-windows\MacroUI.exe"
if not exist "%UI%" set "UI=ui-csharp\bin\Debug\net8.0-windows\MacroUI.exe"
if not exist "%UI%" (
    echo The interface has not been built yet. Build it once with:
    echo     dotnet build ui-csharp -c Release
    echo Needs the .NET 8 SDK: https://dotnet.microsoft.com/download/dotnet/8.0
    pause
    exit /b 1
)

start "" "%UI%"
endlocal
