param(
  [Parameter(Mandatory)]
  [ValidateSet('SelectCheckpoint', 'Prerequisites', 'Restore', 'Build', 'Checkpoint')]
  [string]$Phase,
  [int]$Stage = 1,
  [string]$ResumeRun = '',
  [string]$Repository = $env:GITHUB_REPOSITORY,
  [ValidateRange(5, 300)]
  [int]$CompileMinutes = 300,
  [ValidateRange(30, 120)]
  [int]$FinalizeReserveMinutes = 50
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
  throw 'This fixed-path build script requires a disposable GitHub-hosted runner'
}
if ($ResumeRun -and $ResumeRun -notmatch '^[0-9]+$') { throw 'ResumeRun must be a numeric Actions run ID' }
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$root = 'C:\c'
$work = 'C:\c\chromix'
$src = Join-Path $work 'src'
$out = Join-Path $src 'out\Default'
$restore = 'C:\google-restore'
$parts = 'C:\google-parts'
$selectionPath = 'C:\google-checkpoint-selection.json'
$seven = (Get-Command 7z.exe -ErrorAction Stop).Source
$gh = (Get-Command gh.exe -ErrorAction Stop).Source

function Checked([scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "Build command failed with exit code $LASTEXITCODE" }
}
function Remove-OwnedDirectory([string]$Path, [string]$Expected) {
  if ([IO.Path]::GetFullPath($Path) -cne [IO.Path]::GetFullPath($Expected)) { throw 'Unexpected cleanup path' }
  if (Test-Path -LiteralPath $Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Cleanup target must not be a junction' }
    Remove-Item -LiteralPath $Path -Recurse -Force
  }
}
function Output([string]$Name, [string]$Value) {
  if ($env:GITHUB_OUTPUT) { Add-Content -LiteralPath $env:GITHUB_OUTPUT -Value "$Name=$Value" }
  Write-Host "$Name=$Value"
}

if ($Phase -eq 'SelectCheckpoint') {
  if (-not $ResumeRun) { throw 'An explicit completed checkpoint is required; no cold or upstream fallback is allowed' }
  Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_checkpoint.py') select --repository $Repository --source-run $ResumeRun --current-run $env:GITHUB_RUN_ID --stage $Stage --output $selectionPath }
  return
}

