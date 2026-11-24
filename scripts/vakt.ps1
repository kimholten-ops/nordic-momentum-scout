# Momentum-vakt: oppdaterer koden og starter scripts\vakt.py.
# Startes av Oppgaveplanlegging (se scripts\installer_vakt.ps1).
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$Host.UI.RawUI.WindowTitle = "Momentum-vakt (la vinduet staa, det kan minimeres)"
git fetch -q origin
$gren = (git symbolic-ref --short refs/remotes/origin/HEAD)
if ($gren) {
    git checkout -q -f ($gren -replace '^origin/', '')
    git reset -q --hard $gren
}
& "$repo\.venv\Scripts\python.exe" -m scripts.vakt
Start-Sleep -Seconds 10
