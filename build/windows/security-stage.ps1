#Requires -Version 7.0
param(
  [ValidateSet('Build','Acquire','Extract','Prepare','Configure','Compile','Bundle','Verify','Archive','Checkpoint','Result')][string]$Phase = 'Build',
  [ValidateSet('fresh','resume','upstream','upgrade')][string]$Operation = 'fresh',
  [ValidateRange(1,99)][int]$Stage = 1,
  [string]$SourceRun = '',
  [ValidateRange(0,99)][int]$SourceStage = 0
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
$preflight = Join-Path $evidence 'checkpoint-preflight.json'
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

# These transition helpers are tested independently without running the CI entry point.
function Assert-PhasePredecessor([System.Collections.IDictionary]$State,[string]$NextPhase) {
  $order = @('Acquire','Extract','Prepare','Configure','Compile','Bundle','Verify','Archive')
  $index = [Array]::IndexOf($order, $NextPhase)
  if ($index -lt 0) { throw 'Unknown phase' }
  if ($State.status -in @('failed','budget-stop','candidate-created')) { throw 'Stage is already terminal' }
  $completed = @($State.completed)
  if ($completed.Count -ne $index) { throw 'Phase skipped or repeated' }
  for ($i = 0; $i -lt $index; $i++) {
    if ($completed[$i] -cne $order[$i]) { throw 'Phase completion order is invalid' }
  }
}
function Get-StageContinuation([System.Collections.IDictionary]$State,[bool]$CandidateDelivered,[bool]$ManifestDelivered,[bool]$RetainCheckpoint=$false) {
  $finished = $CandidateDelivered -and $State.status -ceq 'candidate-created' -and $State.finished -eq $true -and @($State.completed)[-1] -ceq 'Archive'
  $resume = -not $finished -and $State.status -ceq 'budget-stop' -and $State.resume_safe -eq $true -and $ManifestDelivered
  return @{ finished=$finished; resume_needed=$resume; checkpoint_ready=$ManifestDelivered; retention_failed=($finished -and $RetainCheckpoint -and -not $ManifestDelivered) }
}
function Get-CheckpointDiskBudget([System.Collections.IDictionary]$Manifest) {
  [long]$transferBytes = 0
  foreach ($part in @($Manifest.parts)) {
    if ([long]$part.bytes -le 0) { throw 'Invalid transfer volume size' }
    $transferBytes += [long]$part.bytes
  }
  $knownTree = $Manifest.Contains('tree_bytes')
  [long]$treeBytes = if ($knownTree) { $Manifest.tree_bytes } else { 0 }
  if ($transferBytes -le 0 -or $knownTree -and $treeBytes -le 0) { throw 'Invalid checkpoint disk budget' }
  return @{ compressed_bytes=$transferBytes; uncompressed_bytes=$treeBytes; tree_size_known=$knownTree;
    required_free_bytes=($transferBytes + $treeBytes + 24GB) }
}
function Clear-HostedToolSpace {
  # This allowlist is only reached behind the hosted-runner guard; never use
  # pipeline output or user input to construct recursive removal targets.
  foreach ($literal in @('C:\Android','C:\Program Files\Android','C:\ghcup','C:\Miniconda3','C:\Program Files\LLVM')) {
    if (Test-Path -LiteralPath $literal) {
      Assert-Plain $literal
      $resolved = (Resolve-Path -LiteralPath $literal).Path.TrimEnd('\')
      if ($resolved -cne $literal) { throw 'Unexpected hosted-runner cleanup resolution' }
      Remove-Item -LiteralPath $resolved -Recurse -Force
    }
  }
}
function Save-State {
  $temporary = $statePath + '.tmp'
  Json $temporary $state
  Move-Item -LiteralPath $temporary -Destination $statePath -Force
}
function Read-State {
  if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'Prior phase state is missing' }
  $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -AsHashtable
  if ($saved.schema_version -ne 2 -or $saved.stage -ne $Stage -or
      $saved.run_id -cne $env:GITHUB_RUN_ID -or $saved.source_commit -cne $env:GITHUB_SHA) {
    throw 'Phase state belongs to another job or source'
  }
  return $saved
}
if ($Phase -eq 'Result') {
  $result = @{ finished=$false; resume_needed=$false; checkpoint_ready=$false; retention_failed=$false }
  if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    $saved = Read-State
    $result = Get-StageContinuation $saved ($env:CANDIDATE_OUTCOME -ceq 'success') ($env:MANIFEST_OUTCOME -ceq 'success') ($env:RETAIN_CHECKPOINT -ceq 'true')
  }
  Output finished ($result.finished.ToString().ToLowerInvariant())
  Output resume_needed ($result.resume_needed.ToString().ToLowerInvariant())
  Output checkpoint_ready ($result.checkpoint_ready.ToString().ToLowerInvariant())
  if ($result.retention_failed) { throw 'Candidate delivered, but the requested reusable checkpoint is incomplete; retained-cache requirement failed.' }
  return
}
if ($Phase -eq 'Checkpoint') {
  Output checkpoint 'false'
  Output parts '0'
  Output resume_safe 'false'
  if (-not (Test-Path -LiteralPath $statePath) -or -not (Test-Path -LiteralPath $work)) { return }
  $saved = Read-State
  # Incomplete extraction/migration is reproducible from the original donor;
  # its small diagnostics are useful, a giant non-resumable archive is not.
  if ($saved.resume_safe -ne $true) {
    Json (Join-Path $evidence 'checkpoint-skipped.json') @{ reason='source-not-resume-safe'; status=$saved.status; phase=$saved.phase }
    Write-Host 'Retaining diagnostics and the original donor; partial source is not resume-safe.'
    return
  }
  if (Test-Path -LiteralPath $parts) { throw 'Checkpoint destination already exists; refusing duplicate compression' }
  $null = New-Item -ItemType Directory -Path $parts
  Json (Join-Path $work 'security-phase-state.json') $saved
  $treeBytes = (Get-ChildItem -LiteralPath $work -File -Force -Recurse | Measure-Object Length -Sum).Sum
  $minimumFree = [Math]::Max(24GB, [Math]::Ceiling($treeBytes * 0.25 + 5GB))
  if ((Get-PSDrive C).Free -lt $minimumFree) { throw 'Insufficient free space to preserve the complete checkpoint' }
  Push-Location $root
  try {
    Checked { & $seven a -v8g -mx=1 -mtc=on (Join-Path $parts 'tree.7z') chromix -bsp0 | Select-Object -Last 8 }
  } finally { Pop-Location }
  $manifestArgs = @('-B','-X','utf8',(Join-Path $repo 'tools\security_checkpoint.py'),'create',
    '--repo',$repo,'--work',$work,'--parts',$parts,'--stage',"$Stage",'--output',(Join-Path $parts 'checkpoint.json'),
    '--tree-bytes',"$([long]$treeBytes)")
  $manifestArgs += '--resume-safe'
  Checked { & $python @manifestArgs }
  $count = @(Get-ChildItem -LiteralPath $parts -Filter 'tree.7z.*' -File).Count
  Output parts "$count"
  Output checkpoint 'true'
  Output resume_safe 'true'
  return
}

