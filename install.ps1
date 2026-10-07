param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^COM[1-9][0-9]*$')]
    [string]$Port
)

$ErrorActionPreference = 'Stop'
$sourceDirectory = $PSScriptRoot
$bundleDirectory = Join-Path $PSScriptRoot 'spaceagon_compass'
if (Test-Path -LiteralPath $bundleDirectory -PathType Container) {
    $sourceDirectory = $bundleDirectory
}

$appFiles = @('app.py', 'compass_math.py', 'compass_view.py', '__init__.py', 'metadata.json', 'tildagon.toml')
$sourceFiles = @()
foreach ($name in $appFiles) {
    $sourcePath = Join-Path $sourceDirectory $name
    if ($name -eq 'metadata.json' -and -not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        $sourcePath = Join-Path $sourceDirectory 'dev\metadata.json'
    }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Missing app file: $sourcePath"
    }
    $sourceFiles += $sourcePath
}

Write-Host "Installing Field Compass on $Port..."
$directorySetup = @'
import os
for path in ('/apps', '/apps/spaceagon_compass'):
    try:
        os.stat(path)
    except OSError:
        os.mkdir(path)
'@
& py -m mpremote connect $Port exec $directorySetup
if ($LASTEXITCODE -ne 0) {
    throw 'Cannot connect to the badge. Check its USB IN port and COM port.'
}
& py -m mpremote connect $Port fs cp @sourceFiles ':/apps/spaceagon_compass/'
if ($LASTEXITCODE -ne 0) {
    throw 'Copy failed. Reconnect the badge and run the installer again.'
}
Write-Host 'Files installed. Hold REBOOP for two seconds, then launch Field Compass.'
