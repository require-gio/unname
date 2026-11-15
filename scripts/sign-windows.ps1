param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BinaryPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Test-Path -LiteralPath $BinaryPath -PathType Leaf)) {
    throw "Zu signierende Datei fehlt: $BinaryPath"
}

$thumbprint = $env:WINDOWS_CERTIFICATE_SHA1
if ([string]::IsNullOrWhiteSpace($thumbprint)) {
    throw "WINDOWS_CERTIFICATE_SHA1 fehlt; Release-Artefakte werden nicht unsigniert erzeugt."
}

$timestampUrl = $env:WINDOWS_TIMESTAMP_URL
if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
    throw "WINDOWS_TIMESTAMP_URL fehlt; eine dauerhafte Authenticode-Signatur braucht einen RFC-3161-Zeitstempel."
}

$signTool = Get-Command signtool.exe -ErrorAction Stop
$normalizedThumbprint = $thumbprint.Replace(" ", "").ToUpperInvariant()
if ($normalizedThumbprint -notmatch '^[0-9A-F]{40}$') {
    throw "WINDOWS_CERTIFICATE_SHA1 muss ein 40-stelliger SHA-1-Zertifikatabdruck sein."
}

& $signTool.Source sign /sha1 $normalizedThumbprint /fd SHA256 /tr $timestampUrl /td SHA256 /v $BinaryPath
if ($LASTEXITCODE -ne 0) {
    throw "signtool.exe ist mit Exit-Code $LASTEXITCODE fehlgeschlagen."
}

$signature = Get-AuthenticodeSignature -LiteralPath $BinaryPath
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Authenticode-Prüfung fehlgeschlagen: $($signature.Status) – $($signature.StatusMessage)"
}

Write-Host "Authenticode-Signatur geprüft: $BinaryPath"
