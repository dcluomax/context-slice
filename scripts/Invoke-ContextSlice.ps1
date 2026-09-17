[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Query,
    [string]$Scope,
    [ValidateRange(1024,262144)][int]$MaxBytes = 8192,
    [ValidateRange(1,20)][int]$Limit = 3,
    [string]$Session,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$entrypoint = Join-Path (Split-Path $PSScriptRoot -Parent) 'context_slice_cli.py'
$arguments = @($entrypoint, 'brief', '--root', $Root, '--limit', $Limit, '--max-bytes', $MaxBytes)
if ($Scope) { $arguments += @('--scope', $Scope) }
if ($Session) { $arguments += @('--session', $Session) }
$arguments += @('--', $Query)
& $Python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Context Slice exited with code $LASTEXITCODE. No retrieval success is implied."
}
