# Starter Ollama-tolkningen av ventende innsidemeldinger på Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\start_ollama.ps1
# Trygt å kjøre igjen: fortsetter der den slapp (meldinger i
# data\ollama\tolkninger.jsonl hoppes over). Avbryt med Ctrl+C.
param([string]$Modell = "qwen2.5:7b", [int]$Maks = 0)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

foreach ($k in "git", "ollama") {
    if (-not (Get-Command $k -ErrorAction SilentlyContinue)) { throw "Fant ikke '$k' i PATH. Installer det og prøv igjen." }
}

# «python» kan være en snarvei til Microsoft Store (WindowsApps) uten ekte Python bak.
function Finn-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c -and $c.Source -notlike "*WindowsApps*") { return @($c.Source) }
    # Installert uten «Add to PATH»: let i standardmappene, nyeste først.
    $steder = @("$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe",
                "C:\Python3*\python.exe")
    $funnet = Get-ChildItem $steder -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Directory.Name -replace "\D", "") } -Descending | Select-Object -First 1
    if ($funnet) { return @($funnet.FullName) }
    return $null
}
$py = Finn-Python
if (-not $py) {
    Write-Host "== Fant ikke Python - installerer Python 3.12 med winget" -ForegroundColor Cyan
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = Finn-Python
    if (-not $py) { throw "Python ble installert, men finnes ikke i PATH ennå. Lukk PowerShell, åpne et nytt vindu og kjør skriptet igjen." }
}
$pyArgs = @(); if ($py.Length -gt 1) { $pyArgs = $py[1..($py.Length - 1)] }

Write-Host "== Oppdaterer repoet" -ForegroundColor Cyan
git pull --rebase --autostash

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "== Lager Python-miljø (.venv)" -ForegroundColor Cyan
    if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }
    & $py[0] @pyArgs -m venv .venv
    if (-not (Test-Path .venv\Scripts\python.exe)) { throw "Klarte ikke å lage .venv" }
}
Write-Host ("== Bruker " + (& .\.venv\Scripts\python.exe --version)) -ForegroundColor Cyan
# Tolkningen trenger bare requests + tidssonedata (Windows har ikke tidssonedatabasen innebygd).
.\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check requests tzdata

Write-Host "== Sjekker Ollama" -ForegroundColor Cyan
try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 5 | Out-Null }
catch {
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}
ollama pull $Modell

$env:INNSIDE_LLM = "ollama"
$env:OLLAMA_MODELL = $Modell
if (-not $env:MOMENTUM_UA) { $env:MOMENTUM_UA = "momentum-varsling/0.1 (lokal ollama-tolkning)" }

Write-Host "== Tolker (Ctrl+C for å stoppe; start igjen for å fortsette)" -ForegroundColor Cyan
if ($Maks -gt 0) { .\.venv\Scripts\python.exe -m scripts.ollama_tolk --maks $Maks }
else { .\.venv\Scripts\python.exe -m scripts.ollama_tolk }
