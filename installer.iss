#define MyAppName "SEARLab RFID Lab System"
#define MyAppVersion "1.0"
#define MyAppExeName "SEARLab_RFID_App.exe"

[Setup]
AppId={{8F5B1F1E-6C2D-4B8A-9E3F-4B8A9E3F1C2D}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=SEARLab_RFID_Setup
Compression=lzma
SolidCompression=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\SEARLab_RFID_App\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\SEARLab RFID Dashboard"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\SEARLab RFID Dashboard"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch SEARLab RFID Dashboard now"; Flags: nowait postinstall skipifsilent
