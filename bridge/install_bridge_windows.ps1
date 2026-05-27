param(
  [string]$Server = "",
  [string]$PairingCode = "",
  [string]$TallyHost = "localhost",
  [int]$TallyPort = 9000,
  [switch]$RunOnce
)

$ErrorActionPreference = "Stop"

function Read-Required($Prompt, $CurrentValue) {
  if ($CurrentValue -and $CurrentValue.Trim().Length -gt 0) {
    return $CurrentValue.Trim()
  }
  do {
    $value = Read-Host $Prompt
  } while (-not $value -or $value.Trim().Length -eq 0)
  return $value.Trim()
}

$Server = Read-Required "Sena server URL, for example https://app.senaagents.com" $Server
$PairingCode = Read-Required "Pairing code from Migration" $PairingCode

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
  $Python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $Python) {
  throw "Python was not found. Install Python 3.11+ or use the packaged Sena Tally Bridge executable."
}
$PythonCmd = $Python.Source

$InstallDir = Join-Path $env:LOCALAPPDATA "SenaTallyBridge"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$BridgeFiles = @(
  "sena_tally_bridge.py",
  "tally_decode_path.py",
  "tally_http_path.py",
  "tally_http_pool.py"
)
foreach ($File in $BridgeFiles) {
  $Source = Join-Path $PSScriptRoot $File
  if (-not (Test-Path $Source)) {
    throw "Required bridge file not found: $Source"
  }
  Copy-Item $Source (Join-Path $InstallDir $File) -Force
}

$DecoderSource = Join-Path $PSScriptRoot "tally1800"
$DecoderTarget = Join-Path $InstallDir "tally1800"
if (-not (Test-Path $DecoderSource)) {
  throw "Required decoder package not found: $DecoderSource"
}
if (Test-Path $DecoderTarget) {
  Remove-Item $DecoderTarget -Recurse -Force
}
Copy-Item $DecoderSource $DecoderTarget -Recurse -Force

$BridgeTarget = Join-Path $InstallDir "sena_tally_bridge.py"

$Config = @{
  server = $Server
  pairing_code = $PairingCode
  tally_host = $TallyHost
  tally_port = $TallyPort
  installed_at = (Get-Date).ToString("s")
}
$ConfigPath = Join-Path $InstallDir "bridge-config.json"
$Config | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8

$RunScript = Join-Path $InstallDir "run-bridge.ps1"
@"
`$ErrorActionPreference = "Stop"
& "$PythonCmd" "$BridgeTarget" --config "$ConfigPath"
"@ | Set-Content -Path $RunScript -Encoding UTF8

Write-Host "Sena Tally Bridge installed at $InstallDir"
Write-Host "Config saved to $ConfigPath"
Write-Host "Run it with: powershell -ExecutionPolicy Bypass -File `"$RunScript`""

if ($RunOnce) {
  & $PythonCmd $BridgeTarget --config $ConfigPath --once
}
