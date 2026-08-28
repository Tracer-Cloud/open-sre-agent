[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("readme-install", "raw-artifact")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [string]$Repository = "Tracer-Cloud/opensre",
    [string]$InstallerUri = "https://install.opensre.com"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-OpenSreWindowsFacts {
    $operatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem
    $defenderEnabled = $null
    $realTimeProtectionEnabled = $null

    try {
        $defender = Get-MpComputerStatus -ErrorAction Stop
        $defenderEnabled = [bool]$defender.AntivirusEnabled
        $realTimeProtectionEnabled = [bool]$defender.RealTimeProtectionEnabled
    }
    catch {
        # Defender status is best-effort because third-party antivirus and some
        # restricted Windows hosts do not expose Get-MpComputerStatus.
    }

    return [ordered]@{
        caption = [string]$operatingSystem.Caption
        version = [string]$operatingSystem.Version
        architecture = [string][System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
        defender_enabled = $defenderEnabled
        defender_realtime_enabled = $realTimeProtectionEnabled
    }
}

function Measure-OpenSreHelp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BinaryPath
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    & $BinaryPath --help 1> $null 2> $null
    $exitCode = $LASTEXITCODE
    $stopwatch.Stop()

    if ($exitCode -ne 0) {
        throw "'$BinaryPath --help' exited with status $exitCode."
    }

    return [Math]::Round($stopwatch.Elapsed.TotalMilliseconds, 2)
}

function Get-OpenSreMainArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryName,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $headers = @{
        "Accept" = "application/vnd.github+json"
        "User-Agent" = "opensre-windows-startup-investigation"
    }
    $release = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/$RepositoryName/releases/tags/main-build" `
        -Headers $headers
    $architecture = [string][System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
    if ($architecture -ne "X64") {
        throw "The raw-artifact benchmark currently requires an x64 Windows runner; found '$architecture'."
    }

    $archiveName = "opensre_main_windows-x64.zip"
    $archiveAsset = @($release.assets) | Where-Object { $_.name -eq $archiveName } | Select-Object -First 1
    $checksumAsset = @($release.assets) | Where-Object { $_.name -eq "$archiveName.sha256" } | Select-Object -First 1
    if ($null -eq $archiveAsset -or $null -eq $checksumAsset) {
        throw "The main-build release is missing '$archiveName' or its checksum."
    }

    $archivePath = Join-Path $Destination $archiveName
    $checksumPath = "$archivePath.sha256"
    Invoke-WebRequest -Uri $archiveAsset.browser_download_url -Headers $headers -OutFile $archivePath
    Invoke-WebRequest -Uri $checksumAsset.browser_download_url -Headers $headers -OutFile $checksumPath

    $checksumLine = Get-Content -LiteralPath $checksumPath | Where-Object { $_ -match '^[A-Fa-f0-9]{64}\s+' } | Select-Object -First 1
    if (-not $checksumLine) {
        throw "Checksum file '$checksumPath' does not contain a SHA256 value."
    }
    $expectedHash = ($checksumLine -split '\s+')[0].ToLowerInvariant()
    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Checksum mismatch for '$archiveName'."
    }

    $extractionRoot = Join-Path $Destination "extracted"
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractionRoot -Force
    $binaries = @(Get-ChildItem -LiteralPath $extractionRoot -Filter "opensre.exe" -File -Recurse)
    if ($binaries.Count -ne 1) {
        throw "Expected one opensre.exe in '$archiveName'; found $($binaries.Count)."
    }

    return [ordered]@{
        binary_path = [string]$binaries[0].FullName
        artifact_name = $archiveName
        release_published_at = [string]$release.published_at
    }
}

$benchmarkRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("opensre-startup-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $benchmarkRoot | Out-Null

$installDurationMs = $null
$artifactName = ""
$releasePublishedAt = ""
$installMethod = ""
$binaryPath = ""
$overriddenEnvironment = @(
    "OPENSRE_INSTALL_DIR",
    "OPENSRE_INSTALL_CHANNEL",
    "OPENSRE_SKIP_GH_INSTALL",
    "OPENSRE_AUTO_LAUNCH"
)
$originalEnvironment = @{}
foreach ($name in $overriddenEnvironment) {
    $originalEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    if ($Mode -eq "readme-install") {
        $installDir = Join-Path $benchmarkRoot "bin"
        $env:OPENSRE_INSTALL_DIR = $installDir
        $env:OPENSRE_INSTALL_CHANNEL = "main"
        $env:OPENSRE_SKIP_GH_INSTALL = "1"
        $env:OPENSRE_AUTO_LAUNCH = "0"

        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        Invoke-Expression (Invoke-RestMethod -Uri $InstallerUri)
        $stopwatch.Stop()
        $installDurationMs = [Math]::Round($stopwatch.Elapsed.TotalMilliseconds, 2)
        $binaryPath = Join-Path $installDir "opensre.exe"
        $installMethod = "irm $InstallerUri | iex"
    }
    else {
        $artifact = Get-OpenSreMainArtifact -RepositoryName $Repository -Destination $benchmarkRoot
        $binaryPath = [string]$artifact.binary_path
        $artifactName = [string]$artifact.artifact_name
        $releasePublishedAt = [string]$artifact.release_published_at
        $installMethod = "download and extract main-build artifact without executing it"
    }

    if (-not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) {
        throw "OpenSRE binary was not found at '$binaryPath'."
    }

    $firstHelpMs = Measure-OpenSreHelp -BinaryPath $binaryPath
    $secondHelpMs = Measure-OpenSreHelp -BinaryPath $binaryPath
    $binarySizeBytes = (Get-Item -LiteralPath $binaryPath).Length
    $facts = Get-OpenSreWindowsFacts

    $result = [ordered]@{
        schema_version = 1
        measured_at_utc = [DateTime]::UtcNow.ToString("o")
        mode = $Mode
        install_method = $installMethod
        install_channel = "main"
        install_duration_ms = $installDurationMs
        first_help_ms = $firstHelpMs
        second_help_ms = $secondHelpMs
        binary_size_bytes = $binarySizeBytes
        artifact_name = $artifactName
        release_published_at = $releasePublishedAt
        packaging_mode = "PyInstaller onefile"
        windows = $facts
        notes = @(
            "The README installer runs opensre.exe --version while verifying the archive, before the first user invocation.",
            "The raw-artifact mode avoids that verification launch and therefore measures the executable's first process start.",
            "Bare interactive-shell startup is not measured because GitHub-hosted runners are non-interactive."
        )
    }

    $outputDirectory = Split-Path -Parent $OutputPath
    if ($outputDirectory) {
        New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    }
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding utf8

    $summary = @"
### OpenSRE Windows startup: $Mode

| Measurement | Result |
| --- | ---: |
| Windows | $($facts.caption) $($facts.version) ($($facts.architecture)) |
| Defender real-time protection | $($facts.defender_realtime_enabled) |
| Install / extraction | $installDurationMs ms |
| First opensre --help | $firstHelpMs ms |
| Second opensre --help | $secondHelpMs ms |
| Binary size | $binarySizeBytes bytes |
| Packaging | PyInstaller onefile |

The README installer executes opensre.exe --version during verification. Only the raw-artifact job measures the binary's true first process start.
"@
    Write-Host $summary
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Value $summary
    }
}
finally {
    foreach ($name in $overriddenEnvironment) {
        [System.Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], "Process")
    }
    Remove-Item -LiteralPath $benchmarkRoot -Recurse -Force -ErrorAction SilentlyContinue
}
