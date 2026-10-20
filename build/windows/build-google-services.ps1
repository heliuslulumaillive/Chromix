param(
  [Parameter(Mandatory)]
  [ValidateSet('Prerequisites', 'Restore', 'Build', 'Checkpoint')]
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

if ($Phase -eq 'Prerequisites') {
  # Leave time inside the 355-minute job limit to package and upload the tree.
  @{ started_at = [DateTimeOffset]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath 'C:\google-build-session.json'
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
  if (-not $ResumeRun) { throw 'An explicit completed checkpoint is required; no cold or upstream fallback is allowed' }
  New-Item -ItemType Directory -Path $restore -Force | Out-Null
  $manifest = $null
  if ($ResumeRun) {
    $sourceRepo = $Repository
    $sourceRun = $ResumeRun
    $artifacts = & $gh api "repos/$Repository/actions/runs/$ResumeRun/artifacts?per_page=100" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect resume artifacts' }
    $available = @($artifacts.artifacts | Where-Object { $_.name -match '^google-win155-tree-s([0-9]+)-part[0-9]+$' -and -not $_.expired })
    if (-not $available) { throw 'No resumable Google-service checkpoint found' }
    $committed = @($artifacts.artifacts | Where-Object { $_.name -match '^google-win155-checkpoint-s[0-9]+$' -and -not $_.expired })
    if ($committed.Count) {
      $latest = ($committed | ForEach-Object { [int]([regex]::Match($_.name, '-s([0-9]+)$').Groups[1].Value) } | Measure-Object -Maximum).Maximum
      $manifestDir = Join-Path $restore 'manifest'
      Checked { & $gh run download $sourceRun --repo $sourceRepo --name "google-win155-checkpoint-s$latest" --dir $manifestDir }
      $manifest = Get-Content -Raw -LiteralPath (Join-Path $manifestDir 'checkpoint.json') | ConvertFrom-Json
      if ($manifest.format_version -ne 1 -or $manifest.chromium_version -ne '155.0.8059.39' -or $manifest.stage -ne $latest -or [string]$manifest.run_id -ne $sourceRun) {
        throw 'Checkpoint manifest does not match this build'
      }
      if ($manifest.parts.Count -lt 1 -or $manifest.parts.Count -gt 4) { throw 'Invalid checkpoint volume count' }
      $names = @(1..$manifest.parts.Count | ForEach-Object { "google-win155-tree-s$latest-part$_" })
      foreach ($name in $names) {
        if ($name -notin $available.name) { throw "Completed checkpoint is missing volume artifact: $name" }
      }
    } else {
      throw 'Selected run has no complete checkpoint manifest; incomplete volumes cannot be resumed'
    }
  }
  foreach ($name in $names) {
    Write-Host "Downloading checkpoint volume: $name"
    Checked { & $gh run download $sourceRun --repo $sourceRepo --name $name --dir $restore }
  }
  if ($manifest) {
    for ($index = 0; $index -lt $manifest.parts.Count; $index++) {
      $part = $manifest.parts[$index]
      $expected = 'tree.7z.{0:D3}' -f ($index + 1)
      if ($part.name -cne $expected) { throw 'Unexpected checkpoint volume path' }
      $file = Get-Item -LiteralPath (Join-Path $restore $expected)
      if ($file.Length -ne $part.bytes) { throw "Checkpoint volume size mismatch: $expected" }
    }
  }
  $first = @(Get-ChildItem -LiteralPath $restore -Recurse -File -Filter 'tree.7z.001')
  if ($first.Count -ne 1) { throw 'Expected one multi-volume checkpoint archive' }
  Write-Host 'Extracting checkpoint (7-Zip verifies data while extracting)'
  Checked { & $seven x $first[0].FullName "-o$root" -y -bsp0 | Select-Object -Last 8 }
  Remove-OwnedDirectory $restore 'C:\google-restore'
  if (-not (Test-Path -LiteralPath (Join-Path $out 'build.ninja'))) { throw 'Compiled checkpoint is missing' }
  @{
    repository = $sourceRepo
    run_id = $sourceRun
    artifacts = $names
    checkpoint_commit = $(if ($manifest) { $manifest.commit } else { $null })
  } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $work 'google-services-checkpoint-source.json')

  # Use the same GNU patch tested before downloading, independent of the
  # runner PATH and the Git version bundled inside the donor snapshot.
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
  @{
    format_version = 1
    chromium_version = '155.0.8059.39'
    run_id = $env:GITHUB_RUN_ID
    stage = $Stage
    commit = $env:GITHUB_SHA
    parts = @($volumes | ForEach-Object { @{ name = $_.Name; bytes = $_.Length } })
  } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $parts 'checkpoint.json')
  Output 'parts' ([string]$volumes.Count)
  Output 'checkpoint' 'true'
  return
}

