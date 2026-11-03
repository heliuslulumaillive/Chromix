param(
  [Parameter(Mandatory)][string]$Executable,
  [Parameter(Mandatory)][string]$Summary,
  [Parameter(Mandatory)][string]$Log,
  [Parameter(Mandatory)][string]$Receipt,
  [Parameter(Mandatory)][string]$WorkingDirectory,
  [string]$BuildRecord,
  [ValidateSet('WinAuthenticatorTest.*:AuthenticatorRequestDialogControllerTest.*','ShardXNativeBrowserTest.*')]
  [string]$TestFilter='WinAuthenticatorTest.*:AuthenticatorRequestDialogControllerTest.*'
)
$ErrorActionPreference = 'Stop'
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
$workingPath = (Resolve-Path -LiteralPath $WorkingDirectory).Path
$summaryPath = [IO.Path]::GetFullPath($Summary)
$logPath = [IO.Path]::GetFullPath($Log)
$receiptPath = [IO.Path]::GetFullPath($Receipt)
$outputDirectory = [IO.Path]::GetDirectoryName($summaryPath)
if ([IO.Path]::GetDirectoryName($logPath) -ne $outputDirectory -or
    [IO.Path]::GetDirectoryName($receiptPath) -ne $outputDirectory) {
  throw 'Native test evidence must share one output directory'
}
$null = New-Item -ItemType Directory -Path $outputDirectory -Force
# Require this execution's output, never a success JSON inherited from a checkpoint.
$currentSummary = "$summaryPath.current-$([Guid]::NewGuid().ToString('N')).json"
$hostProcess = [Diagnostics.Process]::GetCurrentProcess()
$originalPriority = $hostProcess.PriorityClass
$record = [ordered]@{
  schema_version=1;status='failed';started_at_utc=[DateTime]::UtcNow.ToString('o');
  executable=$executablePath;
  executable_sha256=(Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant();
  executable_bytes=(Get-Item -LiteralPath $executablePath).Length;
  host_process_id=$hostProcess.Id;original_host_priority=$originalPriority.ToString();
  test_host_priority=$null;restored_host_priority=$null;native_exit_code=$null;
  startup_assertions_unchanged=$true;
  test_filter=$TestFilter
}
$locationPushed = $false
try {
  if ($BuildRecord) {
    $buildPath = (Resolve-Path -LiteralPath $BuildRecord).Path
    $buildFile = Get-Item -LiteralPath $buildPath
    if ($buildFile.Length -gt 4MB -or $buildFile.PSIsContainer -or
        ($buildFile.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw 'Invalid immutable native-test build record'
    }
    $buildBytes = [IO.File]::ReadAllBytes($buildPath)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $buildHash = [BitConverter]::ToString($hasher.ComputeHash($buildBytes)).Replace('-','').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    $buildText = [Text.Encoding]::UTF8.GetString($buildBytes).TrimStart([char]0xFEFF)
    $build = $buildText | ConvertFrom-Json
    # ConvertFrom-Json may turn ISO strings into DateTime and lose their UTC
    # spelling. Read the original JSON string before enforcing explicit UTC.
    $document = [Text.Json.JsonDocument]::Parse($buildText)
    try { $createdText = $document.RootElement.GetProperty('created_at_utc').GetString() }
    finally { $document.Dispose() }
    $native = $build.native_test_executable
    if (($build.schema_version -isnot [int] -and $build.schema_version -isnot [long]) -or
        $build.schema_version -ne 1 -or $build.build_variant -ne 'fingerprint-modes-win156' -or
        $build.chromium_version -ne '156.0.8078.79' -or
        $build.source_commit -cnotmatch '^[a-f0-9]{40}$' -or
        ($build.run_id -isnot [int] -and $build.run_id -isnot [long]) -or $build.run_id -le 0 -or
        $build.preparation_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        $build.gn_args_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
        $native.name -cne 'native_passkey_unittests.exe' -or
        [IO.Path]::GetFileName($executablePath) -cne $native.name -or
        $native.sha256 -cne $record.executable_sha256 -or
        ($native.bytes -isnot [int] -and $native.bytes -isnot [long]) -or
        $native.bytes -le 0 -or $native.bytes -ne $record.executable_bytes) {
      throw 'Native test executable/source does not match its immutable pretest build record'
    }
    $created = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse($createdText, [Globalization.CultureInfo]::InvariantCulture,
          [Globalization.DateTimeStyles]::None, [ref]$created) -or $created.Offset -ne [TimeSpan]::Zero -or
        $createdText -notmatch '(Z|\+00:00)$' -or
        $created -gt [DateTimeOffset]::Parse($record.started_at_utc, [Globalization.CultureInfo]::InvariantCulture)) {
      throw 'Native tests cannot precede the immutable pretest build record'
    }
    $record['build_record_sha256'] = $buildHash
    foreach ($key in @('source_commit','run_id','preparation_sha256','gn_args_sha256')) {
      $record[$key] = $build.$key
    }
  }
  # Chromium's TestSuite checks process priority before running any test. A
  # BelowNormal/Idle CI shell otherwise gives every test child that same class.
  # Normalize before child creation; changing an already started child races.
  $hostProcess.PriorityClass = [Diagnostics.ProcessPriorityClass]::Normal
  $hostProcess.Refresh()
  if ($hostProcess.PriorityClass -ne [Diagnostics.ProcessPriorityClass]::Normal) {
    throw 'Cannot establish Normal priority before native test child creation'
  }
  $record.test_host_priority = $hostProcess.PriorityClass.ToString()
  "Native test launcher priority: $originalPriority -> $($record.test_host_priority)" |
    Set-Content -LiteralPath $logPath -Encoding utf8
  Push-Location -LiteralPath $workingPath
  $locationPushed = $true
  & $executablePath "--gtest_filter=$($record.test_filter)" '--test-launcher-jobs=1' `
    "--test-launcher-summary-output=$currentSummary" 2>&1 |
    Tee-Object -FilePath $logPath -Append
  $record.native_exit_code = $LASTEXITCODE
  if ($record.native_exit_code -ne 0) { throw "Native passkey tests failed: $($record.native_exit_code)" }
  if (-not (Test-Path -LiteralPath $currentSummary -PathType Leaf)) {
    throw 'The current native execution did not produce a result file'
  }
  Move-Item -LiteralPath $currentSummary -Destination $summaryPath -Force
  $record['summary_sha256'] = (Get-FileHash -LiteralPath $summaryPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ((Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $record.executable_sha256 -or
      (Get-Item -LiteralPath $executablePath).Length -ne $record.executable_bytes -or
      ($BuildRecord -and (Get-FileHash -LiteralPath $buildPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $record.build_record_sha256)) {
    throw 'Native test executable or pretest build record changed during execution'
  }
  $record.status = 'passed'
} catch {
  $record['error'] = $_.Exception.Message
  throw
} finally {
  if ($locationPushed) { Pop-Location }
  try {
    $hostProcess.PriorityClass = $originalPriority
    $hostProcess.Refresh()
    $record.restored_host_priority = $hostProcess.PriorityClass.ToString()
    if ($hostProcess.PriorityClass -ne $originalPriority) { throw 'Original launcher priority was not restored' }
  } catch {
    $record.status = 'failed'
    $record['priority_restore_error'] = $_.Exception.Message
    throw
  } finally {
    $record['completed_at_utc'] = [DateTime]::UtcNow.ToString('o')
    $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
    $hostProcess.Dispose()
  }
}
