param(
    # Run now, whatever the day, time or earlier result; a finished collection is reused.
    [switch]$Force
)

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

function Invoke-Pipeline {
    param([bool]$ReuseCollection)

    # Python writes tracebacks to stderr; that must not end this script before
    # the exit code can be read.
    $ErrorActionPreference = "Continue"
    $Arguments = @($Pipeline, "--date", $RunDate)
    if ($ReuseCollection) {
        $Arguments += "--skip-collection"
    }
    & $Python @Arguments 2>&1 | ForEach-Object { "$_" } |
        Tee-Object -FilePath $LogFile -Append | Write-Host
    return $LASTEXITCODE
}

try {
    $HasMutex = $Mutex.WaitOne(0, $false)

    if (-not $HasMutex) {
        # A run started early (-Force) is still going; the 08:30 trigger stands down quietly.
        Write-Host "NEWS DAILY RUN: SKIP_ALREADY_RUNNING"
        exit 0
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
        # Code changed since the last freeze (for example a cleanup commit). The full
        # test suite decides: a passing build is re-frozen and the run continues; a
        # failing one stops here. A changed model file is never accepted this way.
        $ModelChanges = @($HashFailures | Where-Object { $_ -match '^data[\\/]' })
        if ($ModelChanges.Count -gt 0) {
            Fail-Run `
                -Message ("Frozen model files have changed: " + ($ModelChanges -join "; ")) `
                -Code 12
        }
        Write-Host ("Frozen code changed: " + ($HashFailures -join "; ") + ". Running the full test suite before continuing.")
        "[$(Get-Date -Format o)] Auto re-freeze attempt; changed: " + ($HashFailures -join "; ") | Out-File -FilePath $LogFile -Append -Encoding utf8
        & $Python (Join-Path $Root "scripts\news_release_gate.py") freeze --operator "scheduled run (re-frozen after the full test suite passed)" 2>&1 |
            ForEach-Object { "$_" } | Tee-Object -FilePath $LogFile -Append | Select-Object -Last 2 | Write-Host
        if ($LASTEXITCODE -ne 0) {
            Fail-Run `
                -Message ("Frozen production code has changed and the test suite did not pass: " + ($HashFailures -join "; ")) `
                -Code 12
        }
    }

    # This task also fires at login, so a PC that was off at 08:30 catches up.
    $Gate = Join-Path $Root "scripts\news_release_gate.py"
    if ($Force) {
        $Decision = & $Python $Gate run-decision --project-root $Root --force | Select-Object -Last 1
    }
    else {
        $Decision = & $Python $Gate run-decision --project-root $Root | Select-Object -Last 1
    }
    if ($Decision -like "SKIP_*") {
        Write-Host "NEWS DAILY RUN: $Decision"
        exit 0
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

    # Only registered editions stop stories repeating; warn, do not stop the run.
    & $Python $Gate previous-edition --date $RunDate --reports-root (Join-Path $Root "data\processed\reports") 2>&1 |
        Tee-Object -FilePath $LogFile -Append | Out-Null
    if ($LASTEXITCODE -eq 3) {
        $null = Send-DesktopAlert -Title "Previous news brief not registered ($RunDate)" -Text "Register the edition you sent with news_release_gate.py register-sent, or its stories may repeat."
    }

    Write-RunStatus -Status "RUNNING" -Message "Pipeline started." -ExitCode 0

    Push-Location $Root

    try {
        $ReuseCollection = $Decision -eq "RUN_SKIP_COLLECTION"
        $PipelineExitCode = Invoke-Pipeline -ReuseCollection $ReuseCollection

        # A failure after collection repeats only the later stages, once.
        if ($PipelineExitCode -ne 0 -and $PipelineExitCode -ne 2 -and -not $ReuseCollection) {
            $Collected = & $Python $Gate run-decision --project-root $Root --force | Select-Object -Last 1
            if ($Collected -eq "RUN_SKIP_COLLECTION") {
                Add-Content -Path $LogFile -Value "`nRESUMING after exit code $PipelineExitCode without re-collecting."
                $PipelineExitCode = Invoke-Pipeline -ReuseCollection $true
            }
        }
    }
    finally {
        Pop-Location
    }

    # A blocked or quiet source is worth knowing about whatever the run did.
    & $Python $Gate source-health --date $RunDate --project-root $Root 2>&1 |
        Tee-Object -FilePath $LogFile -Append | Out-Null
    if ($LASTEXITCODE -eq 3) {
        $null = Send-DesktopAlert -Title "News source problem ($RunDate)" -Text "A source looks blocked or far below normal; see data\source_status\source_health.json"
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

    # Publish the interactive dashboard produced by the same QA-passed ETL run.
    $InteractiveHtml = Join-Path $ReportRoot "$BriefName Interactive.html"
    $InteractiveReceipt = Join-Path $ReportRoot "$BriefName Interactive.validation.json"
    if (-not (Test-Path -LiteralPath $InteractiveHtml) -or -not (Test-Path -LiteralPath $InteractiveReceipt)) {
        Fail-Run -Message "Interactive dashboard or validation receipt missing." -Code 29
    }
    $InteractiveCheck = Get-Content -LiteralPath $InteractiveReceipt -Raw | ConvertFrom-Json
    if (-not $InteractiveCheck.source_unchanged -or
        $InteractiveCheck.source_sha256 -ne $Validated.sha256 -or
        [int]$InteractiveCheck.stories -ne [int]$Validated.consolidated_event_count) {
        Fail-Run -Message "Interactive dashboard does not match the validated news edition." -Code 29
    }
    $InteractiveHash = (Get-FileHash -LiteralPath $InteractiveHtml -Algorithm SHA256).Hash
    foreach ($DashboardFolder in @($DailyDelivery, $LatestDelivery)) {
        $InteractiveCopy = Join-Path $DashboardFolder "$BriefName Interactive.html"
        Copy-Item -LiteralPath $InteractiveHtml -Destination $InteractiveCopy -Force
        Copy-Item -LiteralPath $InteractiveReceipt -Destination (Join-Path $DashboardFolder "$BriefName Interactive.validation.json") -Force
        if ((Get-FileHash -LiteralPath $InteractiveCopy -Algorithm SHA256).Hash -ne $InteractiveHash) {
            Fail-Run -Message "Interactive delivery copy differs from the generated dashboard." -Code 29
        }
    }

    $DeliveryStatus = [ordered]@{
        interactive_dashboard = (Join-Path $DailyDelivery "$BriefName Interactive.html")
        latest_interactive_dashboard = (Join-Path $LatestDelivery "financial_brief_interactive.html")
        interactive_dashboard_sha256 = $InteractiveHash
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
