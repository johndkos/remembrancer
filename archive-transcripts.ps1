# Archives Claude Code session data (~\.claude\projects: transcripts, memory files)
# to durable storage so the raw conversation layer survives Claude Code's ~30-day
# transcript cleanup.
#
# ADDITIVE ONLY: no /MIR, no /PURGE — files pruned from the source are kept in the
# archive forever. Never add a deletion flag to this robocopy call.
#
# Register as a scheduled task (e.g. nightly) with your destination:
#   schtasks /Create /TN "Claude Transcript Archive" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"<path>\archive-transcripts.ps1\" -Destination \"<archive-root>\"" /SC DAILY /ST 03:30
#
# Note: per-project `memory` folders under the source may be junctions/symlinks;
# robocopy follows them by default, so memory content is archived as real files.
# Do not add /XJ if you want the memory layer preserved in the archive.

param(
    [string]$Source = (Join-Path $env:USERPROFILE ".claude\projects"),
    [Parameter(Mandatory = $true)][string]$Destination
)

$dst    = Join-Path $Destination "projects"
$logDir = Join-Path $Destination "logs"

New-Item -ItemType Directory -Force -Path $dst, $logDir | Out-Null
$log = Join-Path $logDir ("archive-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))

Add-Content $log "=== Archive run started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
robocopy $Source $dst /E /XO /R:2 /W:5 /NP /LOG+:$log | Out-Null
$code = $LASTEXITCODE

# Robocopy exit codes: 0 = nothing new, 1-7 = copied/extras (success), >=8 = failures
if ($code -ge 8) {
    Add-Content $log "ARCHIVE FAILED: robocopy exit code $code at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    exit 1
}
Add-Content $log "Archive run finished OK (robocopy exit code $code)"
exit 0
