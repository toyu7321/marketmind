<#
Run after docker compose up --build (or after starting both local servers).
It verifies the checked-out project, Compose configuration, API health,
database health reported by the API, and frontend availability.
#>
param([int]$TimeoutSeconds = 45, [switch]$SkipComposeCheck)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$required = @('.env.example', 'docker-compose.yml', 'backend/requirements.txt', 'backend/alembic.ini', 'frontend/package.json')
$missing = $required | Where-Object { -not (Test-Path $_) }
if ($missing) { throw "Missing required files: $($missing -join ', ')" }
if (-not (Test-Path '.env')) { throw "Missing .env. Run: Copy-Item .env.example .env" }

if (-not $SkipComposeCheck) {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker Desktop is not available. Install and start Docker Desktop, then re-run this script.'
  }
  docker compose config --quiet
  if ($LASTEXITCODE -ne 0) { throw 'docker compose config failed.' }
  Write-Host 'Compose configuration: valid' -ForegroundColor Green
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$health = $null
do {
  try { $health = Invoke-RestMethod 'http://localhost:8000/api/health' -TimeoutSec 3 } catch { Start-Sleep -Seconds 2 }
} until ($health -or (Get-Date) -gt $deadline)
if (-not $health) { throw 'Backend health endpoint did not respond at http://localhost:8000/api/health' }
if ($health.database -ne 'connected') { throw "Backend responded, but database is $($health.database)." }

try { $frontend = Invoke-WebRequest 'http://localhost:3000' -UseBasicParsing -TimeoutSec 5 } catch { throw 'Frontend did not respond at http://localhost:3000' }
if ($frontend.StatusCode -ne 200) { throw "Frontend returned HTTP $($frontend.StatusCode)." }

Write-Host 'Backend API: healthy' -ForegroundColor Green
Write-Host 'Database: connected' -ForegroundColor Green
Write-Host 'Frontend: available' -ForegroundColor Green
Write-Host ('Provider mode: ' + $health.market_provider)
Write-Host 'MarketMind local verification passed.' -ForegroundColor Green
