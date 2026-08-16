<#
Starts the local Docker stack, waits for the API, and opens MarketMind. It never
removes containers or database data. Optional market providers use demo data;
private application pages still require configured Supabase Auth.
#>
param([switch]$NoBrowser, [int]$TimeoutSeconds = 90)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw 'Docker Desktop is required for this helper. Install and start Docker Desktop first.'
}
if (-not (Test-Path '.env')) {
  Copy-Item '.env.example' '.env'
  Write-Host 'Created .env with safe defaults. Add local Supabase values before signing in.' -ForegroundColor Yellow
}

docker compose up --detach --build
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not start MarketMind.' }

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$health = $null
do {
  try { $health = Invoke-RestMethod 'http://localhost:8000/api/health' -TimeoutSec 3 } catch { Start-Sleep -Seconds 2 }
} until ($health -or (Get-Date) -gt $deadline)
if (-not $health) {
  docker compose logs --tail 80
  throw 'MarketMind did not pass its API health check in time.'
}

Write-Host "MarketMind is ready at http://localhost:3000 (auth: $($health.authentication); live trading: $($health.live_trading))." -ForegroundColor Green
if (-not $NoBrowser) { Start-Process 'http://localhost:3000' }
