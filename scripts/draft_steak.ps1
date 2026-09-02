# draft_steak.ps1 — launch DraftIQ in GUEST-LEAGUE mode for
# "Steak and Ales with the Lads" (ESPN league 998262196, 8-team FULL PPR, no IDP).
#
#   .\scripts\draft_steak.ps1           # serve the API against the steak board
#   .\scripts\draft_steak.ps1 -Build    # rebuild the steak board first, then serve
#
# Frontend stays the usual `cd frontend; npm run dev` — the UI reads /api/draft-meta
# at boot and reconfigures itself (8 teams, slot 2, 16 rounds, no IDP slot, its own
# localStorage save key). The home-league Taco board/state is untouched.
param([switch]$Build)

$root = Split-Path -Parent $PSScriptRoot
$env:DRAFTIQ_LEAGUE      = 'steak'
$env:DRAFTIQ_LEAGUE_ID   = '998262196'
$env:DRAFTIQ_SETTINGS_DIR = Join-Path $root 'data\raw\espn_steak'
$env:DRAFTIQ_FP_DIR       = Join-Path $root 'data\raw\fantasypros_steak'
$env:DRAFTIQ_BOARD        = Join-Path $root 'data\processed\board_2026_steak.csv'

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

if ($Build -or -not (Test-Path $env:DRAFTIQ_BOARD)) {
    Write-Host "Building steak board -> $($env:DRAFTIQ_BOARD)" -ForegroundColor Yellow
    & $py (Join-Path $root 'models\build_board.py')
    if ($LASTEXITCODE -ne 0) { Write-Host 'BOARD BUILD FAILED - not serving a stale/missing board' -ForegroundColor Red; exit 1 }
}

Write-Host 'Serving DraftIQ (steak mode) on http://127.0.0.1:5001' -ForegroundColor Green
& $py (Join-Path $root 'api.py')
