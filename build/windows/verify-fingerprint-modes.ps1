#Requires -Version 7.0
param(
  [Parameter(Mandatory)][string]$Out,
  [Parameter(Mandatory)][string]$Bundle,
  [Parameter(Mandatory)][string]$Report
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$outPath = (Resolve-Path -LiteralPath $Out).Path
$bundlePath = (Resolve-Path -LiteralPath $Bundle).Path
$reportPath = [IO.Path]::GetFullPath($Report)
$destination = [IO.Path]::GetDirectoryName($reportPath)
$null = New-Item -ItemType Directory -Force -Path $destination
$nativePath = Join-Path $destination 'modes-native.json'
$runtimePath = Join-Path $destination 'modes-runtime.json'
$capabilitiesPath = Join-Path $destination 'fingerprint-modes-capabilities.json'
$utf8 = [Text.UTF8Encoding]::new($false)
function Write-Json([string]$Path, $Value) {
  [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 50) + "`n", $utf8)
}
function File-Hash([string]$Path) {
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

# Use an owned hidden process, exact argument boundaries and a bounded lifetime.
# Only this process tree is terminated on timeout. No process-name cleanup.
function Invoke-OwnedCheck([string]$Executable, [string[]]$Arguments,
                           [string]$WorkingDirectory, [string]$Label,
                           [int]$TimeoutSeconds) {
  $start = [Diagnostics.ProcessStartInfo]::new()
  $start.FileName = $Executable
  $start.WorkingDirectory = $WorkingDirectory
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $start.StandardOutputEncoding = $utf8
  $start.StandardErrorEncoding = $utf8
  foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
  foreach ($key in @($start.Environment.Keys)) {
    if ($key -match '^(GH_|GITHUB_|GOOGLE_|CLOAK|OPENAI_|ANTHROPIC_|CODEX_|AWS_|AZURE_)' -or
        $key -match '(TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL)') {
      $null = $start.Environment.Remove($key)
    }
  }
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $start
  $watch = [Diagnostics.Stopwatch]::StartNew()
  $timedOut = $false
  try {
    if (-not $process.Start()) { throw "Cannot start owned check $Label" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    while (-not $process.WaitForExit(1000)) {
      if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
        $timedOut = $true
        $process.Kill($true)
        $process.WaitForExit()
        break
      }
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($stdout.Length -gt 8MB -or $stderr.Length -gt 8MB) { throw 'Owned check produced oversized evidence' }
    $stdoutPath = Join-Path $evidence "$Label.stdout.log"
    $stderrPath = Join-Path $evidence "$Label.stderr.log"
    [IO.File]::WriteAllText($stdoutPath, $stdout, $utf8)
    [IO.File]::WriteAllText($stderrPath, $stderr, $utf8)
    return [ordered]@{
      label = $Label; executable_sha256 = File-Hash $Executable
      arguments = $Arguments; exit_code = $process.ExitCode; timed_out = $timedOut
      seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 3)
      stdout_file = $stdoutPath; stdout_sha256 = File-Hash $stdoutPath
      stderr_file = $stderrPath; stderr_sha256 = File-Hash $stderrPath
      # Inline the small native output so the uploaded receipt is self-contained.
      stdout = $stdout; stderr = $stderr
      credential_environment_removed = $true
    }
  } finally { $process.Dispose() }
}

function Read-NativeGroups($Execution, [string[]]$RequiredFields,
                           [hashtable]$ExpectedCounts = @{}) {
  if ($Execution.exit_code -ne 0 -or $Execution.timed_out) {
    throw "Native check failed: $($Execution.label)"
  }
  $records = @()
  foreach ($line in ($Execution.stdout -split "`r?`n")) {
    if (-not $line.TrimStart().StartsWith('{')) { continue }
    try { $value = $line | ConvertFrom-Json } catch { throw 'Malformed native JSON result' }
    if (-not $value.PSObject.Properties['failures'] -or
        ($value.failures -isnot [int] -and $value.failures -isnot [long]) -or
        $value.failures -ne 0) {
      throw 'Native JSON contains a failure or lacks an explicit failure count'
    }
    if ($value.PSObject.Properties['status'] -and $value.status -cne 'passed') {
      throw 'Native JSON status did not pass'
    }
    $records += $value
  }
  foreach ($name in $RequiredFields) {
    $matching = @($records | Where-Object { $_.PSObject.Properties[$name] })
    if ($matching.Count -ne 1) { throw "Native coverage group is missing or ambiguous: $name" }
    $rawCount = $matching[0].$name
    if ($rawCount -isnot [int] -and $rawCount -isnot [long]) {
      throw "Native coverage count must be an integer: $name"
    }
    $count = [double]$rawCount
    if (-not [double]::IsFinite($count) -or $count -le 0 -or $count % 1 -ne 0) {
      throw "Native coverage group did not execute: $name"
    }
    if ($ExpectedCounts.ContainsKey($name) -and $rawCount -ne $ExpectedCounts[$name]) {
      throw "Native coverage group does not match the pinned suite: $name"
    }
  }
  if (-not @($records | Where-Object { $_.status -ceq 'passed' -and $_.configuration_checks -gt 0 }).Count) {
    throw 'Native configuration acceptance is missing'
  }
  return $records
}

function Assert-OriginalRuntimeScope($Result) {
  $names = @('canvas_pixels', 'webgl_pixels', 'canvas_references', 'webgl_references',
             'text', 'fonts', 'audio', 'voices', 'webgpu_identity', 'webgpu_features', 'webgpu_limits', 'memory', 'webgl_identity', 'webgl_capabilities', 'webgl_precision', 'webgl_extensions', 'webgl_translated_shaders', 'glyph_metrics')
  $original = $Result.original_runtime_acceptance
  if ($Result.full_strategy_acceptance_scope -cne 'compatible-v1-covered-groups' -or
      $Result.full_original_cloak_acceptance -isnot [bool] -or $Result.full_original_cloak_acceptance -or
      -not $original -or $original.status -cne 'not_tested' -or
      $original.source_goldens_are_runtime_acceptance -isnot [bool] -or $original.source_goldens_are_runtime_acceptance -or
      @($original.PSObject.Properties).Count -ne 3 -or
      @($original.PSObject.Properties | Where-Object { $_.Name -cnotin @('status', 'source_goldens_are_runtime_acceptance', 'surfaces') }).Count -or
      -not $original.surfaces -or @($original.surfaces.PSObject.Properties).Count -ne $names.Count -or
      @($original.surfaces.PSObject.Properties | Where-Object { $_.Name -cnotin $names }).Count) {
    throw 'Original browser runtime acceptance must remain explicitly not_tested in this runner'
  }
  foreach ($name in $names) {
    $surface = $original.surfaces.$name
    if (-not $surface -or @($surface.PSObject.Properties).Count -ne 3 -or
        @($surface.PSObject.Properties | Where-Object { $_.Name -cnotin @('status', 'evidence', 'reason') }).Count -or
        $surface.status -cne 'not_tested' -or $surface.evidence -isnot [array] -or $surface.evidence.Count -ne 0 -or
        $surface.reason -isnot [string] -or -not $surface.reason) {
      throw "Native/source goldens cannot supply original browser acceptance: $name"
    }
  }
}

foreach ($path in @($nativePath, $runtimePath, $reportPath)) {
  if (Test-Path -LiteralPath $path) { throw 'Fingerprint evidence must be fresh for this execution' }
}
$main = [ordered]@{
  schema_version = 1; status = 'failed'; started_at = [DateTimeOffset]::UtcNow.ToString('o')
  browser_sha256 = $null; chrome_dll_sha256 = $null; files = @{}
  full_strategy_acceptance_scope = 'compatible-v1-covered-groups'; full_original_cloak_acceptance = $false
  qualification = 'Native helper/configuration tests and bounded compatible-v1 browser groups; original-mode Web API runtime acceptance is not executed here'
}
$native = [ordered]@{ schema_version = 1; status = 'failed'; processes = @(); groups = @{} }
$hostProcess = [Diagnostics.Process]::GetCurrentProcess()
$originalPriority = $hostProcess.PriorityClass
try {
  $evidence = Join-Path $destination ("modes-native-evidence-" + [Guid]::NewGuid().ToString('N'))
  $null = New-Item -ItemType Directory -Path $evidence
  $executable = Join-Path $outPath 'uxr_surface_modes_tests.exe'
  $browser = Join-Path $bundlePath 'chrome.exe'
  $dll = Join-Path $bundlePath 'chrome.dll'
  $node = $env:FINGERPRINT_TEST_NODE
  if (-not $node -or -not (Test-Path -LiteralPath $node -PathType Leaf)) {
    throw 'FINGERPRINT_TEST_NODE must name the pinned Node 24 interpreter'
  }
  foreach ($path in @($executable, $browser, $dll)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'A compiled mode test or candidate payload is missing' }
  }
  $main.browser_sha256 = File-Hash $browser
  $main.chrome_dll_sha256 = File-Hash $dll
  foreach ($name in @('chrome.exe', 'chrome.dll')) {
    if ((File-Hash (Join-Path $outPath $name)) -cne (File-Hash (Join-Path $bundlePath $name))) {
      throw 'Native test output and packaged browser payload do not match'
    }
  }
  $native['browser_sha256'] = $main.browser_sha256
  $native['chrome_dll_sha256'] = $main.chrome_dll_sha256
  $sourceCapabilities = Join-Path $repo 'build\fingerprint-modes-capabilities.json'
  $null = Get-Content -LiteralPath $sourceCapabilities -Raw | ConvertFrom-Json
  Copy-Item -LiteralPath $sourceCapabilities -Destination $capabilitiesPath -Force
  $capabilitiesHash = File-Hash $sourceCapabilities
  $packagedCapabilities = Join-Path $bundlePath 'fingerprint-modes-capabilities.json'
  if (-not (Test-Path -LiteralPath $packagedCapabilities -PathType Leaf) -or
      (File-Hash $packagedCapabilities) -cne $capabilitiesHash) {
    throw 'Packaged capabilities do not match the checked-out source contract'
  }
  $fixtureRoot = Join-Path $repo 'tools\tests\fixtures'
  if (-not (Test-Path -LiteralPath $fixtureRoot -PathType Container)) { throw 'Independent golden fixtures are missing' }
  $hostProcess.PriorityClass = [Diagnostics.ProcessPriorityClass]::Normal
  $hostProcess.Refresh()
  if ($hostProcess.PriorityClass -ne [Diagnostics.ProcessPriorityClass]::Normal) {
    throw 'Cannot establish normal test-host priority'
  }
  $legacy = Invoke-OwnedCheck $executable @('--config-legacy') $outPath 'config-legacy' 120
  $native.processes += $legacy
  $native.groups['legacy'] = @(Read-NativeGroups $legacy @('configuration_checks'))
  $chromix = Invoke-OwnedCheck $executable @('--config-chromix') $outPath 'config-chromix' 120
  $native.processes += $chromix
  $native.groups['chromix'] = @(Read-NativeGroups $chromix @('configuration_checks'))
  $original = Invoke-OwnedCheck $executable @('--config-original', $fixtureRoot) $outPath 'config-original' 180
  $native.processes += $original
  $native.groups['original'] = @(Read-NativeGroups $original @(
      'original_pixel_checks', 'original_reference_checks', 'original_audio_checks',
      'original_webgl_public_checks', 'original_webgl_translated_checks',
      'original_glyph_metrics_checks', 'original_glyph_metrics_configuration_checks',
      'configuration_checks') @{
        original_pixel_checks = 78
        original_reference_checks = 2660
        original_audio_checks = 337
        original_webgl_public_checks = 4327
        original_webgl_translated_checks = 32
        original_glyph_metrics_checks = 94
        original_glyph_metrics_configuration_checks = 61
      })
  $helpers = Invoke-OwnedCheck $executable @($fixtureRoot) $outPath 'production-helpers-and-config' 180
  $native.processes += $helpers
  $native.groups['production'] = @(Read-NativeGroups $helpers @(
      'graphics_cases', 'checks', 'gpu_checks', 'hardware_checks',
      'font_allowlist_checks', 'audio_default_checks', 'configuration_checks'))
  $native.status = 'passed'
  Write-Json $nativePath $native
  $runtime = Invoke-OwnedCheck $node @(
      (Join-Path $repo 'tools\verify_fingerprint_modes_runtime.mjs'),
      '--browser', $browser, '--report', $runtimePath, '--capabilities', $capabilitiesPath
    ) $repo 'raw-cdp-runtime' 1800
  $main['runtime_process'] = $runtime
  if ($runtime.exit_code -ne 0 -or $runtime.timed_out -or
      -not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
    throw 'Fingerprint runtime acceptance failed; see runtime evidence'
  }
  $result = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
  Assert-OriginalRuntimeScope $result
  if ($result.status -cne 'passed' -or $result.baseline_only -or
      $result.browser_sha256 -cne $main.browser_sha256 -or
      $result.chrome_dll_sha256 -cne $main.chrome_dll_sha256 -or
      -not @($result.checks).Count -or @($result.failed).Count -ne 0 -or
      @($result.checks | Where-Object { $_.passed -ne $true }).Count -or
      $result.full_strategy_acceptance -isnot [bool] -or $result.partial -isnot [bool] -or
      $result.partial -eq $result.full_strategy_acceptance -or
      -not $result.positive_acceptance -or
      @($result.positive_acceptance.PSObject.Properties).Count -ne 25 -or
      @($result.positive_acceptance.PSObject.Properties | Where-Object {
          $_.Value.status -notin @('observed_pass', 'not_tested')
        }).Count) {
    throw 'Runtime report is incomplete, failed, or bound to another browser'
  }
  if ((File-Hash $sourceCapabilities) -cne $capabilitiesHash -or
      (File-Hash $capabilitiesPath) -cne $capabilitiesHash -or
      (File-Hash $packagedCapabilities) -cne $capabilitiesHash) {
    throw 'Capabilities manifest changed during validation'
  }
  if ((File-Hash $browser) -cne $main.browser_sha256 -or
      (File-Hash $dll) -cne $main.chrome_dll_sha256) {
    throw 'Candidate bytes changed during fingerprint validation'
  }
  foreach ($name in @('modes-native.json', 'modes-runtime.json', 'fingerprint-modes-capabilities.json')) {
    $main.files[$name] = File-Hash (Join-Path $destination $name)
  }
  $main['required_runtime_checks'] = @($result.checks).Count
  $main['not_tested'] = $result.not_tested
  $main['coverage_limits'] = $result.coverage_limits
  $main['positive_acceptance'] = $result.positive_acceptance
  $main['full_strategy_acceptance'] = $result.full_strategy_acceptance
  $main['full_strategy_acceptance_scope'] = $result.full_strategy_acceptance_scope
  $main['full_original_cloak_acceptance'] = $result.full_original_cloak_acceptance
  $main['original_runtime_acceptance'] = $result.original_runtime_acceptance
  $main['partial'] = $result.partial
  $main.status = 'passed'
} catch {
  $main['error'] = $_.Exception.Message
  $native['completed_at'] = [DateTimeOffset]::UtcNow.ToString('o')
  if ($native.status -ne 'passed') { $native['error'] = $_.Exception.Message }
  if (-not (Test-Path -LiteralPath $nativePath)) { Write-Json $nativePath $native }
  throw
} finally {
  try { $hostProcess.PriorityClass = $originalPriority } catch {
    $main.status = 'failed'; $main['priority_restore_error'] = $_.Exception.Message
  }
  $hostProcess.Dispose()
  $main['completed_at'] = [DateTimeOffset]::UtcNow.ToString('o')
  Write-Json $reportPath $main
}
