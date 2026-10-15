#define AppVersion "2026.10.7"
#define AppName "学习罗盘"

[Setup]
AppId={{AFC764B4-94E8-4F93-A358-62B12B2794C2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=doubleadd111
AppPublisherURL=https://github.com/doubleadd111/ai-learning-compass
DefaultDirName={localappdata}\Programs\LearningCompass
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
OutputBaseFilename=LearningCompass-{#AppVersion}-windows-x64-setup
SetupIconFile=..\build\compass.ico
UninstallDisplayIcon={app}\LearningCompass.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
InfoAfterFile=..\docs\installer-note.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\LearningCompass\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\LearningCompass.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LearningCompass.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LearningCompass.exe"; Description: "Open Learning Compass"; Flags: nowait postinstall skipifsilent
