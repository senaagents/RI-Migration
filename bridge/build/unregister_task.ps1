param(
  [Parameter(Mandatory=$true)] [string] $TaskName
)

# Best-effort cleanup during uninstall. We never want the uninstaller to
# fail because a scheduled task / process couldn't be killed; surface the
# error and continue.

$ErrorActionPreference = "Continue"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'"
  } catch {
    Write-Warning "Could not unregister scheduled task '$TaskName': $_"
  }
}

# Kill any in-flight bridge process that's still running. The scheduled
# task is gone after uninstall but the process started by the previous
# logon-trigger may still be holding the exe.
Get-Process -Name SenaTallyBridge -ErrorAction SilentlyContinue | ForEach-Object {
  try {
    $_ | Stop-Process -Force -ErrorAction Stop
  } catch {
    Write-Warning "Could not stop process $($_.Id): $_"
  }
}
