; Inno Setup 6 script — build Agent3D-Setup.exe from staged portable tree.
; Compile with: ISCC.exe Agent3D.iss
; Staging folder is produced by packaging/build.ps1  →  packaging/_stage/Agent3D

#define MyAppName "Agent3D"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Agent3D"
#define MyAppURL "https://github.com/"
; Prefer Chinese launcher when present; Start-Agent3D.bat remains as fallback in package
#define MyAppExeName "开始 Agent3D.bat"
#define StageDir "_stage\Agent3D"

#ifndef SourceStage
  #define SourceStage StageDir
#endif

[Setup]
AppId={{A3D7B2E1-0C4F-4A9A-9E6B-7D2F1C8A5B30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Agent3D-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\Start-Agent3D.bat
InfoBeforeFile=launcher\README.txt

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceStage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\开始 Agent3D.bat"; WorkingDir: "{app}"
Name: "{group}\停止 {#MyAppName}"; Filename: "{app}\停止 Agent3D.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\开始 Agent3D.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec
