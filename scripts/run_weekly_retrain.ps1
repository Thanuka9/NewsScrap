# Weekly model retrain, after the daily brief run. Deploys only a better model.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Log = Join-Path $Root ("data\logs\weekly_retrain_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
New-Item -ItemType Directory -Path (Split-Path -Parent $Log) -Force | Out-Null
"[$(Get-Date -Format o)] Retrain task started." | Out-File -FilePath $Log -Encoding utf8

function Send-DesktopAlert {
    param([string]$Title, [string]$Text)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $Icon = New-Object System.Windows.Forms.NotifyIcon
        $Icon.Icon = [System.Drawing.SystemIcons]::Information
        $Icon.BalloonTipTitle = $Title
        $Icon.BalloonTipText = $Text.Substring(0, [Math]::Min(250, $Text.Length))
        $Icon.Visible = $true
        $Icon.ShowBalloonTip(30000)
        Start-Sleep -Seconds 6
        $Icon.Dispose()
    }
    catch {
    }
}

# Never compete with the daily brief for the CPU.
$Deadline = (Get-Date).AddHours(3)
while ((Get-ScheduledTask -TaskName "CBSL News Intelligence Daily").State -eq "Running" -and (Get-Date) -lt $Deadline) {
    "[$(Get-Date -Format o)] Waiting for the daily brief task to finish." | Out-File -FilePath $Log -Encoding utf8 -Append
    Start-Sleep -Seconds 60
}

Push-Location $Root
try {
    "[$(Get-Date -Format o)] Starting model training." | Out-File -FilePath $Log -Encoding utf8 -Append
    & $Python -u (Join-Path $Root "scripts\retrain_models.py") 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $Log -Append
    $Code = $LASTEXITCODE
}
finally {
    Pop-Location
}
$Verdict = (Select-String -Path $Log -Pattern "^\[RETRAIN\]" | Select-Object -Last 1).Line
if ($Code -ne 0 -or -not $Verdict) {
    Send-DesktopAlert -Title "News model retrain FAILED" -Text "The live models are unchanged. See $Log"
    exit 1
}
Send-DesktopAlert -Title "News model retrain finished" -Text $Verdict
exit 0