if (-not $env:SECURITY_JOB_STARTED) { throw 'The worker must record the total job budget before checkout' }
$started = [DateTimeOffset]::Parse($env:SECURITY_JOB_STARTED)
$deadline = $started.AddMinutes(300) # 55 minutes remain for checkpoint compression/upload.
if ($Phase -in @('Build','Acquire')) {
  if (Test-Path -LiteralPath $statePath) { throw 'Acquisition cannot overwrite existing phase state' }
  $state = [ordered]@{ schema_version=2; stage=$Stage; run_id=$env:GITHUB_RUN_ID; source_commit=$env:GITHUB_SHA;
    operation=$Operation; source_run=$SourceRun; source_stage=$SourceStage; status='running'; phase='Acquire';
    resume_safe=$false; finished=$false; completed=@(); timings=@{}; command_number=0 }
  Save-State
} else {
  $state = Read-State
  if ($state.operation -cne $Operation -or $state.source_run -cne $SourceRun -or $state.source_stage -ne $SourceStage) {
    throw 'Phase operation or donor changed within the runner'
  }
}
Output finished 'false'
Output proceed 'false'
function Get-OwnedCommandStatus([int]$ExitCode,[System.Collections.IDictionary]$Receipt) {
  if (-not $Receipt -or -not $Receipt.Contains('cleanup_errors') -or
      $Receipt.cleanup_errors -isnot [array] -or $Receipt.timed_out -isnot [bool]) { return 'unsafe' }
  if (@($Receipt.cleanup_errors).Count -gt 0) { return 'unsafe' }
  if ($ExitCode -eq 124 -and $Receipt.timed_out -eq $true -and $null -eq $Receipt.exit_code) { return 'budget-stop' }
  if ($Receipt.timed_out -eq $true) { return 'unsafe' }
  if ($ExitCode -eq 0 -and $Receipt.exit_code -eq 0) { return 'success' }
  return 'failed'
}
function Invoke-Owned([string]$Label,[string[]]$Command,[int]$LimitSeconds=18000,[string]$WorkingDirectory=$repo,[switch]$GitHubReadToken) {
  $remaining = [int]($deadline - [DateTimeOffset]::UtcNow).TotalSeconds
  if ($remaining -lt 30) { throw 'SECURITY_BUDGET_STOP' }
  $limit = [Math]::Min($LimitSeconds, $remaining)
  $state.command_number++
  Save-State
  $stem = '{0:D3}-{1}' -f $state.command_number,$Label
  $log = Join-Path $evidence "$stem.log"
  $report = Join-Path $evidence "$stem.json"
  $tokenOption = @()
  if ($GitHubReadToken) { $tokenOption += '--github-read-token' }
  & $python -B -X utf8 (Join-Path $repo 'tools\security_run.py') --cwd $WorkingDirectory --timeout $limit --log $log --report $report @tokenOption -- @Command
  $rc = $LASTEXITCODE
  $receipt = $null
  try { $receipt = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json -AsHashtable } catch { }
  $commandStatus = Get-OwnedCommandStatus $rc $receipt
  if ($commandStatus -eq 'unsafe') {
    $state.resume_safe = $false
    Save-State
    throw "Owned command cleanup or receipt is unverified: $Label"
  }
  if ($commandStatus -eq 'budget-stop') { throw 'SECURITY_BUDGET_STOP' }
  if ($commandStatus -ne 'success') {
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 35 }
    throw "Security stage command failed: $Label ($rc)"
  }
}

