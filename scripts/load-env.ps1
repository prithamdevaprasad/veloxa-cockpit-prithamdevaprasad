# Load KEY=VALUE lines from a .env file into the CURRENT PowerShell session (process scope only).
# Values are never printed. Lines starting with # and blank lines are ignored. Usage:  . .\scripts\load-env.ps1
param([string]$Path = (Join-Path (Get-Location) ".env"))
if (-not (Test-Path $Path)) { Write-Host "No .env at $Path (copy .env.example to .env first)."; return }
$names = @()
foreach ($line in Get-Content -LiteralPath $Path) {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    $i = $t.IndexOf("=")
    if ($i -lt 1) { continue }
    $k = $t.Substring(0, $i).Trim()
    $v = $t.Substring($i + 1).Trim().Trim('"')
    if ($k -notmatch '^[A-Z][A-Z0-9_]*$') { continue }
    [Environment]::SetEnvironmentVariable($k, $v, "Process")
    $names += $k
}
Write-Host ("Loaded {0} setting(s) from .env: {1}" -f $names.Count, ($names -join ", "))
