<# Stops the local containers but deliberately keeps the PostgreSQL volume and settings. #>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw 'Docker Desktop is required for this helper.'
}
docker compose stop
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not stop MarketMind.' }
Write-Host 'MarketMind stopped. Your local database volume was kept.' -ForegroundColor Green
