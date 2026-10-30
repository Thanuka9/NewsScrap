param()

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (
    Split-Path -Parent $MyInvocation.MyCommand.Path
)

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Pipeline = Join-Path $Root "scripts\run_pipeline.py"
$FreezeFile = Join-Path $Root "config\production_freeze.json"

$SchedulerRoot = Join-Path $Root "data\scheduler"
$LogRoot = Join-Path $Root "data\logs\scheduler"
$DeliveryRoot = Join-Path $Root "data\delivery"
$AlertRoot = Join-Path $SchedulerRoot "alerts"

foreach ($Dir in @(
    $SchedulerRoot,
    $LogRoot,
    $DeliveryRoot,
    $AlertRoot
)) {
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
}

$Now = Get-Date
$RunDate = $Now.ToString("yyyy-MM-dd")
$BriefName = "Daily Financial Sector News Brief $RunDate"
$RunId = $Now.ToString("yyyyMMdd_HHmmss")

$LogFile = Join-Path $LogRoot "news_pipeline_$RunId.log"
$StatusFile = Join-Path $SchedulerRoot "last_run_status.json"
$DailyDelivery = Join-Path $DeliveryRoot $RunDate
$LatestDelivery = Join-Path $DeliveryRoot "latest"

$Mutex = New-Object System.Threading.Mutex(
    $false,
    "Global\CBSL_NEWS_INTELLIGENCE_DAILY"
)

$HasMutex = $false

function Write-RunStatus {
    param(
        [string]$Status,
        [string]$Message,
        [int]$ExitCode,
        [hashtable]$Extra = @{}
    )

    $Payload = [ordered]@{
        run_id       = $RunId
        run_date     = $RunDate
        status       = $Status
        message      = $Message
        exit_code    = $ExitCode
        timestamp    = (Get-Date).ToString("o")
        project_root = $Root
        log_file     = $LogFile
    }

    foreach ($Key in $Extra.Keys) {
        $Payload[$Key] = $Extra[$Key]
    }

    $Payload |
        ConvertTo-Json -Depth 12 |
        Set-Content -Path $StatusFile -Encoding UTF8

    # Append every finished run, so operational readiness is measured from
    # history (outcome, runtime, and which frozen build ran), not asserted.
    if ($Status -ne "RUNNING") {
        $FreezeHash = ""
        if (Test-Path $FreezeFile) { $FreezeHash = (Get-FileHash -Path $FreezeFile -Algorithm SHA256).Hash }
        $Record = [ordered]@{
            run_id           = $RunId
            run_date         = $RunDate
            status           = $Status
            exit_code        = $ExitCode
            started_at       = $Now.ToString("o")
            finished_at      = (Get-Date).ToString("o")
            duration_minutes = [Math]::Round(((Get-Date) - $Now).TotalMinutes, 1)
            freeze_sha256    = $FreezeHash
            message          = $Message
        }
        Add-Content -Path (Join-Path $SchedulerRoot "run_history.jsonl") -Value ($Record | ConvertTo-Json -Compress) -Encoding UTF8
    }
}

function Send-DesktopAlert {
    # A file in data\scheduler\alerts is only seen if someone looks for it;
    # also raise a Windows notification. Never let alerting stop the run.
    param([string]$Title, [string]$Text)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $Icon = New-Object System.Windows.Forms.NotifyIcon
        $Icon.Icon = [System.Drawing.SystemIcons]::Warning
        $Icon.BalloonTipTitle = $Title
        $Icon.BalloonTipText = $Text.Substring(0, [Math]::Min(250, $Text.Length))
        $Icon.Visible = $true
        $Icon.ShowBalloonTip(30000)
        Start-Sleep -Seconds 6
        $Icon.Dispose()
        return $true
    }
    catch {
        return $false
    }
}

function Fail-Run {
    param(
        [string]$Message,
        [int]$Code = 1
    )

    $null = Send-DesktopAlert -Title "News brief run FAILED ($RunDate)" -Text $Message
    $AlertFile = Join-Path $AlertRoot "FAILED_$RunId.txt"

    @"
CBSL NEWS INTELLIGENCE DAILY RUN FAILED

Run ID    : $RunId
Run date  : $RunDate
Time      : $(Get-Date -Format o)
Exit code : $Code
Message   : $Message
Log       : $LogFile
"@ |
        Set-Content -Path $AlertFile -Encoding UTF8

    Write-RunStatus `
        -Status "FAILED" `
        -Message $Message `
        -ExitCode $Code `
        -Extra @{ alert_file = $AlertFile }

    Add-Content -Path $LogFile -Value "`nFAILED: $Message"

    Write-Host ""
    Write-Host "=============================================================="
    Write-Host "NEWS DAILY RUN FAILED"
    Write-Host "=============================================================="
    Write-Host $Message
    Write-Host "Log   : $LogFile"
    Write-Host "Alert : $AlertFile"

    exit $Code
}