function Set-CompilerEnvironment([switch]$WithoutSourceTools) {
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
  if (-not $WithoutSourceTools) {
    $env:PATH = "$(Join-Path $src 'third_party\llvm-build\Release+Asserts\bin');$(Join-Path $src 'third_party\ninja');$(Join-Path $src 'third_party\node\win');$env:PATH"
    & "$PSScriptRoot\configure-node.ps1" -NodePath (Join-Path $src 'third_party\node\win\node.exe')
    if ($state.ninja) { $env:NINJA = $state.ninja }
  }
}
function Invoke-SecurityPhase([string]$CurrentPhase) {
  Assert-PhasePredecessor $state $CurrentPhase
  $phaseStarted = [DateTimeOffset]::UtcNow
  $state.phase = $CurrentPhase
  Save-State
  if ([int]($deadline - $phaseStarted).TotalSeconds -lt 30) { throw 'SECURITY_BUDGET_STOP' }
  switch ($CurrentPhase) {
    'Acquire' {
      $pins = & "$PSScriptRoot\read-platform-pins.ps1" -Repo $repo
      if ($pins.ChromiumVersion -cne $version -or
          $pins.UngoogledCommit -cne '28a211ab4ab6b83d919966a4f58a6df23cf4fb29' -or
          $pins.UngoogledWindowsCommit -cne '8492884eb30fdd9417ea7816aa92def805c97c74') { throw 'Security lane target pins differ' }
      if (Test-Path -LiteralPath $work) { throw 'Acquisition requires a fresh runner work path' }
      if ($Operation -in @('resume','upgrade')) {
        if (-not $SourceRun) { throw 'Checkpoint acquisition requires a numeric donor run' }
        $checkpointArgs = @('--repo',$repo,'--run',$SourceRun,'--stage',"$Stage",'--source-stage',"$SourceStage")
        if ($Operation -eq 'upgrade') { $checkpointArgs += '--allow-migration' }
        Invoke-Owned 'checkpoint-preflight' (@($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_checkpoint.py'),'preflight') + $checkpointArgs + @('--output',$preflight)) -GitHubReadToken
        Clear-HostedToolSpace
        $admitted = Get-Content -LiteralPath $preflight -Raw | ConvertFrom-Json -AsHashtable
        $diskBudget = Get-CheckpointDiskBudget $admitted.manifest
        $diskBudget.available_bytes = (Get-PSDrive C).Free
        Json (Join-Path $evidence 'download-disk-budget.json') $diskBudget
        if ($diskBudget.available_bytes -lt $diskBudget.required_free_bytes) { throw 'Insufficient free disk space for checkpoint transfer, known source size and build reserve' }
        Invoke-Owned 'checkpoint-download' (@($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_checkpoint.py'),'restore') + $checkpointArgs + @('--destination',$restore,'--preflight-file',$preflight)) -GitHubReadToken
      } else {
        if ($SourceRun -or $SourceStage -ne 0) { throw 'Fresh/upstream acquisition cannot adopt a checkpoint donor' }
        Clear-HostedToolSpace
        if ($Operation -eq 'upstream') {
          Invoke-Owned 'exact-upstream-download' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\fetch_upstream_cache.py'),
            '--platform','windows','--arch','x64','--destination',$upstreamCache,'--run-id','41098495420') -GitHubReadToken
          $download = Get-Content -LiteralPath (Join-Path $upstreamCache 'result.json') -Raw | ConvertFrom-Json
          if ($download.status -cne 'hit') { throw 'Exact 156 upstream snapshot unavailable; explicit fresh operation is required, no fallback performed.' }
        }
      }
    }
    'Extract' {
      if ($Operation -in @('resume','upgrade')) {
        $layoutPath = Join-Path $evidence 'checkpoint-archive-layout.json'
        Invoke-Owned 'checkpoint-archive-list' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_checkpoint.py'),'verify-archive','--archive',(Join-Path $restore 'tree.7z.001'),'--sevenzip',$seven,'--output',$layoutPath)
        $layout = Get-Content -LiteralPath $layoutPath -Raw | ConvertFrom-Json
        if ([long]$layout.uncompressed_bytes -le 0) { throw 'Checkpoint archive does not declare a positive source size' }
        [long]$requiredExtractionSpace = [long]$layout.uncompressed_bytes + 24GB
        $availableExtractionSpace = (Get-PSDrive C).Free
        Json (Join-Path $evidence 'extract-disk-budget.json') @{ required_free_bytes=$requiredExtractionSpace; available_bytes=$availableExtractionSpace; uncompressed_bytes=$layout.uncompressed_bytes }
        if ($availableExtractionSpace -lt $requiredExtractionSpace) { throw 'Insufficient free disk space to extract the source and retain build reserve' }
        $null = New-Item -ItemType Directory -Path $root -Force
        Invoke-Owned 'checkpoint-extract' @($seven,'x',(Join-Path $restore 'tree.7z.001'),"-o$root",'-y','-bsp0')
        Invoke-Owned 'checkpoint-tree' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_checkpoint.py'),'verify-tree','--work',$work,'--manifest',(Join-Path $restore 'checkpoint.json'))
        if ($Operation -eq 'upgrade') {
          Invoke-Owned 'migration-source' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_migration.py'),'verify-source','--repo',$repo,'--work',$work,'--manifest',(Join-Path $restore 'checkpoint.json'))
        } else {
          $origin = Join-Path $work '.security-origin'
          $null = New-Item -ItemType Directory -Path $origin -Force
          Copy-Item -LiteralPath (Join-Path $restore 'checkpoint.json') -Destination (Join-Path $origin "checkpoint-$SourceRun-s$SourceStage.json")
        }
        Assert-Plain $restore
        if ((Resolve-Path -LiteralPath $restore).Path -cne 'C:\security-restore') { throw 'Unexpected restore cleanup path' }
        Remove-Item -LiteralPath $restore -Recurse -Force
      } else {
        $null = New-Item -ItemType Directory -Path $work -Force
        if ($Operation -eq 'upstream') {
          Invoke-Owned 'exact-upstream-restore' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\restore_upstream_cache.py'),
            '--phase','restore','--platform','windows','--arch','x64','--workdir',$work,'--cache-dir',$upstreamCache)
          if (-not (Test-Path -LiteralPath (Join-Path $src '.chromix-upstream-restored.json'))) { throw 'Full upstream restoration was not verified; refusing compilation' }
        }
      }
    }
    'Prepare' {
      # The environment inspection must see the same SDK/VS inputs as GN and
      # Ninja. Install once, after the donor transfer has already been admitted.
      Invoke-Owned 'sdk' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\ensure-windows-sdk.ps1",'-Arch','x64','-ChromiumVersion',$version,'-Install')
      Set-CompilerEnvironment -WithoutSourceTools
      $state.sdk_ready = $true
      Save-State
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
      if ($Operation -in @('resume','upgrade') -and (Test-Path -LiteralPath (Join-Path $src '.chromix-upstream-restored.json'))) {
        Invoke-Owned 'restored-environment-inspect' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\prepare_restored_build.py'),
          '--phase','inspect','--platform','windows','--arch','x64','--workdir',$work)
      }
      $patch = Join-Path $src 'third_party\git\usr\bin\patch.exe'
      # Never repeat base preparation/domain replacement after service restoration.
      $prepareArgs = @($python,'-B','-X','utf8',(Join-Path $repo 'tools\prepare_google_services.py'),
        '--repo',$repo,'--work',$work,'--patch-bin',$patch)
      if ($Operation -eq 'upgrade') { $prepareArgs += '--reuse-prepared-domains' }
      Invoke-Owned 'service-and-fingerprint-restore' $prepareArgs
      if ($Operation -eq 'upgrade') {
        Invoke-Owned 'migration-target' @($python,'-B','-X','utf8',(Join-Path $repo 'tools\security_migration.py'),'verify-target','--repo',$repo,'--work',$work)
      }
    }
    'Configure' {
      if ($state.sdk_ready -ne $true) { throw 'Native SDK preparation was not completed' }
      Set-CompilerEnvironment
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
      Save-State
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
      $state.ninja = $ninja
      $state.gn = $gn
    }
    'Compile' {
      Set-CompilerEnvironment
      $ninja = $state.ninja
      if (-not $ninja -or -not (Test-Path -LiteralPath $ninja -PathType Leaf)) { throw 'Configured Ninja is missing' }
      $resources = & $python -B -X utf8 (Join-Path $repo 'tools\build_resources.py') | ConvertFrom-Json
      if ($LASTEXITCODE -ne 0) { throw 'Resource sizing failed' }
      Json (Join-Path $evidence 'resources.json') $resources
      $env:NINJA_STATUS = '[%f/%t] active=%r rate=%c/s elapsed=%es '
      $state.status = 'running'; Save-State
      Invoke-Owned 'ninja' @($ninja,'-C',$out,'-j',"$($resources.ninja_jobs)",'-k','20','chrome','native_passkey_unittests','uxr_surface_modes_tests')
    }
    'Bundle' {
      Set-CompilerEnvironment
      Invoke-Owned 'package' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\package-win.ps1",'-Out',$out,'-Dest','C:\c\dist','-Arch','x64','-SkipArchive')
      $bundle = 'C:\c\dist\chromix'
      $state.build_hash = (Get-FileHash -LiteralPath (Join-Path $bundle 'google-services-build.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    'Verify' {
      Set-CompilerEnvironment
      $bundle = 'C:\c\dist\chromix'
      Invoke-Owned 'native-passkey' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\run-native-passkey-tests.ps1",'-Executable',(Join-Path $out 'native_passkey_unittests.exe'),
        '-Summary',(Join-Path $evidence 'native-passkey-unittests.json'),'-Log',(Join-Path $evidence 'native-passkey.log'),
        '-Receipt',(Join-Path $evidence 'native-passkey-launcher.json'),'-WorkingDirectory',$src)
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
    }
    'Archive' {
      Set-CompilerEnvironment
      Invoke-Owned 'archive' @($pwsh,'-NoProfile','-File',"$PSScriptRoot\package-win.ps1",'-Out',$out,'-Dest','C:\c\dist','-Arch','x64','-ArchiveOnly','-ExpectedBuildRecordSHA256',$state.build_hash)
      # Keep the artifact verifier's outer receipts beside the archive. These copies
      # are outside the immutable browser bundle and therefore do not rebaseline it.
      foreach ($name in @('build-inputs.json','native-passkey-unittests.json','native-passkey-launcher.json',
          'candidate-smoke.json','google-services-runtime.json','security-runtime.json','fingerprint-modes-validation.json',
          'modes-native.json','modes-runtime.json','fingerprint-modes-capabilities.json')) {
        Copy-Item -LiteralPath (Join-Path $evidence $name) -Destination (Join-Path 'C:\c\dist' $name)
      }
      Copy-Item -LiteralPath (Join-Path $work 'google-services-source.json') -Destination 'C:\c\dist\google-services-source.json'
      $state.finished = $true; $state.status = 'candidate-created'
    }
  }
  $state.completed = @($state.completed) + $CurrentPhase
  $state.timings[$CurrentPhase] = @{ started_at=$phaseStarted.ToString('o'); finished_at=[DateTimeOffset]::UtcNow.ToString('o'); seconds=([DateTimeOffset]::UtcNow-$phaseStarted).TotalSeconds }
  Save-State
  Json (Join-Path $evidence ("phase-" + $CurrentPhase.ToLowerInvariant() + '.json')) @{ phase=$CurrentPhase; status='completed'; timing=$state.timings[$CurrentPhase]; resume_safe=$state.resume_safe }
}
try {
  $phases = if ($Phase -eq 'Build') { @('Acquire','Extract','Prepare','Configure','Compile','Bundle','Verify','Archive') } else { @($Phase) }
  foreach ($item in $phases) { Invoke-SecurityPhase $item }
  Output proceed 'true'
  Output finished ($state.finished.ToString().ToLowerInvariant())
  Output resume_safe ($state.resume_safe.ToString().ToLowerInvariant())
} catch {
  $state.status = if ($_.Exception.Message -eq 'SECURITY_BUDGET_STOP') { 'budget-stop' } else { 'failed' }
  $state.error = $_.Exception.Message
  Save-State
  Json (Join-Path $evidence ("phase-" + $state.phase.ToLowerInvariant() + '.json')) @{ phase=$state.phase; status=$state.status; error=$state.error; resume_safe=$state.resume_safe }
  Output proceed 'false'
  Output resume_safe ($state.resume_safe.ToString().ToLowerInvariant())
  if ($state.status -ne 'budget-stop') { throw }
  Write-Host 'Stage budget reached; later phases are skipped and the prepared state is preserved.'
  # The Actions pwsh wrapper observes the native exit code even after this
  # timeout has been handled. Only the deliberate budget stop is successful.
  $global:LASTEXITCODE = 0
}
