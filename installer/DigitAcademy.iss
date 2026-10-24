[Setup]
AppName=Digit Academy Management System
AppVersion=1.0.0
AppPublisher=Digit Academy Charitable Trust
DefaultDirName={autopf}\Digit Academy
DefaultGroupName=Digit Academy
OutputDir=..\Output
OutputBaseFilename=DigitAcademy_Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\DigitAcademy.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\DigitAcademy\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Digit Academy"; Filename: "{app}\DigitAcademy.exe"
Name: "{autodesktop}\Digit Academy"; Filename: "{app}\DigitAcademy.exe"

[Run]
Filename: "{app}\DigitAcademy.exe"; Description: "Launch Digit Academy"; Flags: nowait postinstall skipifsilent
; Student data lives in %LOCALAPPDATA%\DigitAcademy and is never touched by install/uninstall.