try {
    $HasMutex = $Mutex.WaitOne(0, $false)

    if (-not $HasMutex) {
        Fail-Run -Message "Another News pipeline instance is already running." -Code 30
    }

    if (-not (Test-Path $Python)) {
        Fail-Run -Message "Virtual-environment Python missing: $Python" -Code 10
    }

    if (-not (Test-Path $Pipeline)) {
        Fail-Run -Message "Pipeline script missing: $Pipeline" -Code 10
    }

    if (-not (Test-Path $FreezeFile)) {
        Fail-Run -Message "Production freeze file missing: $FreezeFile" -Code 11
    }

    # Verify frozen code
    $Freeze = Get-Content -Path $FreezeFile -Raw | ConvertFrom-Json
    $HashFailures = @()

    foreach ($Entry in $Freeze.files) {
        $FullPath = Join-Path $Root $Entry.path

        if (-not (Test-Path $FullPath)) {
            $HashFailures += "$($Entry.path) [MISSING]"
            continue
        }

        $ActualHash = (Get-FileHash -Path $FullPath -Algorithm SHA256).Hash

        if ($ActualHash -ne $Entry.sha256) {
            $HashFailures += "$($Entry.path) [HASH CHANGED]"
        }
    }

    if ($HashFailures.Count -gt 0) {
        Fail-Run `
            -Message ("Frozen production code has changed: " + ($HashFailures -join "; ")) `
            -Code 12
    }

    # Daily backup of sent editions, sign-offs, state and config (keep 30).
    # A failed backup alerts but does not stop the day's edition.
    $BackupRoot = Join-Path $Root "data\backups"
    & $Python (Join-Path $Root "scripts\news_release_gate.py") backup --out $BackupRoot --project-root $Root 2>&1 |
        Tee-Object -FilePath $LogFile -Append | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $null = Send-DesktopAlert -Title "News brief backup FAILED ($RunDate)" -Text "The daily records backup failed; see $LogFile"
    }
    Get-ChildItem -Path $BackupRoot -Filter "news_scrap_records_*.zip" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue

    @"
================================================================================
CBSL NEWS INTELLIGENCE - SCHEDULED DAILY RUN
================================================================================
Run ID       : $RunId
Run date     : $RunDate
Started      : $(Get-Date -Format o)
Project root : $Root
Python       : $Python
Pipeline     : $Pipeline
Freeze       : PASS
================================================================================

