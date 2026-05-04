; Sena Tally Bridge — Inno Setup script.
;
; Wizard collects: Sena server URL, pairing code, Tally host/port, and Tally data folder.
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
OutputDir=..\..\dist-installer
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
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
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
    'To find the Tally company data folder, open TallyPrime on this Windows machine, then press Alt+Y (Data) > Configuration > Company Data Path. Paste that folder path here. These values are saved to bridge-config.json and can be edited later by reinstalling.');
  ConfigPage.Add('Sena server URL (e.g. https://app.senaagents.com)', False);
  ConfigPage.Add('Pairing code from the Sena migration page', False);
  ConfigPage.Add('Tally host (default: localhost)', False);
  ConfigPage.Add('Tally port (default: 9000)', False);
  ConfigPage.Add('Tally company data folder from Alt+Y (Data) > Configuration > Company Data Path', False);

  // Pre-fill defaults
  ConfigPage.Values[2] := 'localhost';
  ConfigPage.Values[3] := '9000';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ServerUrl, PairingCode, DataDir: string;
begin
  Result := True;
  if CurPageID = ConfigPage.ID then
  begin
    ServerUrl := Trim(ConfigPage.Values[0]);
    PairingCode := Trim(ConfigPage.Values[1]);
    DataDir := Trim(ConfigPage.Values[4]);
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
    if Length(DataDir) = 0 then
    begin
      MsgBox('Tally company data folder is required. In TallyPrime, press Alt+Y (Data) > Configuration, copy Company Data Path, and paste it into this installer.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if not DirExists(DataDir) then
    begin
      MsgBox('Tally company data folder does not exist: ' + DataDir, mbError, MB_OK);
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
  // ISPP (Inno Setup's preprocessor) sees `#` at column-0 as a directive
  // prefix and chokes on `#8` / `#13` Pascal char literals before the
  // Pascal compiler reads them. Use Chr(...) instead — semantically the
  // same, but invisible to the preprocessor.
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    if C = '"' then Result := Result + '\"'
    else if C = '\' then Result := Result + '\\'
    else if C = Chr(8) then Result := Result + '\b'
    else if C = Chr(9) then Result := Result + '\t'
    else if C = Chr(10) then Result := Result + '\n'
    else if C = Chr(13) then Result := Result + '\r'
    else Result := Result + C;
  end;
end;

procedure WriteBridgeConfig();
var
  ConfigPath, Body, Server, Pairing, Host, Port, DataDir, InstalledAt, CRLF, AppDir: string;
begin
  AppDir := ExpandConstant('{app}');
  // Defensive: at ssPostInstall the directory exists from the [Files] copy,
  // but if the install has zero [Files] entries on a re-run path, create it.
  ForceDirectories(AppDir);
  ConfigPath := AppDir + '\bridge-config.json';
  Server := Trim(ConfigPage.Values[0]);
  Pairing := Trim(ConfigPage.Values[1]);
  Host := Trim(ConfigPage.Values[2]);
  if Length(Host) = 0 then Host := 'localhost';
  Port := Trim(ConfigPage.Values[3]);
  if Length(Port) = 0 then Port := '9000';
  DataDir := Trim(ConfigPage.Values[4]);
  InstalledAt := GetDateTimeString('yyyy-mm-dd"T"hh:nn:ss', '-', ':');

  // CRLF — Chr(13)+Chr(10) instead of #13#10 (see comment on JsonEscape).
  CRLF := Chr(13) + Chr(10);
  Body :=
    '{' + CRLF +
    '  "server": "' + JsonEscape(Server) + '",' + CRLF +
    '  "pairing_code": "' + JsonEscape(Pairing) + '",' + CRLF +
    '  "tally_host": "' + JsonEscape(Host) + '",' + CRLF +
    '  "tally_port": ' + Port + ',' + CRLF +
    '  "tally_data_dir": "' + JsonEscape(DataDir) + '",' + CRLF +
    '  "installed_at": "' + InstalledAt + '",' + CRLF +
    '  "version": "{#MyAppVersion}"' + CRLF +
    '}' + CRLF;

  if not SaveStringToFile(ConfigPath, Body, False) then
    RaiseException('Failed to write ' + ConfigPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // ssPostInstall fires after [Files] copy, so {app}\ exists; ssInstall
  // fires *before* file copy and the directory may not exist yet — that
  // was the cause of "Failed to write bridge-config.json" on first install.
  if CurStep = ssPostInstall then
    WriteBridgeConfig();
end;
