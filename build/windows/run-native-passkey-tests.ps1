param(
  [Parameter(Mandatory)][string]$Executable,
  [Parameter(Mandatory)][string]$Summary,
  [Parameter(Mandatory)][string]$Log,
  [Parameter(Mandatory)][string]$Receipt,
  [Parameter(Mandatory)][string]$WorkingDirectory
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
  host_process_id=$hostProcess.Id;original_host_priority=$originalPriority.ToString();
  test_host_priority=$null;restored_host_priority=$null;native_exit_code=$null;
  startup_assertions_unchanged=$true;
  test_filter='WinAuthenticatorTest.*:AuthenticatorRequestDialogControllerTest.*'
}
$locationPushed = $false
try {
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
