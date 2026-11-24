# Installerer Momentum-vakten i Oppgaveplanlegging (kjøres én gang).
# Vakten starter hverdager kl. 07:55 og når du logger inn, og avslutter
# av seg selv etter 17:45. Fjern den igjen med:
#   Unregister-ScheduledTask -TaskName "Momentum-vakt" -Confirm:$false
$repo = Split-Path -Parent $PSScriptRoot
$skript = Join-Path $repo "scripts\vakt.ps1"
$handling = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$skript`"" -WorkingDirectory $repo
$hverdag = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "07:55"
$innlogging = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$innst = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 11) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Momentum-vakt" -Action $handling -Trigger $hverdag, $innlogging -Settings $innst `
    -Description "Tolker ferske boersmeldinger med Ollama i boerstiden (nordic-momentum-scout)" -Force | Out-Null
Write-Host "Momentum-vakt er installert: starter hverdager 07:55 og ved innlogging." -ForegroundColor Green
if ((Get-Date).DayOfWeek -notin "Saturday", "Sunday") {
    Start-ScheduledTask -TaskName "Momentum-vakt"
    Write-Host "Vakten er startet naa (eget vindu)."
}
