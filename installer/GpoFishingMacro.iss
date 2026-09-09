; Installer for GPO Fishing Macro.
;
; Per-user by design: it installs under %LocalAppData%\Programs, the same place
; Chrome and VS Code put a user install, so it never asks for administrator
; rights. Nothing is written outside the install folder except the Start Menu
; shortcut and the uninstall entry, and uninstalling removes both.

#define AppName        "GPO Fishing Macro"
#define AppVersion      "1.0.0"
#define AppPublisher    "ZeekyBlast"
#define AppExe          "GPO Fishing Macro.exe"
#define AppUrl          "https://github.com/ZeekyBlast/GPO-Fishing-Macro"

[Setup]
; Never reuse this GUID for a different program; it is how Windows recognises
; an upgrade of this one.
AppId={{7C4A1D2E-9B33-4F58-A6E1-2D5C8F0B71A4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup

DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=..\dist
OutputBaseFilename={#AppName} Setup {#AppVersion}
SetupIconFile=..\ui-csharp\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Say plainly what this is before anything is copied. A person is right to be
; wary of an unsigned installer, so the wizard tells them what it does and
; where it puts things rather than hiding it.
AppComments=Auto-fishing macro for Grand Piece Online.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\build\app\{#AppExe}";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\main.py";                    DestDir: "{app}"; Flags: ignoreversion
Source: "..\settings.example.json";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";                  DestDir: "{app}"; Flags: ignoreversion
Source: "..\gpo_macro\*.py";             DestDir: "{app}\gpo_macro"; Flags: ignoreversion
; The bundled interpreter and its packages: this is why nothing has to be
; installed or pip-ed by hand.
Source: "..\build\runtime\*";            DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}";  Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}";   Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Written by the app after it runs, so Setup does not know about them.
Type: files;      Name: "{app}\settings.json"
Type: files;      Name: "{app}\gpo_macro.log"
Type: files;      Name: "{app}\crash.log"
Type: files;      Name: "{app}\ui-crash.log"
Type: files;      Name: "{app}\test_detection.png"
Type: filesandordirs; Name: "{app}\templates"
Type: filesandordirs; Name: "{app}\captures"
Type: filesandordirs; Name: "{app}\gpo_macro\__pycache__"
Type: filesandordirs; Name: "{app}\runtime"
Type: dirifempty; Name: "{app}\gpo_macro"
Type: dirifempty; Name: "{app}"

[Code]
{ An upgrade cannot overwrite a running copy, and a macro that is mid-cast is
  holding the mouse button down. Ask rather than kill silently. }
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if CheckForMutexes('GpoFishingMacroRunning') then
  begin
    if MsgBox('GPO Fishing Macro is currently running. Close it and continue?',
              mbConfirmation, MB_YESNO) = IDYES then
      Exec('taskkill.exe', '/IM "{#AppExe}" /F', '', SW_HIDE,
           ewWaitUntilTerminated, ResultCode)
    else
      Result := False;
  end;
end;