"@ |
        Set-Content -Path $LogFile -Encoding UTF8

    Write-RunStatus -Status "RUNNING" -Message "Pipeline started." -ExitCode 0

    Push-Location $Root

    try {
        & $Python $Pipeline --date $RunDate 2>&1 |
            Tee-Object -FilePath $LogFile -Append

        $PipelineExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($PipelineExitCode -eq 2) {
        Write-RunStatus -Status "REVIEW_REQUIRED" -Message "Collection or editorial exceptions require review; no attachment delivered." -ExitCode 2
        $null = Send-DesktopAlert -Title "News brief needs review ($RunDate)" -Text "The edition is held: articles or publisher coverage need review before release. See data\processed\reports\$RunDate\window_1d\integrated_release_result.json"
        exit 2
    }

    if ($PipelineExitCode -ne 0) {
        Fail-Run `
            -Message "run_pipeline.py returned exit code $PipelineExitCode" `
            -Code $PipelineExitCode
    }

    # Both schedulers use this exact contract, including attachment/ledger hashes.
    Push-Location $Root
    try {
        $ValidatedJson = & $Python (Join-Path $Root "scripts\check_release.py") --date $RunDate
        $ValidationExitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
    if ($ValidationExitCode -ne 0) {
        Fail-Run -Message ("Release contract rejected outputs: " + ($ValidatedJson -join "`n")) -Code 22
    }
    $Validated = ($ValidatedJson -join "`n") | ConvertFrom-Json
    if ($Validated.status -eq "ALREADY_SENT") {
        Write-RunStatus -Status "ALREADY_SENT" -Message "Sent edition retained; no duplicate delivery." -ExitCode 0
        exit 0
    }
    if ($Validated.status -ne "QA_PASS_NOT_DELIVERED") {
        Fail-Run -Message "Unsupported release result: $($Validated.status)" -Code 22
    }
    $ReportRoot = Join-Path $Root "data\processed\reports\$RunDate\window_1d"
    $ReportManifest = $Validated.report_manifest
    $DigestHtml = $Validated.named_report
    $DigestCsv = Join-Path $ReportRoot "$BriefName Articles.csv"
    $All = [int]$Validated.article_count
    $Keep = [int]$Validated.retained_article_count
    $Noise = $All - $Keep

    # Publish only after all QA passes
    New-Item -ItemType Directory -Path $DailyDelivery -Force | Out-Null
    New-Item -ItemType Directory -Path $LatestDelivery -Force | Out-Null

    Copy-Item -Path $DigestHtml `
        -Destination (Join-Path $DailyDelivery "$BriefName.html") `
        -Force

    Copy-Item -Path $ReportManifest `
        -Destination (Join-Path $DailyDelivery "report_manifest.json") `
        -Force

    Copy-Item -Path $DigestCsv `
        -Destination (Join-Path $DailyDelivery "$BriefName Articles.csv") `
        -Force

    Copy-Item -Path $DigestHtml `
        -Destination (Join-Path $LatestDelivery "$BriefName.html") `
        -Force

    Copy-Item -Path $ReportManifest `
        -Destination (Join-Path $LatestDelivery "report_manifest.json") `
        -Force

    foreach ($PublishedCopy in @((Join-Path $DailyDelivery "$BriefName.html"), (Join-Path $LatestDelivery "$BriefName.html"))) {
        if ((Get-FileHash -LiteralPath $PublishedCopy -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Validated.sha256) {
            Remove-Item -LiteralPath $PublishedCopy -Force
            Fail-Run -Message "Delivery copy differs from validated revision." -Code 28
        }
    }

    $DeliveryStatus = [ordered]@{
        status            = "QA_PASS_NOT_DELIVERED"
        revision_id       = $Validated.revision_id
        attachment_sha256 = $Validated.sha256
        run_id            = $RunId
        publication_date  = $RunDate
        generated_at      = (Get-Date).ToString("o")
        all_articles      = $All
        retained_articles = $Keep
        filtered_articles = $Noise
        report            = (Join-Path $LatestDelivery "$BriefName.html")
        source_log        = $LogFile
    }

    $DeliveryStatus |
        ConvertTo-Json -Depth 10 |
        Set-Content `
            -Path (Join-Path $LatestDelivery "delivery_status.json") `
            -Encoding UTF8

    Write-RunStatus `
        -Status "SUCCESS" `
        -Message "Pipeline and final QA passed." `
        -ExitCode 0 `
        -Extra @{
            all_articles    = $All
            keep_articles   = $Keep
            noise_articles  = $Noise
            delivery_folder = $DailyDelivery
            latest_report   = (Join-Path $LatestDelivery "$BriefName.html")
        }

    # Retain 60 days of scheduler logs
    Get-ChildItem -Path $LogRoot -File -Filter "*.log" |
        Where-Object {
            $_.LastWriteTime -lt (Get-Date).AddDays(-60)
        } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "=============================================================="
    Write-Host "NEWS DAILY RUN: PASS"
    Write-Host "=============================================================="
    Write-Host "Publication date : $RunDate"
    Write-Host "Articles         : $All"
    Write-Host "Retained         : $Keep"
    Write-Host "Filtered         : $Noise"
    Write-Host "Log              : $LogFile"
    Write-Host "Ready report     : $(Join-Path $LatestDelivery "$BriefName.html")"
    Write-Host "=============================================================="

    $null = Send-DesktopAlert -Title "News brief ready to approve ($RunDate)" -Text "QA passed: $Keep stories. Approve with news_release_gate.py promote, then send."
    exit 0
}
catch {
    Fail-Run -Message $_.Exception.Message -Code 99
}
finally {
    if ($HasMutex) {
        try {
            $Mutex.ReleaseMutex()
        }
        catch {
        }
    }

    $Mutex.Dispose()
}
