$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $PSScriptRoot "start_local_server.ps1"
$catchupScript = Join-Path $PSScriptRoot "run_local_catchup.ps1"
$powerShell = (Get-Command powershell.exe).Source

$serverAction = New-ScheduledTaskAction -Execute $powerShell -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startScript`""
$serverTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$serverSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "NaverKeywordDashboardServer" -Action $serverAction -Trigger $serverTrigger -Settings $serverSettings -Description "로그인 시 네이버 키워드 localhost 대시보드 서버 시작" -Force | Out-Null

$dailyAction = New-ScheduledTaskAction -Execute $powerShell -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$catchupScript`""
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At "08:00"
$dailySettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "NaverKeywordDailyCatchup" -Action $dailyAction -Trigger $dailyTrigger -Settings $dailySettings -Description "매일 오전 8시 누락일~전일 네이버 키워드 쿼리 확정" -Force | Out-Null

Write-Host "설치 완료: NaverKeywordDashboardServer, NaverKeywordDailyCatchup"
