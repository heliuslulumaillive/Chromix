param(
  [Parameter(Mandatory)]
  [ValidateSet('Restore', 'Build', 'Checkpoint')]
  [string]$Phase,
  [int]$Stage = 1,
  [string]$ResumeRun = '',
  [string]$Repository = $env:GITHUB_REPOSITORY,
  [int]$CompileMinutes = 190
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

if ($Phase -eq 'Restore') {
  New-Item -ItemType Directory -Path $restore -Force | Out-Null
  # Leave time inside the 355-minute job limit to package and upload the tree.
  @{ started_at = [DateTimeOffset]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath 'C:\google-build-session.json'
  # These are disposable hosted-runner caches, not files on the owner's PC.
  foreach ($unused in @('C:\Android', 'C:\Program Files\Android', 'C:\Program Files (x86)\Android', 'C:\ghcup', 'C:\Miniconda3', 'C:\ProgramData\chocolatey\cache')) {
    if (Test-Path -LiteralPath $unused) { Remove-OwnedDirectory $unused $unused }
  }
  if (Test-Path -LiteralPath $root) { throw 'Checkpoint destination already exists' }
  if (-not $ResumeRun) {
    $recent = & $gh api "repos/$Repository/actions/artifacts?per_page=100" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect recent checkpoints' }
    $complete = @($recent.artifacts | Where-Object { $_.name -match '^google-win155-checkpoint-s[0-9]+$' -and -not $_.expired } | Sort-Object created_at -Descending)
    if ($complete.Count) {
      $ResumeRun = [string]$complete[0].workflow_run.id
      Write-Host "Using the newest completed Google-service checkpoint from run $ResumeRun"
    } else {
      # The first fixed run can reuse the old worker's objects once that workflow
      # has completed successfully, even though it predates completion manifests.
      $successful = & $gh api "repos/$Repository/actions/workflows/build-google-services-win155.yml/runs?status=success&per_page=10" | ConvertFrom-Json
      if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect legacy checkpoint runs' }
      foreach ($previousRun in $successful.workflow_runs) {
        $previous = & $gh api "repos/$Repository/actions/runs/$($previousRun.id)/artifacts?per_page=100" | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect legacy checkpoint artifacts' }
        if (@($previous.artifacts | Where-Object { $_.name -match '^google-win155-tree-s[0-9]+-part1$' -and -not $_.expired }).Count) {
          $ResumeRun = [string]$previousRun.id
          Write-Host "Using the last successful legacy checkpoint from run $ResumeRun"
          break
        }
      }
    }
  }
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
      # Explicit resumes also accept checkpoints produced before manifests existed;
      # 7-Zip must still extract every volume successfully before source mutation.
      Write-Warning 'Resuming a legacy checkpoint without a completion manifest'
      $latest = ($available | ForEach-Object { [int]([regex]::Match($_.name, '-s([0-9]+)-').Groups[1].Value) } | Measure-Object -Maximum).Maximum
      $names = @($available | Where-Object { $_.name -match "^google-win155-tree-s$latest-part[0-9]+$" } | Sort-Object name | ForEach-Object { $_.name })
    }
  } else {
    $sourceRepo = 'xiaozhou26/Chromix'
    $sourceRun = '38387174079'
    $names = @('win-x64-tree-s8-attempt-1-part1', 'win-x64-tree-s8-attempt-1-part2')
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

  $patch = Join-Path $src 'third_party\git\usr\bin\patch.exe'
  if (-not (Test-Path -LiteralPath $patch)) { $patch = 'C:\Program Files\Git\usr\bin\patch.exe' }
  Checked { python -X utf8 (Join-Path $repo 'tools\prepare_google_services.py') --work $work --repo $repo --patch-bin $patch }
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
$CompileMinutes = [Math]::Min($CompileMinutes, [Math]::Floor(355 - $elapsed - 95))
if ($CompileMinutes -lt 5) {
  Write-Warning 'Cache restoration consumed the compile budget; saving a prepared checkpoint for the next stage'
  return
}
Write-Host "Compile budget: $CompileMinutes minutes; reserving 95 minutes for packaging and checkpoint upload"

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
  Checked { & (Join-Path $out 'gn.exe') gen $out --fail-on-unused-args }
  $log = Join-Path $logs "ninja-stage-$Stage.log"
  $errorLog = Join-Path $logs "ninja-stage-$Stage.stderr.log"
  $process = Start-Process -FilePath $ninja -ArgumentList @('-C', $out, '-j', $jobs, 'chrome') -WorkingDirectory $src -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errorLog -PassThru
  $processHandle = $process.Handle
  $clock = [Diagnostics.Stopwatch]::StartNew()
  $timedOut = $false
  while (-not $process.WaitForExit(10000)) {
    if ($clock.Elapsed.TotalMinutes -ge $CompileMinutes) {
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
    if (Test-Path -LiteralPath 'C:\c\dist') { throw 'Expected a fresh candidate output directory' }
    Checked { & "$PSScriptRoot\package-win.ps1" -Out $out -Dest 'C:\c\dist' -Arch x64 }
    Copy-Item -LiteralPath (Join-Path $work 'google-services-source.json') -Destination 'C:\c\dist\google-services-source.json'
    Output 'finished' 'true'
  }
} finally { Pop-Location }

# Checkpoint creation is a separate workflow step. A compiler error remains a
# failed build, while the successfully compiled objects can still be uploaded.
