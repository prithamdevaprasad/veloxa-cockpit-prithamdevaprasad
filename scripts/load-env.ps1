# Load KEY=VALUE lines from a .env file into the CURRENT PowerShell process environment (process scope only).
# Values are never printed, and no PowerShell variable is left holding a value: the work runs in its own scope.
# Blank lines and lines starting with # are ignored. Usage (from the repository root):  . .\scripts\load-env.ps1
param([string]$Path = (Join-Path (Get-Location) ".env"))
& {
    param([string]$EnvFile)
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        Write-Host "No .env at $EnvFile (copy .env.example to .env first)."
        return
    }
    $loaded = @()
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        $text = $line.Trim()
        if ($text -eq "" -or $text.StartsWith("#")) { continue }
        $eq = $text.IndexOf("=")
        if ($eq -lt 1) { continue }
        $key = $text.Substring(0, $eq).Trim()
        $value = $text.Substring($eq + 1).Trim().Trim('"')
        if ($key -notmatch '^[A-Z][A-Z0-9_]*$') { continue }
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
        $loaded += $key
    }
    Write-Host ("Loaded {0} setting(s) from .env: {1}" -f $loaded.Count, ($loaded -join ", "))
} $Path
