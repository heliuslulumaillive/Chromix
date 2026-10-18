param(
  [Parameter(Mandatory)][string]$BrowserPath,
  [string]$Credentials = (Join-Path $env:USERPROFILE '.config\chromix-build\google-api-credentials.json'),
  [string]$ProfileDirectory = (Join-Path $env:USERPROFILE '.config\chromix-build\google-account-probe-profile')
)
$ErrorActionPreference = 'Stop'
$browser = (Resolve-Path -LiteralPath $BrowserPath).Path
$configuration = Get-Content -LiteralPath $Credentials -Raw | ConvertFrom-Json
$names = @('GOOGLE_API_KEY', 'GOOGLE_DEFAULT_CLIENT_ID', 'GOOGLE_DEFAULT_CLIENT_SECRET')
$saved = @{}
foreach ($name in $names) {
  $value = [string]$configuration.$name
  if ([string]::IsNullOrWhiteSpace($value)) { throw "Missing local configuration field: $name" }
  $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$profile = [IO.Path]::GetFullPath($ProfileDirectory)
New-Item -ItemType Directory -Path $profile -Force | Out-Null
try {
  foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, [string]$configuration.$name, 'Process') }
  # This is an intentionally visible browser for the owner's interactive login
  # and PIN/device confirmation. Credentials are inherited, never command args.
  Start-Process -FilePath $browser -ArgumentList @("--user-data-dir=`"$profile`"", '--no-first-run', '--no-default-browser-check', 'chrome://settings/people') -WindowStyle Normal
  Write-Host 'Account probe opened with a separate persistent profile. API credentials were not printed.'
} finally {
  foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
}
