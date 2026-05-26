#!/usr/bin/env pwsh
# Post-provision RBAC hook for `azd up` / `azd provision`.
# Reads bicep outputs from azd's environment and calls scripts/setup_rbac.py
# to assign the roles required for local development and for the Foundry
# project's managed identity (when AZURE_RBAC_PRINCIPAL_ID is set).

$ErrorActionPreference = 'Stop'

Write-Host "`n=== azd postprovision: RBAC setup ===" -ForegroundColor Cyan

if (-not $env:AZURE_RESOURCE_GROUP) {
    Write-Error "AZURE_RESOURCE_GROUP is not set. Did azd provision succeed?"
    exit 1
}

# Pick a Python interpreter — prefer the backend venv if it exists.
$venvPython = Join-Path $PSScriptRoot '..\backend\.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }

$scriptPath = Join-Path $PSScriptRoot 'setup_rbac.py'

# Build args from azd-exposed bicep outputs.
$commonArgs = @(
    '--subscription',           $env:AZURE_SUBSCRIPTION_ID,
    '--resource-group',         $env:AZURE_RESOURCE_GROUP,
    '--cosmos-account',         $env:COSMOSACCOUNTNAME,
    '--storage-account',        $env:STORAGEACCOUNTNAME,
    '--search-service',         $env:SEARCHNAME,
    '--ai-services-account',    $env:FOUNDRYNAME,
    '--ai-multiservice-account', $env:AISERVICESNAME,
    '--key-vault',              $env:KEYVAULTNAME,
    '--app-insights',           $env:APPINSIGHTSNAME
)

# 1. Grant roles to the signed-in user (required for local dev with DefaultAzureCredential).
Write-Host "`n--- Granting roles to signed-in user ---" -ForegroundColor Cyan
& $python $scriptPath @commonArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "RBAC setup for signed-in user failed (exit code $LASTEXITCODE)."
    exit $LASTEXITCODE
}

# 2. Grant roles to the Foundry project's managed identity (always present after provision).
$foundryProjectMi = $env:FOUNDRYPROJECTPRINCIPALID
if ($foundryProjectMi) {
    Write-Host "`n--- Granting roles to Foundry project managed identity ---" -ForegroundColor Cyan
    & $python $scriptPath @commonArgs `
        --principal-id $foundryProjectMi `
        --principal-name 'Foundry project MI'
    if ($LASTEXITCODE -ne 0) {
        Write-Error "RBAC setup for Foundry project MI failed (exit code $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
}

# 3. Optional extra principal (e.g. an app's user-assigned MI) via azd env var.
if ($env:AZURE_RBAC_PRINCIPAL_ID) {
    Write-Host "`n--- Granting roles to $($env:AZURE_RBAC_PRINCIPAL_ID) ---" -ForegroundColor Cyan
    & $python $scriptPath @commonArgs `
        --principal-id $env:AZURE_RBAC_PRINCIPAL_ID `
        --principal-name ($env:AZURE_RBAC_PRINCIPAL_NAME ?? 'azd-configured principal')
    if ($LASTEXITCODE -ne 0) {
        Write-Error "RBAC setup for AZURE_RBAC_PRINCIPAL_ID failed (exit code $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
}

Write-Host "`n=== RBAC setup complete ===" -ForegroundColor Green
