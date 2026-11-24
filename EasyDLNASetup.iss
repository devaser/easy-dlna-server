; SPDX-License-Identifier: GPL-3.0-or-later
; Copyright (C) 2026 devaser (https://github.com/devaser)
;
; Easy DLNA Server - Inno Setup script (single-file installer).
;
; Build (run in the folder that contains this file):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" EasyDLNASetup.iss
; Output:
;   EasyDLNASetup.exe
;
; Needed next to this file at build time:
;   dist\EasyDLNAService.exe
;   dist\EasyDLNATray.exe
;   dist\EasyDLNAScanner.exe
;   dist\EasyDLNASettings.exe
;   LICENSE
;   THIRD_PARTY_NOTICES.md
;
; Install with a log (useful if something fails):
;   EasyDLNASetup.exe /LOG="install_inno.log"
;
; Silent install, skipping the firewall exceptions:
;   EasyDLNASetup.exe /VERYSILENT /TASKS="!firewall"
;
; What it does
;   Install / upgrade
;     1. Closes Tray, Settings and Scanner (they lock the executables).
;     2. If the service exists: stops it, waits until its process is gone
;        (kills it after 60 s), deletes it and waits until it has disappeared.
;        If it does not exist, it just continues.
;     3. Copies the files and prepares the data folder in %ProgramData%.
;     4. Installs the service and sets its SID type and permissions.
;     5. Optional task "firewall" (selected by default): creates the firewall
;        rules for UDP 1900 and TCP 8200, only for this program and this
;        service, local subnet only, Private, Domain and Public profiles.
;     6. Starts the service.
;   Uninstall
;     Closes the apps, stops and deletes the service, removes the firewall
;     rules and the files. Data in %ProgramData% is never touched.
;
; Requires Inno Setup 6.3 or later (x64compatible). With an older 6.x version,
; replace x64compatible with x64.
; Keep this file ASCII only, or save it as UTF-8 with BOM.

#define MyAppName "Easy DLNA Server"
#define MyAppVersion "0.3.3"
#define MyAppPublisher "devaser"
#define MyAppURL "https://github.com/devaser/easy-dlna-server"

[Setup]
; Unique application ID: never reuse it for a different product.
AppId={{B1F4C7A2-5D3E-4A89-8E26-9C0D7F3A1B64}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\Easy DLNA Server
DefaultGroupName=Easy DLNA Server
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=.
OutputBaseFilename=EasyDLNASetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\EasyDLNATray.exe
; The apps are closed by the code below, not by the Restart Manager prompt.
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "firewall"; Description: "Allow Easy DLNA Server through Windows Firewall (UDP 1900 and TCP 8200 by default, local network only)"; GroupDescription: "Network access:"

[Files]
Source: "dist\EasyDLNAService.exe";  DestDir: "{app}"; Flags: ignoreversion
Source: "dist\EasyDLNATray.exe";     DestDir: "{app}"; Flags: ignoreversion
Source: "dist\EasyDLNAScanner.exe";  DestDir: "{app}"; Flags: ignoreversion
Source: "dist\EasyDLNASettings.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE";                   DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md";    DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; Data folder shared by the service (runs as SYSTEM) and by the tray, settings
; and scanner (run as the logged-in user). Without this, the user-level apps
; cannot write the configuration or the library database that the service
; created. It is never removed on uninstall.
Name: "{commonappdata}\Easy DLNA Server"; Permissions: users-modify; Flags: uninsneveruninstall

[Icons]
Name: "{group}\Easy DLNA Server";   Filename: "{app}\EasyDLNATray.exe"
Name: "{group}\Easy DLNA Settings"; Filename: "{app}\EasyDLNASettings.exe"
; Start the tray automatically when any user logs in.
Name: "{commonstartup}\Easy DLNA Tray"; Filename: "{app}\EasyDLNATray.exe"

[Run]
; Launch the tray as the user who started the installer (not elevated).
Filename: "{app}\EasyDLNATray.exe"; Description: "Start Easy DLNA Tray"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
const
  ServiceName      = 'EasyDLNAServer';
  ServiceProcess   = 'EasyDLNAService.exe';
  FirewallRuleSsdp = 'EasyDLNA_SSDP';
  FirewallRuleHttp = 'EasyDLNA_HTTP';
  // Default service permissions, plus start/stop for interactive users (tray).
  ServiceSddl      = 'D:(A;;CCLCSWRPWPLOCRRC;;;SY)(A;;CCDCLCSWRPWPLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)(A;;RPWP;;;IU)';

var
  Problems: String;

function ScExe: String;
begin
  Result := ExpandConstant('{sys}\sc.exe');
end;

function NetshExe: String;
begin
  Result := ExpandConstant('{sys}\netsh.exe');
end;

function TaskkillExe: String;
begin
  Result := ExpandConstant('{sys}\taskkill.exe');
end;

function CmdExe: String;
begin
  Result := ExpandConstant('{cmd}');
end;

{ Runs a program, waits for it and returns its exit code (-1 if it could not start). }
function RunTool(const FileName, Params: String): Integer;
var
  ResultCode: Integer;
begin
  if Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode
  else
    Result := -1;
  Log(FileName + ' ' + Params + ' -> ' + IntToStr(Result));
end;

procedure Require(const What: String; ExitCode: Integer);
begin
  if ExitCode <> 0 then
    Problems := Problems + '- ' + What + ' (exit code ' + IntToStr(ExitCode) + ')' + #13#10;
end;

function ServiceExists: Boolean;
begin
  Result := RunTool(ScExe, 'query ' + ServiceName) = 0;
end;

function ServiceProcessRunning: Boolean;
begin
  Result := RunTool(CmdExe,
    '/C tasklist /FI "IMAGENAME eq ' + ServiceProcess + '" /NH | find /I "' + ServiceProcess + '" >nul') = 0;
end;

procedure CloseUserApps;
begin
  { Returns an error when nothing is running: that is fine. }
  RunTool(TaskkillExe, '/F /T /IM EasyDLNATray.exe /IM EasyDLNASettings.exe /IM EasyDLNAScanner.exe');
end;

{ Stops and deletes the service if it exists; does nothing if it does not. }
procedure RemoveService;
var
  I: Integer;
begin
  if ServiceExists then
  begin
    RunTool(ScExe, 'stop ' + ServiceName);
    for I := 1 to 60 do
    begin
      if not ServiceProcessRunning then
        Break;
      Sleep(1000);
    end;
  end;

  { Last resort: the process is still alive (or it runs without the service). }
  if ServiceProcessRunning then
    RunTool(TaskkillExe, '/F /T /IM ' + ServiceProcess);

  if ServiceExists then
  begin
    RunTool(ScExe, 'delete ' + ServiceName);
    for I := 1 to 30 do
    begin
      if not ServiceExists then
        Break;
      Sleep(1000);
    end;
  end;
end;

procedure RemoveFirewallRules;
begin
  { netsh reports an error when the rule does not exist: that is fine. }
  RunTool(NetshExe, 'advfirewall firewall delete rule name=' + FirewallRuleSsdp);
  RunTool(NetshExe, 'advfirewall firewall delete rule name=' + FirewallRuleHttp);
end;

{ Registers the service using the executable itself. Tries the usual forms of the
  command line; if all of them fail, captures the output to show the reason. }
procedure InstallService;
var
  Exe, LogFile: String;
  Done: Boolean;
  Output: AnsiString;
begin
  Exe := ExpandConstant('{app}') + '\' + ServiceProcess;

  Done := RunTool(Exe, '--startup auto install') = 0;
  if not Done then
    Done := RunTool(Exe, 'install --startup auto') = 0;
  if not Done then
    Done := RunTool(Exe, 'install') = 0;

  if not Done then
  begin
    LogFile := ExpandConstant('{tmp}\service-install.log');
    DeleteFile(LogFile);
    RunTool(CmdExe, '/S /C ""' + Exe + '" install > "' + LogFile + '" 2>&1"');
    Output := '';
    LoadStringFromFile(LogFile, Output);
    Problems := Problems + '- The Windows service could not be installed.' + #13#10;
    if Trim(String(Output)) <> '' then
      Problems := Problems + '  Program output: ' + Trim(String(Output)) + #13#10;
  end;
end;

{ Delayed automatic start: the service starts shortly after the network is
  ready. Starting at the very beginning of the boot can leave the SSDP and
  HTTP servers advertising a wrong or missing IP address. }
procedure ConfigureService;
begin
  Require('sc config', RunTool(ScExe, 'config ' + ServiceName + ' start= delayed-auto'));
  Require('sc sidtype', RunTool(ScExe, 'sidtype ' + ServiceName + ' unrestricted'));
  Require('sc sdset', RunTool(ScExe, 'sdset ' + ServiceName + ' ' + ServiceSddl));
end;

{ Rules limited to this program and this service, local subnet only,
  Private, Domain and Public profiles. }
procedure ConfigureFirewall;
var
  ExePath: String;
begin
  ExePath := ExpandConstant('{app}') + '\' + ServiceProcess;

  RemoveFirewallRules;

  Require('Firewall rule for UDP 1900',
    RunTool(NetshExe,
      'advfirewall firewall add rule name=' + FirewallRuleSsdp + ' dir=in action=allow program="' + ExePath +
      '" service=' + ServiceName +
      ' protocol=UDP localport=1900 remoteip=localsubnet profile=private,domain,public'));

  Require('Firewall rule for TCP 8200',
    RunTool(NetshExe,
      'advfirewall firewall add rule name=' + FirewallRuleHttp + ' dir=in action=allow program="' + ExePath +
      '" service=' + ServiceName +
      ' protocol=TCP localport=8200 remoteip=localsubnet profile=private,domain,public'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    { Before the files are copied. }
    CloseUserApps;
    RemoveService;
  end
  else if CurStep = ssPostInstall then
  begin
    { After the files are copied. }
    Problems := '';

    InstallService;
    ConfigureService;

    if WizardIsTaskSelected('firewall') then
      ConfigureFirewall
    else
      Log('Firewall exceptions were not selected: no rules created.');

    { Not fatal: the service starts automatically and the tray also starts it. }
    RunTool(ScExe, 'start ' + ServiceName);

    if Problems <> '' then
      SuppressibleMsgBox(
        'The installation finished, but there were problems:' + #13#10#13#10 + Problems,
        mbError, MB_OK, IDOK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { Before the files are removed. %ProgramData% is never touched. }
    CloseUserApps;
    RemoveService;
    RemoveFirewallRules;
  end;
end;
