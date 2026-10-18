<#
  Package the native Windows Chromix build into a portable chromix bundle,
  zip archive, and SHA256 manifest.
#>
param(
  [Parameter(Mandatory)] [string]$Out,
  [Parameter(Mandatory)] [string]$Dest,
  [ValidateSet("x64", "arm64")]
  [string]$Arch = $(if ($env:CHROMIX_TARGET_ARCH) { $env:CHROMIX_TARGET_ARCH } else { "x64" })
)
$ErrorActionPreference = "Stop"
if ($Arch -cnotin @("x64", "arm64")) { throw "Arch/CHROMIX_TARGET_ARCH must be x64 or arm64" }
$Bundle = Join-Path $Dest "chromix"
$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
Remove-Item -Recurse -Force $Bundle -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Bundle | Out-Null
Copy-Item (Join-Path $Repo "LICENSE") (Join-Path $Bundle "LICENSE.chromix")
$chromiumLicense = Join-Path (Split-Path (Split-Path $Out -Parent) -Parent) "LICENSE"
if (-not (Test-Path $chromiumLicense)) {
  throw "Chromium license is missing: $chromiumLicense"
}
Copy-Item $chromiumLicense (Join-Path $Bundle "LICENSE.chromium")
$serviceMetadata = Join-Path (Split-Path (Split-Path (Split-Path $Out -Parent) -Parent) -Parent) "google-services-source.json"
if (Test-Path -LiteralPath $serviceMetadata -PathType Leaf) {
  $serviceVariant = Get-Content -LiteralPath $serviceMetadata -Raw | ConvertFrom-Json
  if ($serviceVariant.variant -cne 'google-services-self-use') { throw 'Unsupported Google-service metadata' }
  Copy-Item -LiteralPath $serviceMetadata -Destination (Join-Path $Bundle 'google-services-source.json')
}

$required = @(
  "chrome.exe", "chrome.dll", "chrome_elf.dll",
  "chrome_100_percent.pak", "chrome_200_percent.pak", "resources.pak",
  "icudtl.dat", "libEGL.dll", "libGLESv2.dll"
)
foreach ($name in $required) {
  $source = Join-Path $Out $name
  if (-not (Test-Path $source)) { throw "required runtime file is missing: $source" }
  Copy-Item $source (Join-Path $Bundle $name)
}

$snapshot = @("v8_context_snapshot.bin", "snapshot_blob.bin") |
  Where-Object { Test-Path (Join-Path $Out $_) } |
  Select-Object -First 1
if (-not $snapshot) { throw "no V8 snapshot blob found in $Out" }
Copy-Item (Join-Path $Out $snapshot) (Join-Path $Bundle $snapshot)

$locales = Join-Path $Out "locales"
if (-not (Test-Path $locales)) { throw "required locales directory is missing: $locales" }
Copy-Item $locales (Join-Path $Bundle "locales") -Recurse

foreach ($name in @(
  "chrome_proxy.exe", "chrome_wer.dll", "chrome_crashpad_handler.exe",
  "d3dcompiler_47.dll", "dxcompiler.dll", "dxil.dll",
  "vk_swiftshader.dll", "vk_swiftshader_icd.json", "vulkan-1.dll"
)) {
  $source = Join-Path $Out $name
  if (Test-Path $source) { Copy-Item $source (Join-Path $Bundle $name) }
}

$manifests = @(Get-ChildItem $Out -File -Filter "*.manifest" -ErrorAction SilentlyContinue)
if ($manifests.Count -eq 0) { throw "required side-by-side manifest is missing from $Out" }
foreach ($manifest in $manifests) { Copy-Item $manifest.FullName (Join-Path $Bundle $manifest.Name) }
Get-ChildItem $Out -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
  ForEach-Object { Copy-Item $_.FullName (Join-Path $Bundle $_.Name) -Recurse }

$runtimeCandidates = @(
  (Join-Path $Out "msvcp140.dll"),
  (Join-Path $Out "vcruntime140.dll"),
  (Join-Path $Out "vcruntime140_1.dll"),
  (Join-Path $Out "concrt140.dll")
)
foreach ($source in $runtimeCandidates) {
  if (Test-Path $source) { Copy-Item $source (Join-Path $Bundle (Split-Path $source -Leaf)) }
}

if ($Arch -eq "arm64") {
  foreach ($name in @("msvcp140.dll", "vcruntime140.dll", "msvcp140_atomic_wait.dll", "vccorlib140.dll")) {
    $source = Join-Path $Out $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "ARM64 VC143 runtime is missing: $source" }
    Copy-Item $source (Join-Path $Bundle $name) -Force
  }
}

@'
@echo off
"%~dp0chrome.exe" %*
'@ | Set-Content -Encoding ASCII (Join-Path $Bundle "chromix.cmd")

$dll = Join-Path $Bundle "chrome.dll"
$bytes = [IO.File]::ReadAllBytes($dll)
$ascii = [Text.Encoding]::ASCII.GetString($bytes)
$utf16 = [Text.Encoding]::Unicode.GetString($bytes)
$requiredMarkers = @("uxr-webgl-vendor", "uxr-webgl-renderer")
# Desktop x86 GPU personas are not an ARM64 build identity requirement.
if ($Arch -eq "x64") { $requiredMarkers += @(
  "Google Inc. (Intel)",
  "ANGLE (Intel, Intel(R) UHD Graphics 770",
  "Google Inc. (NVIDIA)",
  "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
  "Google Inc. (AMD)",
  "ANGLE (AMD, AMD Radeon(TM) Graphics"
) }
foreach ($marker in $requiredMarkers) {
  if ($ascii.Contains($marker) -or $utf16.Contains($marker)) {
    continue
  }
  if ($marker.StartsWith("ANGLE (NVIDIA,")) {
    throw "chrome.dll contains forbidden WebGL identity marker: $marker"
  }
  throw "chrome.dll is missing required WebGL persona marker: $marker"
}
Write-Host "==> chrome.dll WebGL persona marker scan passed"

if ($Arch -eq "arm64") {
  python (Join-Path $Repo "tools\verify_windows_bundle.py") --bundle $Bundle --arch $Arch
  if ($LASTEXITCODE -ne 0) { throw "Windows ARM64 bundle metadata verification failed" }
}
$assetName = if ($Arch -eq "arm64") { "chromix-win-arm64.zip" } else { "chromix-win-x64.zip" }
$asset = Join-Path $Dest $assetName
Remove-Item $asset -ErrorAction SilentlyContinue
Compress-Archive -Path $Bundle -DestinationPath $asset
$hash = (Get-FileHash $asset -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $assetName" | Set-Content -Encoding ASCII (Join-Path $Dest "SHA256SUMS")
Write-Host "==> $asset  sha256=$hash"
