param([switch]$Force)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
$examplePath = Join-Path $repoRoot ".env.example"

if ((Test-Path -LiteralPath $envPath) -and -not $Force) {
    Write-Host ".env already exists; nothing changed. Use -Force only if replacement is intended."
    exit 0
}

function New-UrlSafeSecret([int]$byteCount) {
    $bytes = New-Object byte[] $byteCount
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
}

$content = Get-Content -Raw -LiteralPath $examplePath
$content = $content.Replace('replace-with-a-long-random-password', (New-UrlSafeSecret 32))
$content = $content.Replace('replace-with-at-least-32-random-characters', (New-UrlSafeSecret 48))
$content = $content.Replace('generate-with-python-cryptography-fernet', ((New-UrlSafeSecret 32) + '='))
$content = $content.Replace('replace-with-at-least-24-random-characters', (New-UrlSafeSecret 32))
[System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
Write-Host "Created $envPath with random local secrets."
Write-Host "Next: fill GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, then run docker compose up --build."
