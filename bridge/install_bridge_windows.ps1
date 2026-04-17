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
$PairingCode = Read-Required "Pairing code from Talk to Your Data" $PairingCode

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

$BridgeSource = Join-Path $PSScriptRoot "sena_tally_bridge.py"
$BridgeTarget = Join-Path $InstallDir "sena_tally_bridge.py"
Copy-Item $BridgeSource $BridgeTarget -Force

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
`$Config = Get-Content "$ConfigPath" | ConvertFrom-Json
& "$PythonCmd" "$BridgeTarget" --server `$Config.server --pairing-code `$Config.pairing_code --tally-host `$Config.tally_host --tally-port `$Config.tally_port
"@ | Set-Content -Path $RunScript -Encoding UTF8

Write-Host "Sena Tally Bridge installed at $InstallDir"
Write-Host "Config saved to $ConfigPath"
Write-Host "Run it with: powershell -ExecutionPolicy Bypass -File `"$RunScript`""

if ($RunOnce) {
  & $PythonCmd $BridgeTarget --server $Server --pairing-code $PairingCode --tally-host $TallyHost --tally-port $TallyPort --once
}