if ($Phase -eq 'Prerequisites') {
  # Leave time inside the 355-minute job limit to package and upload the tree.
  if (-not (Test-Path -LiteralPath 'C:\google-build-session.json')) {
    @{ started_at = [DateTimeOffset]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath 'C:\google-build-session.json'
  }
  # These are disposable hosted-runner caches, not files on the owner's PC.
  foreach ($unused in @('C:\Android', 'C:\Program Files\Android', 'C:\Program Files (x86)\Android', 'C:\ghcup', 'C:\Miniconda3', 'C:\ProgramData\chocolatey\cache')) {
    if (Test-Path -LiteralPath $unused) { Remove-OwnedDirectory $unused $unused }
  }
  if (Test-Path -LiteralPath $root) { throw 'Checkpoint destination already exists' }
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  if ($LASTEXITCODE -ne 0 -or -not $vs) { throw 'Visual Studio C++ toolchain is unavailable' }
  & "$PSScriptRoot\ensure-windows-sdk.ps1" -Arch x64 -ChromiumVersion '155.0.8059.39' -Install
  return
}

if ($Phase -eq 'Restore') {
  if (-not (Test-Path -LiteralPath $selectionPath)) { throw 'No complete checkpoint manifest was selected before setup' }
  $selection = Get-Content -Raw -LiteralPath $selectionPath | ConvertFrom-Json
  if ($selection.schema_version -ne 1 -or $selection.repository -ne $Repository -or [string]$selection.source_run -ne $ResumeRun) {
    throw 'Checkpoint selection changed between validation and restore'
  }
  Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_checkpoint.py') download --selection $selectionPath --destination $restore --report 'C:\google-transfer.json' }
  $first = Join-Path $restore 'tree.7z.001'
  Write-Host 'Extracting committed checkpoint (7-Zip verifies data and restores file timestamps)'
  Checked { & $seven x $first "-o$root" -y -bsp0 | Select-Object -Last 8 }
  if (-not (Test-Path -LiteralPath (Join-Path $out 'build.ninja'))) { throw 'Compiled checkpoint is missing' }
  $sourceMetadata = Get-Content -LiteralPath (Join-Path $work 'google-services-source.json') -Raw | ConvertFrom-Json
  foreach ($binding in @(@('source_patch_sha256','patch_sha256'), @('source_preparation_sha256','preparation_sha256'))) {
    $expectedHash = $selection.expected_hashes.($binding[0])
    if ($expectedHash -and $sourceMetadata.($binding[1]) -ne $expectedHash) { throw 'Checkpoint source receipt does not match its committed manifest' }
  }
  if ($selection.expected_hashes.gn_args_sha256) {
    $argsHash = (Get-FileHash -LiteralPath (Join-Path $out 'args.gn') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($argsHash -ne $selection.expected_hashes.gn_args_sha256) { throw 'Checkpoint GN configuration identity changed' }
  }
  Remove-OwnedDirectory $restore 'C:\google-restore'
  @{
    repository = $selection.repository
    run_id = $selection.source_run
    artifacts = @($selection.parts | ForEach-Object { $_.artifact_name })
    checkpoint_commit = $selection.checkpoint_commit
  } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $work 'google-services-checkpoint-source.json')
  $patch = 'C:\Program Files\Git\usr\bin\patch.exe'
  if (-not (Test-Path -LiteralPath $patch)) { throw 'Git for Windows GNU patch is unavailable' }
  $logs = Join-Path $work 'google-build-logs'
  New-Item -ItemType Directory -Path $logs -Force | Out-Null
  Checked { python -X utf8 (Join-Path $repo 'tools\prepare_google_services.py') --work $work --repo $repo --patch-bin $patch 2>&1 | Tee-Object -FilePath (Join-Path $logs "prepare-stage-$Stage.log") }
  Output 'checkpoint_ready' 'true'
  return
}

if ($Phase -eq 'Checkpoint') {
  if (-not (Test-Path -LiteralPath (Join-Path $work 'google-services-source.json')) -or
      (Test-Path -LiteralPath (Join-Path $work '.google-services-preparing'))) {
    throw 'Cannot snapshot an unprepared or interrupted source tree'
  }
  if (Test-Path -LiteralPath $parts) { throw 'Expected a fresh checkpoint output directory' }
  New-Item -ItemType Directory -Path $parts | Out-Null
  Push-Location $root
  try {
    Checked { & $seven a -v8g -mx=1 -mtc=on (Join-Path $parts 'tree.7z') chromix -bsp0 | Select-Object -Last 8 }
  } finally { Pop-Location }
  $volumes = @(Get-ChildItem -LiteralPath $parts -Filter 'tree.7z.*' -File | Sort-Object Name)
  if ($volumes.Count -lt 1 -or $volumes.Count -gt 4) { throw 'Checkpoint volume count exceeds the configured upload slots' }
  $preparedSource = Get-Content -LiteralPath (Join-Path $work 'google-services-source.json') -Raw | ConvertFrom-Json
  @{
    format_version = 1
    chromium_version = '155.0.8059.39'
    run_id = $env:GITHUB_RUN_ID
    stage = $Stage
    commit = $env:GITHUB_SHA
    source_patch_sha256 = $preparedSource.patch_sha256
    source_preparation_sha256 = $preparedSource.preparation_sha256
    gn_args_sha256 = (Get-FileHash -LiteralPath (Join-Path $out 'args.gn') -Algorithm SHA256).Hash.ToLowerInvariant()
    parts = @($volumes | ForEach-Object { @{ name = $_.Name; bytes = $_.Length } })
  } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $parts 'checkpoint.json')
  Output 'parts' ([string]$volumes.Count)
  Output 'checkpoint' 'true'
  return
}

