#Requires -Version 7.0
param(
  [ValidateSet('Build','Checkpoint')][string]$Phase = 'Build',
  [ValidateSet('fresh','resume','upstream')][string]$Operation = 'fresh',
  [ValidateRange(1,99)][int]$Stage = 1,
  [string]$SourceRun = ''
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -cne 'true' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted') {
  throw 'Security build requires a disposable GitHub-hosted Windows runner; local builds are forbidden.'
}
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$root = 'C:\c'
$work = 'C:\c\chromix'
$src = Join-Path $work 'src'
$out = Join-Path $src 'out\Default'
$evidence = 'C:\security-evidence'
$parts = 'C:\security-parts'
$restore = 'C:\security-restore'
$upstreamCache = 'C:\security-upstream'
$statePath = Join-Path $evidence 'stage-state.json'
$version = '156.0.8078.79'
$utf8 = [Text.UTF8Encoding]::new($false)
$seven = (Get-Command 7z.exe -ErrorAction Stop).Source
$python = (Get-Command python.exe -ErrorAction Stop).Source
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
function Output([string]$Name,[string]$Value) {
  if ($env:GITHUB_OUTPUT) { Add-Content -LiteralPath $env:GITHUB_OUTPUT -Value "$Name=$Value" }
}
function Json([string]$Path,$Data) {
  [IO.File]::WriteAllText($Path, ($Data | ConvertTo-Json -Depth 30) + "`n", $utf8)
}
function Assert-Plain([string]$Path) {
  $cursor = [IO.Path]::GetFullPath($Path)
  while ($cursor) {
    if (Test-Path -LiteralPath $cursor) {
      if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'CI path redirects' }
    }
    $cursor = [IO.Path]::GetDirectoryName($cursor)
  }
}
function Checked([scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "Checked command failed: $LASTEXITCODE" }
}
foreach ($path in @($root,$work,$src,$out,$evidence,$parts,$restore,$upstreamCache)) { Assert-Plain $path }
$null = New-Item -ItemType Directory -Path $evidence -Force

if ($Phase -eq 'Checkpoint') {
  if (-not (Test-Path -LiteralPath $statePath) -or -not (Test-Path -LiteralPath $work)) { return }
  $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
  if (Test-Path -LiteralPath $parts) { throw 'Checkpoint destination must be fresh' }
  $null = New-Item -ItemType Directory -Path $parts
  $treeBytes = (Get-ChildItem -LiteralPath $work -File -Force -Recurse | Measure-Object Length -Sum).Sum
  $minimumFree = [Math]::Max(24GB, [Math]::Ceiling($treeBytes * 0.25 + 5GB))
  if ((Get-PSDrive C).Free -lt $minimumFree) { throw 'Insufficient free space to preserve the complete checkpoint' }
  Push-Location $root
  try {
    Checked { & $seven a -v8g -mx=1 -mtc=on (Join-Path $parts 'tree.7z') chromix -bsp0 | Select-Object -Last 8 }
  } finally { Pop-Location }
  $manifestArgs = @('-B','-X','utf8',(Join-Path $repo 'tools\security_checkpoint.py'),'create',
    '--repo',$repo,'--work',$work,'--parts',$parts,'--stage',"$Stage",'--output',(Join-Path $parts 'checkpoint.json'))
  if ($saved.resume_safe) { $manifestArgs += '--resume-safe' }
  Checked { & $python @manifestArgs }
  $count = @(Get-ChildItem -LiteralPath $parts -Filter 'tree.7z.*' -File).Count
  Output parts "$count"
  Output checkpoint 'true'
  Output resume_safe ($saved.resume_safe.ToString().ToLowerInvariant())
  return
}

