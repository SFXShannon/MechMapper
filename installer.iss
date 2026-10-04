; Mech Mapper installer (Inno Setup 6).
; Built by build_installer.ps1, which passes the version: ISCC /DAppVersion=2.1.0 installer.iss
; Expects dist\MW5_MECHMAPPER.exe to exist (run build.bat first).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; AppId identifies the app for upgrades and uninstall - never change it.
AppId={{8C2F6E4B-5D1A-4F3E-9B7C-2A6D9E1F4B30}
AppName=Mech Mapper
AppVersion={#AppVersion}
AppVerName=Mech Mapper {#AppVersion}
AppPublisher=SFXShannon
AppPublisherURL=https://github.com/SFXShannon/MechMapper
AppSupportURL=https://github.com/SFXShannon/MechMapper/issues
AppUpdatesURL=https://github.com/SFXShannon/MechMapper/releases
AppCopyright=Copyright (c) 2026 SFXShannon. All rights reserved.
DefaultDirName={autopf}\Mech Mapper
DefaultGroupName=Mech Mapper
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=LICENSE.txt
OutputDir=dist
OutputBaseFilename=MechMapper-Setup-{#AppVersion}
SetupIconFile=mech_mapper.ico
UninstallDisplayIcon={app}\MW5_MECHMAPPER.exe
UninstallDisplayName=Mech Mapper
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Mech Mapper Setup
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; The app holds this mutex while running, so setup can ask the user to close it
AppMutex=MechMapperRunning
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\MW5_MECHMAPPER.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "vendor\ViGEmBus_1.22.0_x64_x86_arm64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsViGEmBus

[Icons]
Name: "{autoprograms}\Mech Mapper"; Filename: "{app}\MW5_MECHMAPPER.exe"
Name: "{autodesktop}\Mech Mapper"; Filename: "{app}\MW5_MECHMAPPER.exe"; Tasks: desktopicon

[Run]
Filename: "{tmp}\ViGEmBus_1.22.0_x64_x86_arm64.exe"; Parameters: "/exenoui /qn /norestart"; StatusMsg: "Installing the virtual controller driver (ViGEmBus)..."; Flags: waituntilterminated; Check: NeedsViGEmBus
; shellexec: the exe requires admin, and only ShellExecute can show the elevation prompt
Filename: "{app}\MW5_MECHMAPPER.exe"; Description: "{cm:LaunchProgram,Mech Mapper}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
Type: files; Name: "{app}\update.log"

[Code]
function NeedsViGEmBus: Boolean;
begin
  Result := not RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\ViGEmBus');
end;