Output 'finished' 'false'
Output 'compiler_started' 'false'
Output 'cache_may_change' 'false'
$session = Get-Content -Raw -LiteralPath 'C:\google-build-session.json' | ConvertFrom-Json
$elapsed = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($session.started_at)).TotalMinutes
$compileBudgetMinutes = [Math]::Min($CompileMinutes, [Math]::Floor(355 - $elapsed - $FinalizeReserveMinutes))
if ($compileBudgetMinutes -lt 5) {
  Write-Warning 'Cache restoration consumed the compile budget; the next stage can reuse the original committed checkpoint'
  return
}

$env:DEPOT_TOOLS_WIN_TOOLCHAIN = '0'
$env:DEPOT_TOOLS_METRICS = '0'
$env:DEPOT_TOOLS_COLLECT_METRICS = '0'
& "$PSScriptRoot\ensure-windows-sdk.ps1" -Arch x64 -ChromiumVersion '155.0.8059.39'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio C++ toolchain is unavailable' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$sdkInfo = & "$PSScriptRoot\ensure-windows-sdk.ps1" -Arch x64 -ChromiumVersion '155.0.8059.39' -Describe | ConvertFrom-Json
$environment = & $env:ComSpec /d /s /c "`"$devcmd`" -arch=x64 -host_arch=x64 -winsdk=$($sdkInfo.version) >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'Visual Studio environment setup failed' }
foreach ($line in $environment) {
  if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process') }
}
& "$PSScriptRoot\configure-node.ps1" -NodePath (Join-Path $src 'third_party\node\win\node.exe')
$ninja = & python (Join-Path $repo 'tools\restore_ninja.py') --workdir $work --platform windows --arch x64
if ($LASTEXITCODE -ne 0 -or -not $ninja) { throw 'Cannot select checkpoint-compatible Ninja' }
$env:PATH = "$(Split-Path $ninja);$(Join-Path $src 'third_party\node\win');$env:PATH"
$resources = & python (Join-Path $repo 'tools\build_resources.py') | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot select compiler parallelism' }
$jobs = [int]$resources.ninja_jobs
$logs = Join-Path $work 'google-build-logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
Push-Location $src
try {
  Checked { & (Join-Path $out 'gn.exe') gen $out --fail-on-unused-args 2>&1 | Tee-Object -FilePath (Join-Path $logs "gn-stage-$Stage.log") }
  Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_cache_audit.py') --ninja $ninja --out $out --refresh-manifest --target chrome --target native_passkey_unittests --report (Join-Path $logs "cache-audit-stage-$Stage.json") }
  # GN and the dry run can take minutes. Count them before reserving the tail.
  $elapsed = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($session.started_at)).TotalMinutes
  $compileBudgetMinutes = [Math]::Min($CompileMinutes, [Math]::Floor(355 - $elapsed - $FinalizeReserveMinutes))
  if ($compileBudgetMinutes -lt 5) {
    Write-Warning 'Preparation consumed the compile budget; the original committed checkpoint remains reusable'
    return
  }
  Write-Host "Compile budget: $compileBudgetMinutes minutes; reserving $FinalizeReserveMinutes minutes for packaging and checkpoint upload"
  $env:NINJA_STATUS = '[%f/%t] active=%r rate=%c/s elapsed=%es '
  $log = Join-Path $logs "ninja-stage-$Stage.log"
  $errorLog = Join-Path $logs "ninja-stage-$Stage.stderr.log"
  $audit = Get-Content -LiteralPath (Join-Path $logs "cache-audit-stage-$Stage.json") -Raw | ConvertFrom-Json
  if ($audit.status -ne 'passed' -or $null -eq $audit.pending_actions) { throw 'A real incremental build plan is required before compilation' }
  Output 'compiler_started' 'true'
  Output 'cache_may_change' $(if ($audit.pending_actions -gt 0) { 'true' } else { 'false' })
  $process = Start-Process -FilePath $ninja -ArgumentList @('-C', $out, '-j', $jobs, 'chrome', 'native_passkey_unittests') -WorkingDirectory $src -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errorLog -PassThru
  $processHandle = $process.Handle
  $clock = [Diagnostics.Stopwatch]::StartNew()
  $timedOut = $false
  while (-not $process.WaitForExit(10000)) {
    if ($clock.Elapsed.TotalMinutes -ge $compileBudgetMinutes) {
      Write-Host 'Saving progress at the compile time budget'
      & taskkill.exe /PID $process.Id /T /F | Out-Null
      $process.WaitForExit()
      $timedOut = $true
      break
    }
    if ([int]$clock.Elapsed.TotalSeconds % 60 -lt 11) {
      Get-Content -LiteralPath $log -Tail 3 -ErrorAction SilentlyContinue
    }
  }
  $process.Refresh()
  if (-not $timedOut -and $process.ExitCode -ne 0) {
    Get-Content -LiteralPath $log -Tail 100
    Get-Content -LiteralPath $errorLog -Tail 40
    throw "Ninja failed: $($process.ExitCode)"
  }
  if (-not $timedOut) {
    $nativeTests = Join-Path $logs "native-passkey-unittests-stage-$Stage.json"
    $nativeLauncher = Join-Path $logs "native-passkey-launcher-stage-$Stage.json"
    Checked { & "$PSScriptRoot\run-native-passkey-tests.ps1" -Executable (Join-Path $out 'native_passkey_unittests.exe') -Summary $nativeTests -Log (Join-Path $logs "native-passkey-unittests-stage-$Stage.log") -Receipt $nativeLauncher -WorkingDirectory $src }
    Checked { python -B -X utf8 (Join-Path $repo 'tools\verify_google_services_candidate.py') --native-tests-json $nativeTests }
    if (Test-Path -LiteralPath 'C:\c\dist') { throw 'Expected a fresh candidate output directory' }
    Checked { & "$PSScriptRoot\package-win.ps1" -Out $out -Dest 'C:\c\dist' -Arch x64 -SkipArchive }
    $buildRecordHash = (Get-FileHash -LiteralPath 'C:\c\dist\chromix\google-services-build.json' -Algorithm SHA256).Hash.ToLowerInvariant()
    Copy-Item -LiteralPath $nativeTests -Destination 'C:\c\dist\native-passkey-unittests.json'
    Copy-Item -LiteralPath $nativeLauncher -Destination 'C:\c\dist\native-passkey-launcher.json'
    $smokeReport = Join-Path $logs "candidate-smoke-stage-$Stage\report.json"
    Checked { python -X utf8 (Join-Path $repo 'tools\verify_windows_bundle.py') --bundle 'C:\c\dist\chromix' --arch x64 --native --version '155.0.8059.39' --report $smokeReport | Select-Object -Last 12 }
    Copy-Item -LiteralPath $smokeReport -Destination 'C:\c\dist\candidate-smoke.json'
    Checked { python -m pip install --disable-pip-version-check -r (Join-Path $repo 'tools\google-services-requirements.txt') }
    $googleReport = Join-Path $logs "google-services-runtime-stage-$Stage.json"
    Checked { python -B -X utf8 (Join-Path $repo 'tools\verify_google_services_runtime.py') --browser 'C:\c\dist\chromix\chrome.exe' --report $googleReport }
    Copy-Item -LiteralPath $googleReport -Destination 'C:\c\dist\google-services-runtime.json'
    Copy-Item -LiteralPath (Join-Path $work 'google-services-source.json') -Destination 'C:\c\dist\google-services-source.json'
    Checked { & "$PSScriptRoot\package-win.ps1" -Out $out -Dest 'C:\c\dist' -Arch x64 -ArchiveOnly -ExpectedBuildRecordSHA256 $buildRecordHash }
    Output 'finished' 'true'
  }
} finally { Pop-Location }

# Checkpoint creation is a separate workflow step. A compiler error remains a
# failed build, while the successfully compiled objects can still be uploaded.