if (-not $env:SECURITY_JOB_STARTED) { throw 'The worker must record the total job budget before checkout' }
$started = [DateTimeOffset]::Parse($env:SECURITY_JOB_STARTED)
$deadline = $started.AddMinutes(300) # 55 minutes remain for checkpoint compression/upload.
$script:commandNumber = 0
$state = [ordered]@{ stage=$Stage; operation=$Operation; status='preparing'; resume_safe=$false; finished=$false }
Json $statePath $state
Output finished 'false'
function Invoke-Owned([string]$Label,[string[]]$Command,[int]$LimitSeconds=18000,[string]$WorkingDirectory=$repo,[switch]$GitHubReadToken) {
  $remaining = [int]($deadline - [DateTimeOffset]::UtcNow).TotalSeconds
  if ($remaining -lt 30) { throw 'SECURITY_BUDGET_STOP' }
  $limit = [Math]::Min($LimitSeconds, $remaining)
  $script:commandNumber++
  $stem = '{0:D3}-{1}' -f $script:commandNumber,$Label
  $log = Join-Path $evidence "$stem.log"
  $report = Join-Path $evidence "$stem.json"
  $tokenOption = @()
  if ($GitHubReadToken) { $tokenOption += '--github-read-token' }
  & $python -B -X utf8 (Join-Path $repo 'tools\security_run.py') --cwd $WorkingDirectory --timeout $limit --log $log --report $report @tokenOption -- @Command
  $rc = $LASTEXITCODE
  if ($rc -eq 124) { throw 'SECURITY_BUDGET_STOP' }
  if ($rc -ne 0) {
    Get-Content -LiteralPath $log -Tail 35
    throw "Security stage command failed: $Label ($rc)"
  }
}

