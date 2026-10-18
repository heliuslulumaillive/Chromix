param(
  [int]$Stage = 1,
  [string]$ResumeRun = '',
  [string]$Repository = $env:GITHUB_REPOSITORY,
  [int]$CompileMinutes = 190
)
$ErrorActionPreference = 'Stop'
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

Output 'finished' 'false'
# These are disposable hosted-runner caches, not files on the owner's PC.
foreach ($unused in @('C:\Android', 'C:\Program Files\Android', 'C:\Program Files (x86)\Android', 'C:\ghcup', 'C:\Miniconda3', 'C:\ProgramData\chocolatey\cache')) {
  if (Test-Path -LiteralPath $unused) { Remove-OwnedDirectory $unused $unused }
}
if (Test-Path -LiteralPath $root) { throw 'Checkpoint destination already exists' }
New-Item -ItemType Directory -Path $restore -Force | Out-Null
if ($ResumeRun) {
  $sourceRepo = $Repository
  $sourceRun = $ResumeRun
  $artifacts = & $gh api "repos/$Repository/actions/runs/$ResumeRun/artifacts?per_page=100" | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect resume artifacts' }
  $available = @($artifacts.artifacts | Where-Object { $_.name -match '^google-win155-tree-s([0-9]+)-part[0-9]+$' -and -not $_.expired })
  if (-not $available) { throw 'No resumable Google-service checkpoint found' }
  $latest = ($available | ForEach-Object { [int]([regex]::Match($_.name, '-s([0-9]+)-').Groups[1].Value) } | Measure-Object -Maximum).Maximum
  $names = @($available | Where-Object { $_.name -match "^google-win155-tree-s$latest-part[0-9]+$" } | Sort-Object name | ForEach-Object { $_.name })
} else {
  $sourceRepo = 'xiaozhou26/Chromix'
  $sourceRun = '38387174079'
  $names = @('win-x64-tree-s8-attempt-1-part1', 'win-x64-tree-s8-attempt-1-part2')
}
foreach ($name in $names) {
  Write-Host "Downloading checkpoint volume: $name"
  Checked { & $gh run download $sourceRun --repo $sourceRepo --name $name --dir $restore }
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

# Keep a reusable checkpoint even after a successful compile. It is build input,
# not an automatically published browser release.
New-Item -ItemType Directory -Path $parts -Force | Out-Null
Push-Location $root
try {
  Checked { & $seven a -v8g -mx=1 -mtc=on (Join-Path $parts 'tree.7z') chromix -bsp0 | Select-Object -Last 8 }
} finally { Pop-Location }
if (@(Get-ChildItem -LiteralPath $parts -Filter 'tree.7z.*' -File).Count -gt 4) {
  throw 'Checkpoint exceeds the four configured upload slots'
}
Output 'checkpoint' 'true'
