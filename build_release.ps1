$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = '0.2.0'
$ReleaseRoot = Join-Path $ProjectRoot 'release'
$PyDistRoot = Join-Path $ReleaseRoot '_pyinstaller-dist'
$WorkRoot = Join-Path $ReleaseRoot '_pyinstaller-build'
$PackageName = "QMX-windows-x64-v$Version"
$Stage = Join-Path $ReleaseRoot $PackageName
$Archive = Join-Path $ReleaseRoot "$PackageName.zip"
$Installer = Join-Path $ReleaseRoot "QMX-Setup-x64-v$Version.exe"
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$AppIcon = Join-Path $ProjectRoot 'docs\assets\qmx-icon.ico'
$ISCC = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'

if (-not (Test-Path -LiteralPath $Python)) { throw "Build Python not found: $Python" }
if (-not (Test-Path -LiteralPath $ISCC)) { throw "Inno Setup compiler not found: $ISCC" }
$bits = & $Python -c "import struct; print(struct.calcsize('P') * 8)"
if ($LASTEXITCODE -ne 0 -or $bits.Trim() -ne '64') { throw 'A 64-bit Python environment is required to build the x64 release.' }

New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name QMX `
    --icon $AppIcon --add-data "${AppIcon};assets" `
    --specpath $ProjectRoot --distpath $PyDistRoot --workpath $WorkRoot `
    (Join-Path $ProjectRoot 'main.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }

$SourceBundle = Join-Path $PyDistRoot 'QMX'
if (-not (Test-Path -LiteralPath (Join-Path $SourceBundle 'QMX.exe'))) { throw 'PyInstaller output is incomplete.' }

$releasePrefix = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
$resolvedStage = [IO.Path]::GetFullPath($Stage)
if (-not $resolvedStage.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to replace a staging folder outside release/.' }
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage | Out-Null
Copy-Item -Path (Join-Path $SourceBundle '*') -Destination $Stage -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination $Stage
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README_EN.md') -Destination $Stage
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'THIRD_PARTY_NOTICES.md') -Destination $Stage
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'licenses') -Destination (Join-Path $Stage 'licenses') -Recurse
$DocsAssets = Join-Path $Stage 'docs\assets'
New-Item -ItemType Directory -Force -Path $DocsAssets | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs\assets\qmx-icon.png'), `
    (Join-Path $ProjectRoot 'docs\assets\qmx-icon.ico'), `
    (Join-Path $ProjectRoot 'docs\assets\qmx-main.png') -Destination $DocsAssets

if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($Stage, $Archive, [IO.Compression.CompressionLevel]::Optimal, $false)
& $ISCC "/DAppVersion=$Version" "/DPackageDir=$Stage" (Join-Path $ProjectRoot 'installer\QMX.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }

$ChecksumFile = Join-Path $ReleaseRoot 'SHA256SUMS.txt'
Get-FileHash -LiteralPath $Archive, $Installer -Algorithm SHA256 |
    ForEach-Object { '{0}  {1}' -f $_.Hash, [IO.Path]::GetFileName($_.Path) } |
    Set-Content -LiteralPath $ChecksumFile -Encoding ascii
Get-Item -LiteralPath $Archive, $Installer, $ChecksumFile | Select-Object FullName, Length