try {
  if ($Operation -in @('fresh','upstream')) {
    if ($SourceRun -or (Test-Path -LiteralPath $work)) { throw 'Fresh Windows 156 preparation cannot adopt an existing source tree or donor' }
    $null = New-Item -ItemType Directory -Path $work -Force
    # Only disposable hosted-runner tools, checked against exact absolute paths.
    foreach ($literal in @('C:\Android','C:\Program Files\Android','C:\ghcup','C:\Miniconda3','C:\Program Files\LLVM')) {
      if (Test-Path -LiteralPath $literal) {
        Assert-Plain $literal
        $resolved = (Resolve-Path -LiteralPath $literal).Path.TrimEnd('\')
        if ($resolved -cne $literal) { throw 'Unexpected hosted-runner cleanup resolution' }
        Remove-Item -LiteralPath $resolved -Recurse -Force
      }
    }
  } else {
    if (-not $SourceRun -or (Test-Path -LiteralPath $work)) { throw 'Resume requires a source run and fresh runner work path' }
    Checked { & $python -B -X utf8 (Join-Path $repo 'tools\security_checkpoint.py') restore --repo $repo --destination $restore --run $SourceRun --stage $Stage }
    Checked { & $python -B -X utf8 (Join-Path $repo 'tools\security_checkpoint.py') verify-archive --archive (Join-Path $restore 'tree.7z.001') --sevenzip $seven }
    $null = New-Item -ItemType Directory -Path $root -Force
    Checked { & $seven x (Join-Path $restore 'tree.7z.001') "-o$root" -y -bsp0 | Select-Object -Last 8 }
    Checked { & $python -B -X utf8 (Join-Path $repo 'tools\security_checkpoint.py') verify-tree --work $work --manifest (Join-Path $restore 'checkpoint.json') }
    # Free only the exact owned transfer directory after digest-verified extraction.
    Assert-Plain $restore
    if ((Resolve-Path -LiteralPath $restore).Path -cne 'C:\security-restore') { throw 'Unexpected restore cleanup path' }
    Remove-Item -LiteralPath $restore -Recurse -Force
  }
  $pins = & "$PSScriptRoot\read-platform-pins.ps1" -Repo $repo
  if ($pins.ChromiumVersion -cne $version -or
      $pins.UngoogledCommit -cne '28a211ab4ab6b83d919966a4f58a6df23cf4fb29' -or
      $pins.UngoogledWindowsCommit -cne '8492884eb30fdd9417ea7816aa92def805c97c74') { throw 'Security lane target pins differ' }
  Invoke-Owned 'sdk' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\ensure-windows-sdk.ps1",'-Arch','x64','-ChromiumVersion',$version,'-Install')
  $env:DEPOT_TOOLS_WIN_TOOLCHAIN = '0'
  $env:DEPOT_TOOLS_METRICS = '0'
  $env:DEPOT_TOOLS_COLLECT_METRICS = '0'
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  if (-not $vs) { throw 'Visual Studio x64 C++ toolchain is missing' }
  $devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
  $vsenv = & $env:ComSpec /d /s /c "`"$devcmd`" -arch=x64 -host_arch=x64 -winsdk=10.0.28000.0 >nul && set"
  if ($LASTEXITCODE -ne 0) { throw 'Visual Studio environment failed' }
  foreach ($line in $vsenv) { if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1],$Matches[2],'Process') } }
  if ($Operation -eq 'upstream') {
    Invoke-Owned 'exact-upstream-download' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\fetch_upstream_cache.py'),
      '--platform','windows','--arch','x64','--destination',$upstreamCache,'--run-id','41098495420') -GitHubReadToken
    $download = Get-Content -LiteralPath (Join-Path $upstreamCache 'result.json') -Raw | ConvertFrom-Json
    if ($download.status -cne 'hit') { throw 'Exact 156 upstream snapshot unavailable; explicit fresh operation is required, no fallback performed.' }
    Invoke-Owned 'exact-upstream-restore' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\restore_upstream_cache.py'),
      '--phase','restore','--platform','windows','--arch','x64','--workdir',$work,'--cache-dir',$upstreamCache)
    if (-not (Test-Path -LiteralPath (Join-Path $src '.chromix-upstream-restored.json'))) { throw 'Full upstream restoration was not verified; refusing compilation' }
  }
  if ($Operation -in @('fresh','upstream')) {
    Invoke-Owned 'base-prepare' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\prepare-ungoogled.ps1",'-Root',$work,'-Repo',$repo,'-Arch','x64',
      '-DeadlineEpoch',"$($deadline.ToUnixTimeSeconds())",'-ReserveMinutes','10')
  }
  if ($Operation -eq 'fresh') {
    $core = Join-Path $work 'tooling\ungoogled-chromium'
    $windows = Join-Path $work 'tooling\ungoogled-chromium-windows'
    Invoke-Owned 'domain-substitution' @($python,'-B','-X','utf8',(Join-Path $core 'utils\domain_substitution.py'),'apply',
      '-r',(Join-Path $core 'domain_regex.list'),'-f',(Join-Path $windows 'domain_substitution.list'),
      '-c',(Join-Path $work 'domain_substitution_cache.tar.gz'),$src)
  }
  if ($Operation -eq 'resume' -and (Test-Path -LiteralPath (Join-Path $src '.chromix-upstream-restored.json'))) {
    Invoke-Owned 'restored-environment-inspect' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\prepare_restored_build.py'),
      '--phase','inspect','--platform','windows','--arch','x64','--workdir',$work)
  }
  $patch = Join-Path $src 'third_party\git\usr\bin\patch.exe'
  # Never repeat base preparation/domain replacement after service restoration.
  Invoke-Owned 'service-and-fingerprint-restore' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\prepare_google_services.py'),
    '--repo',$repo,'--work',$work,'--patch-bin',$patch)
  $null = New-Item -ItemType Directory -Path $out -Force
  $merge = @($python,'-B','-X','utf8',(Join-Path $repo 'tools\merge_gn_args.py'),(Join-Path $out 'args.gn'))
  if (Test-Path -LiteralPath (Join-Path $src '.chromix-upstream-restored.json')) {
    $merge += @('--preserve-pgo-from',(Join-Path $out 'args.gn'),(Join-Path $out 'args.gn'))
  }
  $merge += @(
    (Join-Path $work 'tooling\ungoogled-chromium\flags.gn'),(Join-Path $work 'tooling\ungoogled-chromium-windows\flags.windows.gn'),
    (Join-Path $repo 'build\args.windows.gn'),(Join-Path $repo 'build\args.security.gn'))
  Invoke-Owned 'merge-gn' $merge
  Invoke-Owned 'record-gn-before-tools' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_build_record.py'),
    '--repo',$repo,'--work',$work,'--output',(Join-Path $evidence 'build-inputs-before-gn.json'))
  $state.resume_safe = $true
  Json $statePath $state
  $env:PATH = "$(Join-Path $src 'third_party\llvm-build\Release+Asserts\bin');$(Join-Path $src 'third_party\ninja');$(Join-Path $src 'third_party\node\win');$env:PATH"
  & "$PSScriptRoot\configure-node.ps1" -NodePath (Join-Path $src 'third_party\node\win\node.exe')
  $ninja = Join-Path $src 'third_party\ninja\ninja.exe'
  if (Test-Path -LiteralPath (Join-Path $src '.chromix-upstream-restored.json')) {
    $ninja = & $python -B -X utf8 (Join-Path $repo 'tools\restore_ninja.py') --workdir $work --platform windows --arch x64
    if ($LASTEXITCODE -ne 0 -or -not $ninja) { throw 'Restored Ninja format compatibility failed' }
    $env:NINJA = $ninja
  }
  if (-not (Test-Path -LiteralPath (Join-Path $src 'third_party\rust-toolchain\bin\bindgen.exe'))) {
    Invoke-Owned 'bindgen' @($python,'-B','-X','utf8',(Join-Path $src 'tools\rust\build_bindgen.py'),'--skip-test') -WorkingDirectory $src
  }
  $gn = Join-Path $out 'gn.exe'
  if (-not (Test-Path -LiteralPath $gn)) {
    Invoke-Owned 'gn-bootstrap' @($python,'-B','-X','utf8',(Join-Path $src 'tools\gn\bootstrap\bootstrap.py'),'-o',$gn,'--skip-generate-buildfiles') -WorkingDirectory $src
  }
  if (Test-Path -LiteralPath (Join-Path $src '.chromix-upstream-restored.json')) {
    Invoke-Owned 'restored-environment-finish' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\prepare_restored_build.py'),
      '--phase','finish','--platform','windows','--arch','x64','--workdir',$work)
  }
  Invoke-Owned 'metagen-configure' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\install_windows_metagen_bindings.py'),
    '--configure','--src',$src,'--out',$out,'--chromium-version',$version)
  Invoke-Owned 'gn-generate' @($gn,'gen',$out,"--root=$src",'--fail-on-unused-args')
  Invoke-Owned 'metagen-verify' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\install_windows_metagen_bindings.py'),
    '--src',$src,'--out',$out,'--ninja',$ninja,'--chromium-version',$version,'--download')
  Invoke-Owned 'record-gn-before-compile' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_build_record.py'),
    '--repo',$repo,'--work',$work,'--output',(Join-Path $evidence 'build-inputs.json'))
  Invoke-Owned 'pending-actions' @($ninja,'-C',$out,'-n','chrome','native_passkey_unittests','uxr_surface_modes_tests')
  $resources = & $python -B -X utf8 (Join-Path $repo 'tools\build_resources.py') | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0) { throw 'Resource sizing failed' }
  Json (Join-Path $evidence 'resources.json') $resources
  $env:NINJA_STATUS = '[%f/%t] active=%r rate=%c/s elapsed=%es '
  $state.status = 'compiling'; Json $statePath $state
  Invoke-Owned 'ninja' @($ninja,'-C',$out,'-j',"$($resources.ninja_jobs)",'-k','20','chrome','native_passkey_unittests','uxr_surface_modes_tests')
  Invoke-Owned 'native-passkey' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\run-native-passkey-tests.ps1",'-Executable',(Join-Path $out 'native_passkey_unittests.exe'),
    '-Summary',(Join-Path $evidence 'native-passkey-unittests.json'),'-Log',(Join-Path $evidence 'native-passkey.log'),
    '-Receipt',(Join-Path $evidence 'native-passkey-launcher.json'),'-WorkingDirectory',$src)
  Invoke-Owned 'package' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\package-win.ps1",'-Out',$out,'-Dest','C:\c\dist','-Arch','x64','-SkipArchive')
  $bundle = 'C:\c\dist\chromix'
  $buildHash = (Get-FileHash -LiteralPath (Join-Path $bundle 'google-services-build.json') -Algorithm SHA256).Hash.ToLowerInvariant()
  Invoke-Owned 'browser-smoke' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\verify_windows_bundle.py'),'--bundle',$bundle,'--arch','x64','--native','--version',$version,'--report',(Join-Path $evidence 'candidate-smoke.json'))
  Invoke-Owned 'security-runtime' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\verify_security_runtime.py'),
    '--browser',(Join-Path $bundle 'chrome.exe'),'--source-record',(Join-Path $evidence 'build-inputs.json'),'--report',(Join-Path $evidence 'security-runtime.json'))
  Invoke-Owned 'accept-security-runtime' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\verify_google_services_candidate.py'),
    '--security-tests-json',(Join-Path $evidence 'security-runtime.json'),'--source-record',(Join-Path $evidence 'build-inputs.json'),
    '--bundle',$bundle,'--build-variant','fingerprint-modes-win156')
  Invoke-Owned 'google-runtime' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\verify_google_services_runtime.py'),'--browser',(Join-Path $bundle 'chrome.exe'),'--report',(Join-Path $evidence 'google-services-runtime.json'))
  Invoke-Owned 'fingerprint-runtime' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\verify-fingerprint-modes.ps1",'-Out',$out,'-Bundle',$bundle,'-Report',(Join-Path $evidence 'fingerprint-modes-validation.json'))
  Invoke-Owned 'accept-native-passkey' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\verify_google_services_candidate.py'),
    '--native-tests-json',(Join-Path $evidence 'native-passkey-unittests.json'),'--build-variant','fingerprint-modes-win156')
  Invoke-Owned 'accept-fingerprint' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\verify_google_services_candidate.py'),
    '--fingerprint-tests-json',(Join-Path $evidence 'fingerprint-modes-validation.json'),'--bundle',$bundle,'--build-variant','fingerprint-modes-win156')
  Invoke-Owned 'archive' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\package-win.ps1",'-Out',$out,'-Dest','C:\c\dist','-Arch','x64','-ArchiveOnly','-ExpectedBuildRecordSHA256',$buildHash)
  # Keep the artifact verifier's outer receipts beside the archive. These copies
  # are outside the immutable browser bundle and therefore do not rebaseline it.
  foreach ($name in @('build-inputs.json','native-passkey-unittests.json','native-passkey-launcher.json',
      'candidate-smoke.json','google-services-runtime.json','security-runtime.json','fingerprint-modes-validation.json',
      'modes-native.json','modes-runtime.json','fingerprint-modes-capabilities.json')) {
    Copy-Item -LiteralPath (Join-Path $evidence $name) -Destination (Join-Path 'C:\c\dist' $name)
  }
  Copy-Item -LiteralPath (Join-Path $work 'google-services-source.json') -Destination 'C:\c\dist\google-services-source.json'
  $state.finished = $true; $state.status = 'candidate-created'
  Json $statePath $state
  Output finished 'true'
} catch {
  $state.status = if ($_.Exception.Message -eq 'SECURITY_BUDGET_STOP') { 'budget-stop' } else { 'failed' }
  $state.error = $_.Exception.Message
  Json $statePath $state
  if ($state.status -ne 'budget-stop') { throw }
  Write-Host 'Stage budget reached; preserve the complete state before continuing.'
}
