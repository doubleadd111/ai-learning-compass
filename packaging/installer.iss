#define AppVersion "2026.10.12"
#define AppName "学习罗盘"

[Setup]
#ifdef QAOutputDir
; Isolated installer QA must never register as or remove the user's real app.
AppId={{DB874C52-324E-4378-92A9-FD395B66E96F}
#else
AppId={{AFC764B4-94E8-4F93-A358-62B12B2794C2}
#endif
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
#ifdef QAOutputDir
OutputDir={#QAOutputDir}
#else
OutputDir=..\release
#endif
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
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "..\dist\LearningCompass\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
#ifndef QAOutputDir
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\LearningCompass.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LearningCompass.exe"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\LearningCompass.exe"; Description: "打开学习罗盘"; Flags: nowait postinstall skipifsilent