Output 'finished' 'false'
$session = Get-Content -Raw -LiteralPath 'C:\google-build-session.json' | ConvertFrom-Json
$elapsed = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($session.started_at)).TotalMinutes
$compileBudgetMinutes = [Math]::Min($CompileMinutes, [Math]::Floor(355 - $elapsed - $FinalizeReserveMinutes))
if ($compileBudgetMinutes -lt 5) {
  Write-Warning 'Cache restoration consumed the compile budget; saving a prepared checkpoint for the next stage'
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
$environment = & $env:ComSpec /d /s /c "`"$devcmd`" -arch=x64 -host_arch=x64 >nul && set"
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
  Checked { python -B -X utf8 (Join-Path $repo 'tools\google_services_cache_audit.py') --ninja $ninja --out $out --target chrome --target device_unittests --report (Join-Path $logs "cache-audit-stage-$Stage.json") }
  # GN and the dry run can take minutes. Count them before reserving the tail.
  $elapsed = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($session.started_at)).TotalMinutes
  $compileBudgetMinutes = [Math]::Min($CompileMinutes, [Math]::Floor(355 - $elapsed - $FinalizeReserveMinutes))
  if ($compileBudgetMinutes -lt 5) {
    Write-Warning 'Preparation consumed the compile budget; preserving the prepared checkpoint'
    return
  }
  Write-Host "Compile budget: $compileBudgetMinutes minutes; reserving $FinalizeReserveMinutes minutes for packaging and checkpoint upload"
  $env:NINJA_STATUS = '[%f/%t] active=%r rate=%c/s elapsed=%es '
  $log = Join-Path $logs "ninja-stage-$Stage.log"
  $errorLog = Join-Path $logs "ninja-stage-$Stage.stderr.log"
  $process = Start-Process -FilePath $ninja -ArgumentList @('-C', $out, '-j', $jobs, 'chrome', 'device_unittests') -WorkingDirectory $src -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errorLog -PassThru
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
    Checked { & (Join-Path $out 'device_unittests.exe') '--gtest_filter=WinAuthenticatorTest.*' '--test-launcher-jobs=1' "--test-launcher-summary-output=$nativeTests" 2>&1 | Tee-Object -FilePath (Join-Path $logs "native-passkey-unittests-stage-$Stage.log") }
    if (Test-Path -LiteralPath 'C:\c\dist') { throw 'Expected a fresh candidate output directory' }
    Checked { & "$PSScriptRoot\package-win.ps1" -Out $out -Dest 'C:\c\dist' -Arch x64 }
    Copy-Item -LiteralPath $nativeTests -Destination 'C:\c\dist\native-passkey-unittests.json'
    $smokeReport = Join-Path $logs "candidate-smoke-stage-$Stage\report.json"
    Checked { python -X utf8 (Join-Path $repo 'tools\verify_windows_bundle.py') --bundle 'C:\c\dist\chromix' --arch x64 --native --version '155.0.8059.39' --report $smokeReport | Select-Object -Last 12 }
    Copy-Item -LiteralPath $smokeReport -Destination 'C:\c\dist\candidate-smoke.json'
    Checked { python -m pip install --disable-pip-version-check -r (Join-Path $repo 'tools\google-services-requirements.txt') }
    $googleReport = Join-Path $logs "google-services-runtime-stage-$Stage.json"
    Checked { python -B -X utf8 (Join-Path $repo 'tools\verify_google_services_runtime.py') --browser 'C:\c\dist\chromix\chrome.exe' --report $googleReport }
    Copy-Item -LiteralPath $googleReport -Destination 'C:\c\dist\google-services-runtime.json'
    Copy-Item -LiteralPath (Join-Path $work 'google-services-source.json') -Destination 'C:\c\dist\google-services-source.json'
    Output 'finished' 'true'
  }
} finally { Pop-Location }

# Checkpoint creation is a separate workflow step. A compiler error remains a
# failed build, while the successfully compiled objects can still be uploaded.
