[CmdletBinding()]
param(
    [string]$TaskName = 'video-use official upstream update',
    [datetime]$DailyAt = [datetime]::Today.AddHours(9)
)

$ErrorActionPreference = 'Stop'
$AutoUpdate = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'auto_update.ps1'
$PowerShell = (Get-Command powershell.exe).Source
$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$AutoUpdate`""
$Trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Description `
    'Safely merges official video-use updates in a temporary worktree, tests, then pushes main.' `
    -Force | Out-Null
Write-Host "Registered daily task '$TaskName' at $($DailyAt.ToString('HH:mm'))."
