# Activate a development shell for thesis-refinery.
#
# Why this exists: `pip install -e .` writes a `.pth` file containing the path to
# this checkout's `src/` directory. Python's `site` module reads `.pth` files with
# `encoding="locale"`, so on a machine whose locale encoding is not UTF-8 (cp936,
# cp1251, latin-1, ...) a checkout living under a non-ASCII path cannot be found,
# and `site` skips it *silently*. `PYTHONUTF8=1` does not help: it changes
# `locale.getpreferredencoding()`, not `locale.getencoding()`.
#
# Setting PYTHONPATH sidesteps the whole thing, because environment variables are
# decoded with the filesystem encoding, which is UTF-8 on Windows.
#
# Usage:  . .\tools\dev-env.ps1        (dot-source it, don't run it)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$srcPath = Join-Path $repoRoot 'src'

if (-not (Test-Path $srcPath)) {
    throw "dev-env: expected a src/ directory at $srcPath"
}

$existing = $env:PYTHONPATH
if ($existing -and ($existing -split [IO.Path]::PathSeparator) -notcontains $srcPath) {
    $env:PYTHONPATH = $srcPath + [IO.Path]::PathSeparator + $existing
} else {
    $env:PYTHONPATH = $srcPath
}

# Keep console output from blowing up on a cp936 console when emitting the odd
# scientific character in a report.
$env:PYTHONIOENCODING = 'utf-8'

Write-Host "thesis-refinery dev shell" -ForegroundColor Cyan
Write-Host "  repo    : $repoRoot"
Write-Host "  PYTHONPATH += $srcPath"
Write-Host ""
Write-Host "  python -m thesis_refinery --help"
Write-Host "  python -m pytest"
Write-Host "  python tools/check_desensitized.py"
