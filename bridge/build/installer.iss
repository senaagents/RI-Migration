; Sena Tally Bridge — Inno Setup script.
;
; Wizard collects: Sena server URL + pairing code, optional Tally host/port.
; Writes %LOCALAPPDATA%\SenaTallyBridge\bridge-config.json from those inputs
; and registers a Scheduled Task at user logon that runs SenaTallyBridge.exe
; with --config pointing at that JSON.
;
; Build (on Windows, after pyinstaller has produced dist/SenaTallyBridge.exe):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" bridge\build\installer.iss
;
; Code-signing is intentionally NOT configured here. When a cert is on hand,
; uncomment SignTool below + add the corresponding Inno Setup signtool entry.

#define MyAppName        "Sena Tally Bridge"
#define MyAppVersion     "0.1.0"
#define MyAppPublisher   "Sena"
#define MyAppExeName     "SenaTallyBridge.exe"
#define MyAppTaskName    "SenaTallyBridge"

[Setup]
AppId={{A2F8E03B-7E1F-4F38-9CB1-3C9F1A7E8E10}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\SenaTallyBridge
DefaultGroupName={#MyAppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\..\..\..\dist-installer
OutputBaseFilename=SenaTallyBridge-Setup-{#MyAppVersion}
SetupIconFile=
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; SignTool=signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "register_task.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "unregister_task.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; Ensure the logs directory exists under the install root so the bridge
; never has to create it at runtime (avoids a permission edge case).
Name: "{app}\logs"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--config ""{app}\bridge-config.json"""
Name: "{group}\Reconfigure {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{group}\Stop {#MyAppName}"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Stop-ScheduledTask -TaskName '{#MyAppTaskName}' -ErrorAction SilentlyContinue; Get-Process -Name SenaTallyBridge -ErrorAction SilentlyContinue | Stop-Process -Force"""

[Run]
; Register the scheduled task immediately after install. Runs as the
; current user, triggered at logon, with --config pointing at the JSON
; we wrote in the Code section.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\register_task.ps1"" -ExePath ""{app}\{#MyAppExeName}"" -ConfigPath ""{app}\bridge-config.json"" -LogDir ""{app}\logs"" -TaskName ""{#MyAppTaskName}"""; \
  Flags: runhidden waituntilterminated
; Kick off the bridge once now so the user doesn't have to log out / in
; before the first sync.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Start-ScheduledTask -TaskName '{#MyAppTaskName}'"""; \
  Flags: runhidden nowait

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\unregister_task.ps1"" -TaskName ""{#MyAppTaskName}"""; \
  Flags: runhidden waituntilterminated; \
  RunOnceId: "UnregisterSenaBridgeTask"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\bridge-config.json"

; ── Custom wizard pages ────────────────────────────────────────────────────
[Code]
var
  ConfigPage: TInputQueryWizardPage;

procedure InitializeWizard();
begin
  ConfigPage := CreateInputQueryPage(
    wpSelectDir,
    'Sena Bridge configuration',
    'Tell the bridge how to reach Sena and Tally.',
    'These values are saved to bridge-config.json and can be edited later by reinstalling.');
  ConfigPage.Add('Sena server URL (e.g. https://app.senaagents.com)', False);
  ConfigPage.Add('Pairing code from the Sena migration page', False);
  ConfigPage.Add('Tally host (default: localhost)', False);
  ConfigPage.Add('Tally port (default: 9000)', False);

  // Pre-fill defaults
  ConfigPage.Values[2] := 'localhost';
  ConfigPage.Values[3] := '9000';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ServerUrl, PairingCode: string;
begin
  Result := True;
  if CurPageID = ConfigPage.ID then
  begin
    ServerUrl := Trim(ConfigPage.Values[0]);
    PairingCode := Trim(ConfigPage.Values[1]);
    if (Length(ServerUrl) = 0) or (Length(PairingCode) = 0) then
    begin
      MsgBox('Server URL and pairing code are required.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if (Pos('http://', LowerCase(ServerUrl)) <> 1) and (Pos('https://', LowerCase(ServerUrl)) <> 1) then
    begin
      MsgBox('Server URL must start with http:// or https://.', mbError, MB_OK);
      Result := False;
      exit;
    end;
  end;
end;

function JsonEscape(const S: string): string;
var
  I: Integer;
  C: Char;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    case C of
      '"':  Result := Result + '\"';
      '\':  Result := Result + '\\';
      #8:   Result := Result + '\b';
      #9:   Result := Result + '\t';
      #10:  Result := Result + '\n';
      #13:  Result := Result + '\r';
    else
      Result := Result + C;
    end;
  end;
end;

procedure WriteBridgeConfig();
var
  ConfigPath, Body, Server, Pairing, Host, Port, InstalledAt: string;
begin
  ConfigPath := ExpandConstant('{app}\bridge-config.json');
  Server := Trim(ConfigPage.Values[0]);
  Pairing := Trim(ConfigPage.Values[1]);
  Host := Trim(ConfigPage.Values[2]);
  Pairing := Pairing;
  if Length(Host) = 0 then Host := 'localhost';
  Port := Trim(ConfigPage.Values[3]);
  if Length(Port) = 0 then Port := '9000';
  InstalledAt := GetDateTimeString('yyyy-mm-dd"T"hh:nn:ss', '-', ':');

  Body :=
    '{' + #13#10 +
    '  "server": "' + JsonEscape(Server) + '",' + #13#10 +
    '  "pairing_code": "' + JsonEscape(Pairing) + '",' + #13#10 +
    '  "tally_host": "' + JsonEscape(Host) + '",' + #13#10 +
    '  "tally_port": ' + Port + ',' + #13#10 +
    '  "installed_at": "' + InstalledAt + '",' + #13#10 +
    '  "version": "{#MyAppVersion}"' + #13#10 +
    '}' + #13#10;

  if not SaveStringToFile(ConfigPath, Body, False) then
    RaiseException('Failed to write ' + ConfigPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // Write config just before [Run] kicks off — directory exists by then.
  if CurStep = ssInstall then
    WriteBridgeConfig();
end;
