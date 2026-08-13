# Deploy the agent to a Pi (PowerShell wrapper around deploy.sh via Git Bash).
#   scripts/deploy.ps1 <pi-host>
param([Parameter(Mandatory = $true)][string]$PiHost)
$bash = "C:\Program Files\Git\bin\bash.exe"
& $bash (Join-Path $PSScriptRoot "deploy.sh") $PiHost
exit $LASTEXITCODE
