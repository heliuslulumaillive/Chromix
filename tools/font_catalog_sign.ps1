param(
  [ValidateSet('Initialize','Sign')][string]$Mode='Sign',
  [string]$KeyName='ShardX Lite Font Catalog v2',
  [Parameter(Mandatory)][string]$PublicKeyFile,
  [string]$PayloadFile,
  [string]$SignatureFile
)
$ErrorActionPreference='Stop'
function Assert-Plain([string]$Path) {
  if (-not [IO.Path]::IsPathRooted($Path) -or $Path.StartsWith('\\')) { throw 'Use absolute local paths' }
  $current=[IO.Path]::GetFullPath($Path)
  while($current) {
    if((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Redirected catalog path' }
    $parent=Split-Path -Parent $current
    if($parent -eq $current) { break }
    $current=$parent
  }
}
Assert-Plain $PublicKeyFile
$provider=[Security.Cryptography.CngProvider]::MicrosoftSoftwareKeyStorageProvider
$exists=[Security.Cryptography.CngKey]::Exists($KeyName,$provider)
if(-not $exists -and $Mode -ne 'Initialize') { throw 'Signing key is unavailable; initialization is explicit' }
if($exists) {
  $key=[Security.Cryptography.CngKey]::Open($KeyName,$provider)
} else {
  $parameters=[Security.Cryptography.CngKeyCreationParameters]::new()
  $parameters.Provider=$provider
  $parameters.ExportPolicy=[Security.Cryptography.CngExportPolicies]::None
  $parameters.KeyUsage=[Security.Cryptography.CngKeyUsages]::Signing
  $key=[Security.Cryptography.CngKey]::Create([Security.Cryptography.CngAlgorithm]::ECDsaP256,$KeyName,$parameters)
}
try {
  $public=$key.Export([Security.Cryptography.CngKeyBlobFormat]::EccPublicBlob)
  if($public.Length -ne 72 -or [BitConverter]::ToUInt32($public,0) -ne 0x31534345 -or [BitConverter]::ToUInt32($public,4) -ne 32) { throw 'Unexpected P-256 public key' }
  $publicHash=([Security.Cryptography.SHA256]::Create().ComputeHash($public) | ForEach-Object {$_.ToString('x2')}) -join ''
  $record=[ordered]@{schemaVersion=2;algorithm='ECDSA-P256-SHA256-P1363';domain='ShardXFontCatalog-v3';keyId=$publicHash;cngPublicBlob=[Convert]::ToBase64String($public)}
  if(Test-Path -LiteralPath $PublicKeyFile) {
    $expected=[IO.File]::ReadAllText($PublicKeyFile)|ConvertFrom-Json
    if($expected.keyId -cne $publicHash -or $expected.cngPublicBlob -cne $record.cngPublicBlob -or $expected.algorithm -cne $record.algorithm) { throw 'Local signer differs from the kernel trusted public key' }
  } elseif($Mode -eq 'Initialize') {
    [IO.File]::WriteAllText($PublicKeyFile,($record|ConvertTo-Json)+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
  } else { throw 'Trusted public key file is missing' }
  if($Mode -eq 'Initialize') {
    [ordered]@{status='ready';keyId=$publicHash;privateKeyExported=$false;keyStore='current-user Windows CNG'}|ConvertTo-Json -Compress
    return
  }
  Assert-Plain $PayloadFile; Assert-Plain $SignatureFile
  $payload=[IO.File]::ReadAllBytes($PayloadFile)
  if($payload.Length -lt 18 -or $payload.Length -gt 1MB) { throw 'Catalog payload length is invalid' }
  $magic=[Text.Encoding]::ASCII.GetString($payload,0,8)
  if($magic -ceq "SXFONT3`0" -and [BitConverter]::ToUInt16($payload,8) -eq 3) {
    $prefix=[Text.Encoding]::ASCII.GetBytes("ShardXFontCatalog-v3`0")
  } elseif($magic -ceq "SXFACE1`0" -and [BitConverter]::ToUInt16($payload,8) -eq 1) {
    $prefix=[Text.Encoding]::ASCII.GetBytes("ShardXFontFaceIndex-v1`0")
  } elseif($magic -ceq "SXFONT2`0" -and [BitConverter]::ToUInt16($payload,8) -eq 2) {
    $prefix=[Text.Encoding]::ASCII.GetBytes("ShardXFontCatalog-v2`0")
  } else { throw 'Unknown catalog protocol; refusing to sign' }
  $signed=[byte[]]::new($prefix.Length+$payload.Length)
  [Array]::Copy($prefix,0,$signed,0,$prefix.Length)
  [Array]::Copy($payload,0,$signed,$prefix.Length,$payload.Length)
  $ecdsa=[Security.Cryptography.ECDsaCng]::new($key)
  try {
    if(Test-Path -LiteralPath $SignatureFile) {
      $old=[IO.File]::ReadAllBytes($SignatureFile)
      if($old.Length -ne 64 -or -not $ecdsa.VerifyData($signed,$old,[Security.Cryptography.HashAlgorithmName]::SHA256)) { throw 'Existing signature differs; use a fresh immutable generation directory' }
      $reused=$true
    } else {
      $signature=$ecdsa.SignData($signed,[Security.Cryptography.HashAlgorithmName]::SHA256)
      if($signature.Length -ne 64 -or -not $ecdsa.VerifyData($signed,$signature,[Security.Cryptography.HashAlgorithmName]::SHA256)) { throw 'P-256 signature self-verification failed' }
      $stream=[IO.File]::Open($SignatureFile,'CreateNew','Write','None')
      try { $stream.Write($signature,0,$signature.Length) } finally { $stream.Dispose() }
      $reused=$false
    }
    [ordered]@{status='signed-and-verified';keyId=$publicHash;reused=$reused;privateKeyExported=$false}|ConvertTo-Json -Compress
  } finally { $ecdsa.Dispose() }
} finally { $key.Dispose() }
