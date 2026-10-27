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
}

function Fail-Run {
    param(
        [string]$Message,
        [int]$Code = 1
    )

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

    if ($PipelineExitCode -ne 0) {
        Fail-Run `
            -Message "run_pipeline.py returned exit code $PipelineExitCode" `
            -Code $PipelineExitCode
    }

    # Validate final outputs
    $ReportRoot = Join-Path $Root "data\processed\reports\$RunDate\window_1d"
    $ReportManifest = Join-Path $ReportRoot "report_manifest.json"
    $DigestHtml = Join-Path $ReportRoot "$BriefName.html"
    $DigestCsv = Join-Path $ReportRoot "$BriefName Articles.csv"
    $CorpusManifest = Join-Path $Root "data\processed\ml_corpus\$RunDate\window_1d\corpus_manifest.json"
    $TaxonomyManifest = Join-Path $Root "data\processed\taxonomy\$RunDate\window_1d\taxonomy_manifest.json"

    foreach ($Required in @(
        $ReportManifest,
        $DigestHtml,
        $DigestCsv,
        $CorpusManifest,
        $TaxonomyManifest
    )) {
        if (-not (Test-Path $Required)) {
            Fail-Run -Message "Required production output missing: $Required" -Code 20
        }
    }

    if ((Get-Item $DigestHtml).Length -lt 500) {
        Fail-Run -Message "Final $BriefName.html is unexpectedly small." -Code 21
    }

    $Report = Get-Content -Path $ReportManifest -Raw | ConvertFrom-Json
    $Corpus = Get-Content -Path $CorpusManifest -Raw | ConvertFrom-Json
    $Taxonomy = Get-Content -Path $TaxonomyManifest -Raw | ConvertFrom-Json

    if ($Report.status -ne "SUCCESS") {
        Fail-Run -Message "Report manifest status is not SUCCESS: $($Report.status)" -Code 22
    }

    if ($Corpus.status -ne "SUCCESS") {
        Fail-Run -Message "Corpus manifest status is not SUCCESS: $($Corpus.status)" -Code 23
    }

    if ($Taxonomy.status -ne "SUCCESS") {
        Fail-Run -Message "Taxonomy manifest status is not SUCCESS: $($Taxonomy.status)" -Code 24
    }

    $All = [int]$Report.metrics.all_phase2_article_count
    $Keep = [int]$Report.metrics.stage_a_keep_article_count
    $Noise = [int]$Report.metrics.stage_a_noise_audit_count

    if (($Keep + $Noise) -ne $All) {
        Fail-Run `
            -Message "Final report does not reconcile: ALL=$All KEEP=$Keep NOISE=$Noise" `
            -Code 25
    }

    $CorpusKeep = [int]$Corpus.stage_a_keep_article_count
    $CorpusNoise = [int]$Corpus.stage_a_noise_audit_count

    if (($CorpusKeep -ne $Keep) -or ($CorpusNoise -ne $Noise)) {
        Fail-Run `
            -Message "Phase-3/report mismatch: Phase3=$CorpusKeep/$CorpusNoise Report=$Keep/$Noise" `
            -Code 26
    }

    $TaxonomyCount = $null

    if ($null -ne $Taxonomy.stage_a_keep_article_count) {
        $TaxonomyCount = [int]$Taxonomy.stage_a_keep_article_count
    }
    elseif ($null -ne $Taxonomy.article_count) {
        $TaxonomyCount = [int]$Taxonomy.article_count
    }

    if (($null -ne $TaxonomyCount) -and ($TaxonomyCount -ne $Keep)) {
        Fail-Run `
            -Message "Taxonomy/report mismatch: Taxonomy=$TaxonomyCount Report=$Keep" `
            -Code 27
    }

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

    $DeliveryStatus = [ordered]@{
        status            = "READY"
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
