# Validate the actual receipt parser without launching a browser or native test.
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$source = Join-Path $repoRoot 'build\windows\verify-fingerprint-modes.ps1'
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Verification script has PowerShell syntax errors' }
$definition = $ast.Find({ param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Read-NativeGroups'
}, $true)
if (-not $definition) { throw 'Actual native receipt parser is missing' }
. ([ScriptBlock]::Create($definition.Extent.Text))
$expected = @{ original_pixel_checks = 78; original_reference_checks = 2660; original_audio_checks = 337; original_webgl_public_checks = 4327; original_webgl_translated_checks = 32; original_glyph_metrics_checks = 94; original_glyph_metrics_configuration_checks = 61 }
$required = @('original_pixel_checks', 'original_reference_checks', 'original_audio_checks', 'original_webgl_public_checks', 'original_webgl_translated_checks', 'original_glyph_metrics_checks', 'original_glyph_metrics_configuration_checks', 'configuration_checks')
function Baseline {
  return @(
    @{ original_pixel_checks = 78; failures = 0 },
    @{ original_reference_checks = 2660; failures = 0 },
    @{ original_audio_checks = 337; failures = 0 },
    @{ original_webgl_public_checks = 4327; failures = 0 },
    @{ original_webgl_translated_checks = 32; failures = 0 },
    @{ original_glyph_metrics_checks = 94; failures = 0 },
    @{ original_glyph_metrics_configuration_checks = 61; failures = 0 },
    @{ status = 'passed'; configuration_checks = 11; failures = 0 }
  )
}
function Parse-Fixture($Records) {
  $output = (($Records | ForEach-Object { $_ | ConvertTo-Json -Compress }) -join "`n") + "`n"
  $execution = @{ label = 'explicit-test-fixture'; exit_code = 0; timed_out = $false; stdout = $output }
  return @(Read-NativeGroups $execution $required $expected)
}
$count = 0
if ((Parse-Fixture (Baseline)).Count -ne 8) { throw 'Valid native fixture rejected' }
$count++
foreach ($field in @('original_pixel_checks', 'original_reference_checks', 'original_audio_checks', 'original_webgl_public_checks', 'original_webgl_translated_checks', 'original_glyph_metrics_checks', 'original_glyph_metrics_configuration_checks')) {
  foreach ($bad in @(0, 1, -1, $true, '2660', 2.5)) {
    $records = Baseline
    foreach ($row in $records) { if ($row.ContainsKey($field)) { $row[$field] = $bad } }
    $rejected = $false
    try { $null = Parse-Fixture $records } catch { $rejected = $true }
    if (-not $rejected) { throw "Invalid group accepted: $field / $bad" }
    $count++
  }
  $records = @(Baseline | Where-Object { -not $_.ContainsKey($field) })
  $rejected = $false
  try { $null = Parse-Fixture $records } catch { $rejected = $true }
  if (-not $rejected) { throw "Missing group accepted: $field" }
  $count++
  $records = Baseline
  $duplicate = @($records | Where-Object { $_.ContainsKey($field) })[0]
  $records += $duplicate
  $rejected = $false
  try { $null = Parse-Fixture $records } catch { $rejected = $true }
  if (-not $rejected) { throw "Duplicate group accepted: $field" }
  $count++
}
$scopeDefinition = $ast.Find({ param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Assert-OriginalRuntimeScope'
}, $true)
if (-not $scopeDefinition) { throw 'Actual original runtime scope validator is missing' }
. ([ScriptBlock]::Create($scopeDefinition.Extent.Text))
function Original-ScopeFixture {
  $surfaces = @{}
  foreach ($name in @('canvas_pixels', 'webgl_pixels', 'canvas_references', 'webgl_references',
                     'text', 'fonts', 'audio', 'voices', 'webgpu_identity', 'webgpu_features', 'webgpu_limits', 'memory', 'webgl_identity', 'webgl_capabilities', 'webgl_precision', 'webgl_extensions', 'webgl_translated_shaders', 'glyph_metrics')) {
    $surfaces[$name] = @{ status = 'not_tested'; evidence = @(); reason = 'No original browser path executed in fixture' }
  }
  return (@{
    full_strategy_acceptance = $true; full_strategy_acceptance_scope = 'compatible-v1-covered-groups'
    full_original_cloak_acceptance = $false
    original_runtime_acceptance = @{ status = 'not_tested'; source_goldens_are_runtime_acceptance = $false; surfaces = $surfaces }
  } | ConvertTo-Json -Depth 20 | ConvertFrom-Json)
}
Assert-OriginalRuntimeScope (Original-ScopeFixture)
$scopeChecks = 1
$mutations = @(
  { param($r) $r.full_strategy_acceptance_scope = 'all-strategies' },
  { param($r) $r.PSObject.Properties.Remove('full_original_cloak_acceptance') },
  { param($r) $r.full_original_cloak_acceptance = $true },
  { param($r) $r.full_original_cloak_acceptance = 0 },
  { param($r) $r.original_runtime_acceptance.status = 'passed' },
  { param($r) $r.original_runtime_acceptance.source_goldens_are_runtime_acceptance = $true },
  { param($r) $r.original_runtime_acceptance.surfaces.PSObject.Properties.Remove('fonts') },
  { param($r) $r.original_runtime_acceptance.surfaces | Add-Member -NotePropertyName extra -NotePropertyValue @{} },
  { param($r) $r.original_runtime_acceptance.surfaces.canvas_references.status = 'observed_pass' },
  { param($r) $r.original_runtime_acceptance.surfaces.canvas_references.evidence = @(@{ original_reference_checks = 2660; failures = 0 }) },
  { param($r) $r.original_runtime_acceptance.surfaces.audio.reason = '' },
  { param($r) $r.original_runtime_acceptance.surfaces.audio | Add-Member -NotePropertyName original_audio_checks -NotePropertyValue 337 },
  { param($r) $r.original_runtime_acceptance | Add-Member -NotePropertyName original_pixel_checks -NotePropertyValue 78 },
  { param($r) $r.original_runtime_acceptance.surfaces.voices.evidence = $null },
  { param($r) $r.original_runtime_acceptance.source_goldens_are_runtime_acceptance = 0 },
  { param($r) $r.original_runtime_acceptance.surfaces.webgl_capabilities.status = 'passed' },
  { param($r) $r.original_runtime_acceptance.surfaces.webgl_capabilities.evidence = @(@{ original_webgl_public_checks = 4327; failures = 0 }) },
  { param($r) $r.original_runtime_acceptance.surfaces.webgl_translated_shaders.status = 'passed' },
  { param($r) $r.original_runtime_acceptance.surfaces.webgl_translated_shaders.evidence = @(@{ original_webgl_translated_checks = 32; failures = 0 }) },
  { param($r) $r.original_runtime_acceptance.surfaces.PSObject.Properties.Remove('glyph_metrics') },
  { param($r) $r.original_runtime_acceptance.surfaces.glyph_metrics.status = 'passed' },
  { param($r) $r.original_runtime_acceptance.surfaces.glyph_metrics.evidence = @(@{ original_glyph_metrics_checks = 94; failures = 0 }) }
)
foreach ($mutate in $mutations) {
  $fixture = Original-ScopeFixture
  & $mutate $fixture
  $rejected = $false
  try { Assert-OriginalRuntimeScope $fixture } catch { $rejected = $true }
  if (-not $rejected) { throw "Invalid original runtime scope accepted at case $scopeChecks" }
  $scopeChecks++
}
@{ checks = $count; original_runtime_scope_checks = $scopeChecks; failures = 0;
   qualification = 'Source receipt-parser fixtures only, no browser execution' } | ConvertTo-Json -Compress
