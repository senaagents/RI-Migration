param(
  [Parameter(Mandatory=$true)] [string] $ExePath,
  [Parameter(Mandatory=$true)] [string] $ConfigPath,
  [Parameter(Mandatory=$true)] [string] $LogDir,
  [Parameter(Mandatory=$true)] [string] $TaskName
)

# Register a Scheduled Task that launches the Sena Tally Bridge at user
# logon. The task runs as the *current* user (not LocalSystem) so it can
# reach Tally Prime's per-user HTTP server on localhost:9000. The task
# command writes bridge stdout/stderr to a daily-rotated log under
# $LogDir so the user can troubleshoot without the bridge having any UI.
#
# Re-registration is idempotent — Unregister-ScheduledTask first if the
# task already exists from a previous install.

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ExePath)) {
  throw "Bridge exe not found at $ExePath"
}
if (-not (Test-Path $ConfigPath)) {
  throw "Config file not found at $ConfigPath"
}
if (-not (Test-Path $LogDir)) {
  New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# We wrap the bridge in a tiny PowerShell command rather than calling
# the exe directly so we can redirect stdout to a dated log file.
$LogTemplate = Join-Path $LogDir "bridge-$(Get-Date -Format yyyy-MM-dd).log"
$Command = "& `"$ExePath`" --config `"$ConfigPath`" *>> `"$LogTemplate`""

$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$Command`""

# At logon (current user only). RunLevel Limited = ordinary user
# privileges, no UAC elevation prompt.
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Hours 0)  # 0 = no limit

$Principal = New-ScheduledTaskPrincipal `
  -UserId $env:USERNAME `
  -LogonType Interactive `
  -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Principal $Principal `
  -Description "Sena Tally Bridge — auto-syncs Tally Prime data to Sena. Edit %LOCALAPPDATA%\SenaTallyBridge\bridge-config.json to reconfigure." | Out-Null

Write-Host "Registered scheduled task '$TaskName' for user $env:USERNAME"
