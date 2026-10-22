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
  [int]$FinalizeReserveMinutes = 50,
  [ValidateSet('google-services', 'fingerprint-modes-win155')]
  [string]$BuildVariant = 'google-services',
  [switch]$AllowBootstrapDonor,
  [string[]]$AdditionalNinjaTargets = @(),
  [string]$FingerprintValidationScript = ''
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
$sessionPath = 'C:\google-build-session.json'
$transferPath = 'C:\google-transfer.json'
$logs = Join-Path $work 'google-build-logs'
$isFingerprint = $BuildVariant -eq 'fingerprint-modes-win155'
if ($isFingerprint) {
  # Keep C:\c and the restored source/output paths stable for Ninja's graph.
  # Only the orchestration state and artifact namespace differ.
  $restore = 'C:\fingerprint-modes-restore'
  $parts = 'C:\fingerprint-modes-parts'
  $selectionPath = 'C:\fingerprint-modes-checkpoint-selection.json'
  $sessionPath = 'C:\fingerprint-modes-build-session.json'
  $transferPath = 'C:\fingerprint-modes-transfer.json'
  $logs = Join-Path $work 'fingerprint-modes-build-logs'
}
$buildStatePath = Join-Path $work 'fingerprint-modes-stage-state.json'
$targets = @('chrome', 'native_passkey_unittests')
foreach ($target in $AdditionalNinjaTargets) {
  if ($target -notmatch '^[A-Za-z0-9_][A-Za-z0-9_./:+-]*$') { throw 'Invalid additional Ninja target' }
  if ($target -notin $targets) { $targets += $target }
}
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
function Invoke-BuildTelemetry([int]$OwnedPid) {
  if (-not $isFingerprint) { return }
  & python -B -X utf8 (Join-Path $repo 'tools\build_telemetry.py') --root-pid $OwnedPid `
    --state (Join-Path $logs "telemetry-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT-stage-$Stage.state.json") `
    --report (Join-Path $logs "telemetry-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT-stage-$Stage.jsonl") --disk $root `
    --log $log --log $errorLog
  if ($LASTEXITCODE -ne 0) { Write-Warning 'Telemetry sample failed; complete compiler logs are retained' }
}

if ($Phase -eq 'SelectCheckpoint') {
  if (-not $ResumeRun) { throw 'An explicit completed checkpoint is required; no cold or upstream fallback is allowed' }
  $variantArgs = @()
  if ($isFingerprint) {
    $variantArgs += @('--build-variant', $BuildVariant)
    if ($AllowBootstrapDonor) { $variantArgs += '--allow-bootstrap-donor' }
  } elseif ($AllowBootstrapDonor) { throw 'Bootstrap authorization is only for the fingerprint variant' }
  Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_checkpoint.py') select --repository $Repository --source-run $ResumeRun --current-run $env:GITHUB_RUN_ID --stage $Stage --output $selectionPath @variantArgs }
  return
}

if ($Phase -eq 'Prerequisites') {
  # The fingerprint preflight installs this before its owned-process test.
  # Keep legacy callers of the shared audit helper supplied as well.
  if (-not $isFingerprint) {
    Checked { python -m pip install --disable-pip-version-check psutil==7.2.2 }
  }
  # Leave time inside the 355-minute job limit to package and upload the tree.
  if (-not (Test-Path -LiteralPath $sessionPath)) {
    @{ started_at = [DateTimeOffset]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $sessionPath
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
  if ($isFingerprint -and ($selection.build_variant -cne $BuildVariant -or $selection.arch -cne 'x64')) {
    throw 'Checkpoint selection belongs to another build variant'
  }
  Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_checkpoint.py') download --selection $selectionPath --destination $restore --report $transferPath }
  $first = Join-Path $restore 'tree.7z.001'
  Write-Host 'Extracting committed checkpoint (7-Zip verifies data and restores file timestamps)'
  Checked { & $seven x $first "-o$root" -y -bsp0 | Select-Object -Last 8 }
  if (-not (Test-Path -LiteralPath (Join-Path $out 'build.ninja'))) { throw 'Compiled checkpoint is missing' }
  $sourceReceiptPath = Join-Path $work 'google-services-source.json'
  $sourceMetadata = Get-Content -LiteralPath $sourceReceiptPath -Raw | ConvertFrom-Json
  if ($isFingerprint) {
    if ($sourceMetadata.variant -cne 'google-services-self-use' -or $sourceMetadata.chromium_version -cne '155.0.8059.39') {
      throw 'Restored source does not retain the Google-service base identity'
    }
    if ($selection.bootstrap_donor) {
      if ($sourceMetadata.PSObject.Properties['build_variant']) { throw 'Bootstrap receipt has an unexpected build variant' }
    } elseif ($sourceMetadata.build_variant -cne $BuildVariant) { throw 'Restored source belongs to another build variant' }
    if (-not $selection.bootstrap_donor -and
        (Get-FileHash -LiteralPath $sourceReceiptPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $selection.expected_hashes.source_receipt_sha256) {
      throw 'Checkpoint source receipt bytes do not match the committed manifest'
    }
  }
  foreach ($binding in @(@('source_patch_sha256','patch_sha256'), @('source_preparation_sha256','preparation_sha256'))) {
    $expectedHash = $selection.expected_hashes.($binding[0])
    if ($expectedHash -and $sourceMetadata.($binding[1]) -ne $expectedHash) { throw 'Checkpoint source receipt does not match its committed manifest' }
  }
  if ($selection.expected_hashes.gn_args_sha256) {
    $argsHash = (Get-FileHash -LiteralPath (Join-Path $out 'args.gn') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($argsHash -ne $selection.expected_hashes.gn_args_sha256) { throw 'Checkpoint GN configuration identity changed' }
  }
  Remove-OwnedDirectory $restore $(if ($isFingerprint) { 'C:\fingerprint-modes-restore' } else { 'C:\google-restore' })
  @{
    repository = $selection.repository
    run_id = $selection.source_run
    artifacts = @($selection.parts | ForEach-Object { $_.artifact_name })
    checkpoint_commit = $selection.checkpoint_commit
  } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $work 'google-services-checkpoint-source.json')
  $patch = 'C:\Program Files\Git\usr\bin\patch.exe'
  if (-not (Test-Path -LiteralPath $patch)) { throw 'Git for Windows GNU patch is unavailable' }
  New-Item -ItemType Directory -Path $logs -Force | Out-Null
  Checked { python -X utf8 (Join-Path $repo 'tools\prepare_google_services.py') --work $work --repo $repo --patch-bin $patch 2>&1 | Tee-Object -FilePath (Join-Path $logs "prepare-stage-$Stage.log") }
  if ($isFingerprint) {
    $prepared = Get-Content -LiteralPath $sourceReceiptPath -Raw | ConvertFrom-Json
    if ($prepared.variant -cne 'google-services-self-use' -or $prepared.chromium_version -cne '155.0.8059.39') {
      throw 'Source preparation changed the required base identity'
    }
    if ($prepared.PSObject.Properties['build_variant'] -and $prepared.build_variant -cne $BuildVariant) {
      throw 'Prepared source has a conflicting build variant'
    }
    $sourceChanged = $prepared.patch_sha256 -ne $sourceMetadata.patch_sha256 -or
      $prepared.preparation_sha256 -ne $sourceMetadata.preparation_sha256 -or
      $sourceMetadata.build_variant -cne $BuildVariant
    if (-not $prepared.PSObject.Properties['build_variant']) {
      $prepared | Add-Member -NotePropertyName build_variant -NotePropertyValue $BuildVariant
      $prepared | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $sourceReceiptPath -Encoding utf8
    }
    @{
      build_variant = $BuildVariant; stage = $Stage; source_changed = $sourceChanged
      preparation_calls = 1; source_identity_checks = 'required_on_every_restore'
      expensive_preparation = $(if ($sourceChanged) { 'recipe_upgrade' } else { 'matching_receipt_reused' })
      source_receipt_sha256 = (Get-FileHash -LiteralPath $sourceReceiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    } | ConvertTo-Json | Set-Content -LiteralPath $buildStatePath -Encoding utf8
  }
  Output 'checkpoint_ready' 'true'
  return
}

if ($Phase -eq 'Checkpoint') {
  if (-not (Test-Path -LiteralPath (Join-Path $work 'google-services-source.json')) -or
      (Test-Path -LiteralPath (Join-Path $work '.google-services-preparing'))) {
    throw 'Cannot snapshot an unprepared or interrupted source tree'
  }
  $preparedSource = Get-Content -LiteralPath (Join-Path $work 'google-services-source.json') -Raw | ConvertFrom-Json
  if ($isFingerprint -and $preparedSource.build_variant -cne $BuildVariant) { throw 'Cannot snapshot another build variant' }
  if ($isFingerprint) {
    $auditPath = Join-Path $logs "cache-audit-stage-$Stage.json"
    if (Test-Path -LiteralPath $auditPath -PathType Leaf) {
      $auditReceipt = Get-Content -LiteralPath $auditPath -Raw | ConvertFrom-Json
      if ($auditReceipt.PSObject.Properties['safe_to_snapshot'] -and $auditReceipt.safe_to_snapshot -eq $false) {
        throw 'Audit process-tree cleanup was not confirmed; refusing a live-tree checkpoint'
      }
    }
  }
  if (Test-Path -LiteralPath $parts) { throw 'Expected a fresh checkpoint output directory' }
  New-Item -ItemType Directory -Path $parts | Out-Null
  Push-Location $root
  try {
    Checked { & $seven a -v8g -mx=1 -mtc=on (Join-Path $parts 'tree.7z') chromix -bsp0 | Select-Object -Last 8 }
  } finally { Pop-Location }
  $volumes = @(Get-ChildItem -LiteralPath $parts -Filter 'tree.7z.*' -File | Sort-Object Name)
  if ($volumes.Count -lt 1 -or $volumes.Count -gt 4) { throw 'Checkpoint volume count exceeds the configured upload slots' }
  $manifest = @{
    format_version = 1
    chromium_version = '155.0.8059.39'
    run_id = $env:GITHUB_RUN_ID
    stage = $Stage
    commit = $env:GITHUB_SHA
    source_patch_sha256 = $preparedSource.patch_sha256
    source_preparation_sha256 = $preparedSource.preparation_sha256
    gn_args_sha256 = (Get-FileHash -LiteralPath (Join-Path $out 'args.gn') -Algorithm SHA256).Hash.ToLowerInvariant()
    parts = @($volumes | ForEach-Object { @{ name = $_.Name; bytes = $_.Length } })
  }
  if ($isFingerprint) {
    $manifest.format_version = 2
    $manifest.build_variant = $BuildVariant
    $manifest.arch = 'x64'
    $manifest.source_receipt_sha256 = (Get-FileHash -LiteralPath (Join-Path $work 'google-services-source.json') -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $parts 'checkpoint.json')
  Output 'parts' ([string]$volumes.Count)
  Output 'checkpoint' 'true'
  return
}

Output 'finished' 'false'
Output 'compiler_started' 'false'
Output 'cache_may_change' 'false'
$sourceChanged = $false
if ($isFingerprint) {
  $stageState = Get-Content -LiteralPath $buildStatePath -Raw | ConvertFrom-Json
  if ($stageState.build_variant -cne $BuildVariant -or $stageState.stage -ne $Stage) { throw 'Prepared stage identity changed' }
  if ((Get-FileHash -LiteralPath (Join-Path $work 'google-services-source.json') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $stageState.source_receipt_sha256) { throw 'Prepared source receipt changed before compilation' }
  $sourceChanged = $stageState.source_changed -eq $true
  if ($sourceChanged) { Output 'cache_may_change' 'true' }
  if (-not $FingerprintValidationScript) { throw 'Fingerprint candidate validation must be configured explicitly' }
  $fingerprintValidator = [IO.Path]::GetFullPath((Join-Path $repo $FingerprintValidationScript))
  if (-not $fingerprintValidator.StartsWith($repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
      -not (Test-Path -LiteralPath $fingerprintValidator -PathType Leaf)) { throw 'Fingerprint validator must be a file inside this repository' }
}
$session = Get-Content -Raw -LiteralPath $sessionPath | ConvertFrom-Json
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
New-Item -ItemType Directory -Path $logs -Force | Out-Null
Push-Location $src
try {
  if ($isFingerprint) {
    # A failed metadata update can still change recoverable GN outputs.
    Output 'cache_may_change' 'true'
    $auditArgs = @('--ensure-manifest', '--manifest-timeout-seconds', '1800', '--plan-timeout-seconds', '600')
    $selectedCheckpoint = Get-Content -LiteralPath $selectionPath -Raw | ConvertFrom-Json
    $previousAudit = Join-Path $logs "cache-audit-stage-$($selectedCheckpoint.stage).json"
    if (Test-Path -LiteralPath $previousAudit -PathType Leaf) {
      $auditArgs += @('--previous-audit', $previousAudit)
    }
    foreach ($target in $targets) { $auditArgs += @('--target', $target) }
    Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_cache_audit.py') --ninja $ninja --out $out @auditArgs --report (Join-Path $logs "cache-audit-stage-$Stage.json") }
  } else {
    Checked { & (Join-Path $out 'gn.exe') gen $out --fail-on-unused-args 2>&1 | Tee-Object -FilePath (Join-Path $logs "gn-stage-$Stage.log") }
    Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_cache_audit.py') --ninja $ninja --out $out --refresh-manifest --target chrome --target native_passkey_unittests --report (Join-Path $logs "cache-audit-stage-$Stage.json") }
  }
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
  Output 'cache_may_change' $(if ($audit.pending_actions -gt 0 -or ($isFingerprint -and ($sourceChanged -or $audit.manifest_refreshed))) { 'true' } else { 'false' })
  $ninjaArguments = @('-C', $out, '-j', $jobs)
  if ($isFingerprint) {
    # Collect a bounded set of independent compiler errors and keep successful
    # objects, instead of paying another full checkpoint transfer per error.
    # Any compiler failure still fails this stage and blocks candidate delivery.
    $ninjaArguments += @('-k', '20')
  }
  $process = Start-Process -FilePath $ninja -ArgumentList ($ninjaArguments + $targets) -WorkingDirectory $src -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errorLog -PassThru
  $processHandle = $process.Handle
  $clock = [Diagnostics.Stopwatch]::StartNew()
  $timedOut = $false
  $nextTelemetrySeconds = 60
  Invoke-BuildTelemetry $process.Id
  while (-not $process.WaitForExit(10000)) {
    if ($clock.Elapsed.TotalMinutes -ge $compileBudgetMinutes) {
      Write-Host 'Saving progress at the compile time budget'
      & taskkill.exe /PID $process.Id /T /F | Out-Null
      $process.WaitForExit()
      $timedOut = $true
      break
    }
    if ($isFingerprint -and $clock.Elapsed.TotalSeconds -ge $nextTelemetrySeconds) {
      Invoke-BuildTelemetry $process.Id
      $nextTelemetrySeconds += 60
    } elseif (-not $isFingerprint -and [int]$clock.Elapsed.TotalSeconds % 60 -lt 11) {
      Get-Content -LiteralPath $log -Tail 3 -ErrorAction SilentlyContinue
    }
  }
  Invoke-BuildTelemetry $process.Id
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
    if ($env:GITHUB_RUN_ID -notmatch '^[1-9][0-9]*$' -or
        $env:GITHUB_RUN_ATTEMPT -notmatch '^[1-9][0-9]*$') {
      throw 'Native smoke evidence requires the actual GitHub run and attempt'
    }
    $smokeReport = Join-Path $logs "candidate-smoke-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT-stage-$Stage\report.json"
    Checked { python -X utf8 (Join-Path $repo 'tools\verify_windows_bundle.py') --bundle 'C:\c\dist\chromix' --arch x64 --native --version '155.0.8059.39' --report $smokeReport | Select-Object -Last 12 }
    Copy-Item -LiteralPath $smokeReport -Destination 'C:\c\dist\candidate-smoke.json'
    Checked { python -m pip install --disable-pip-version-check -r (Join-Path $repo 'tools\google-services-requirements.txt') }
    $googleReport = Join-Path $logs "google-services-runtime-stage-$Stage.json"
    Checked { python -B -X utf8 (Join-Path $repo 'tools\verify_google_services_runtime.py') --browser 'C:\c\dist\chromix\chrome.exe' --report $googleReport }
    Copy-Item -LiteralPath $googleReport -Destination 'C:\c\dist\google-services-runtime.json'
    Copy-Item -LiteralPath (Join-Path $work 'google-services-source.json') -Destination 'C:\c\dist\google-services-source.json'
    if ($isFingerprint) {
      $modesReport = 'C:\c\dist\fingerprint-modes-validation.json'
      Checked { & $fingerprintValidator -Out $out -Bundle 'C:\c\dist\chromix' -Report $modesReport }
      Checked { python -B -X utf8 (Join-Path $repo 'tools\verify_google_services_candidate.py') --fingerprint-tests-json $modesReport --bundle 'C:\c\dist\chromix' }
    }
    Checked { & "$PSScriptRoot\package-win.ps1" -Out $out -Dest 'C:\c\dist' -Arch x64 -ArchiveOnly -ExpectedBuildRecordSHA256 $buildRecordHash }
    Output 'finished' 'true'
  }
} finally { Pop-Location }

# Checkpoint creation is a separate workflow step. A compiler error remains a
# failed build, while the successfully compiled objects can still be uploaded.
