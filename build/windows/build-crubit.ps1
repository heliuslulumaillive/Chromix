param([Parameter(Mandatory)][string]$Src)
$ErrorActionPreference = 'Stop'
$rust = Join-Path $Src 'third_party\rust-toolchain'
$bin = Join-Path $rust 'bin'
$crubit = Join-Path $rust 'crubit'
$exe = Join-Path $crubit 'bin\cc_bindings_from_rs.exe'
if (Test-Path -LiteralPath $exe -PathType Leaf) {
  & $exe --help | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Existing Crubit cannot load the selected Rust compiler' }
  return
}
$target = Join-Path $Src 'out\crubit'
$env:RUSTC = Join-Path $bin 'rustc.exe'
$env:CARGO_HOME = Join-Path $target 'cargo_home'
$env:RUSTFLAGS = '-Ctarget-feature=+crt-static'
& (Join-Path $bin 'cargo.exe') build --release --locked -p cc_bindings_from_rs --bin cc_bindings_from_rs --manifest-path (Join-Path $crubit 'lib\third_party\crubit\Cargo.toml') --target-dir $target
if ($LASTEXITCODE -ne 0) { throw 'The locked Crubit build failed' }
$output = Split-Path -Parent $exe
$null = New-Item -ItemType Directory -Force -Path $output
Get-ChildItem -LiteralPath $bin -File -Filter '*.dll' | Copy-Item -Destination $output
Copy-Item -LiteralPath (Join-Path $target 'release\cc_bindings_from_rs.exe') -Destination $exe
& $exe --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Crubit cannot load the selected Rust compiler' }
