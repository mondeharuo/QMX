#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif
#ifndef PackageDir
  #error PackageDir must point to the staged Windows distribution directory.
#endif

[Setup]
AppId={{AF8AADE0-7F89-4A81-AC36-C818E3943C29}
AppName=QMX
AppVersion={#AppVersion}
AppPublisher=QMX contributors
DefaultDirName={localappdata}\Programs\QMX
DefaultGroupName=QMX
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=QMX-Setup-x64-v{#AppVersion}
SetupIconFile={#PackageDir}\docs\assets\qmx-icon.ico
UninstallDisplayIcon={app}\QMX.exe
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ChangesAssociations=no
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\QMX"; Filename: "{app}\QMX.exe"
Name: "{autodesktop}\QMX"; Filename: "{app}\QMX.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\QMX.exe"; Description: "Launch QMX"; Flags: postinstall nowait skipifsilent
