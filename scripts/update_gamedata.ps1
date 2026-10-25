# Pobiera dane PoB (tylko Data + wybrane wersje drzewka) i buduje data\game.db
# Uzycie:  powershell -ExecutionPolicy Bypass -File scripts\update_gamedata.ps1
# Nowa liga z nowym drzewkiem: dopisz wersje do $TreeVersions.
param(
    [string[]]$TreeVersions = @("3_28", "3_29"),
    # Ta sama wersja PoB co w pob\Dockerfile (ARG POB_COMMIT) - dane gry i silnik obliczeń muszą się zgadzać
    [string]$Commit = "16de4b82d57f1c0de6eb40f37143c32d4da36a02"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pob = Join-Path $root "data\pob"

if (-not (Test-Path (Join-Path $pob ".git"))) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null
    git clone --depth 1 --filter=blob:none --sparse https://github.com/PathOfBuildingCommunity/PathOfBuilding.git $pob
}
git -C $pob fetch --depth 1 origin $Commit
git -C $pob checkout $Commit
$patterns = @("/src/Data/", "!/src/Data/TimelessJewelData/") + ($TreeVersions | ForEach-Object { "/src/TreeData/$_/tree.lua" })
git -C $pob sparse-checkout set --no-cone @patterns
$commit = (git -C $pob rev-parse --short HEAD).Trim()

Push-Location $root
uv run python -m app.gamedata build --pob-src (Join-Path $pob "src") --out (Join-Path $root "data\game.db") --tree-versions ($TreeVersions -join ",") --commit $commit
Pop-Location
