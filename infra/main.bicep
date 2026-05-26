// Entry-point deployment for the Case Assistant Agent.
// Provisions Azure AI Foundry alongside Cosmos DB, AI Search, Key Vault,
// Application Insights (+ Log Analytics), a storage account, and a separate
// Cognitive Services multi-service account for Language + Document Intelligence.
targetScope = 'resourceGroup'

@description('Base name used to derive resource names. 2-20 chars recommended.')
@minLength(2)
@maxLength(20)
param baseName string

@description('Azure region for resources.')
param location string = resourceGroup().location

@description('Tags applied to all resources.')
param tags object = {
  workload: 'case-assistant-agent'
}

// ---------- Foundry ----------
@description('SKU name for the AI Foundry account.')
param foundrySkuName string = 'S0'

@description('Public network access for the Foundry account.')
@allowed([
  'Enabled'
  'Disabled'
])
param foundryPublicNetworkAccess string = 'Enabled'

@description('Model deployments to create under the Foundry account. See modules/foundry.bicep for the schema.')
param modelDeployments array = []

// ---------- AI Search ----------
@description('SKU name for AI Search.')
@allowed([
  'free'
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSkuName string = 'standard'

@description('Auth mode for AI Search. "aadOnly" disables keys (Entra ID only); "both" allows keys + AAD.')
@allowed([
  'aadOnly'
  'both'
])
param searchAuthMode string = 'aadOnly'

// ---------- AI Services (multi-service: Language + Doc Intelligence) ----------
@description('Disable AI Services local (key-based) auth. When true, only Entra ID is allowed.')
param aiServicesDisableLocalAuth bool = true

// ---------- Cosmos DB ----------
@description('Cosmos DB database name.')
param cosmosDatabaseName string = 'agentic_rag'

@description('Cosmos DB container name for conversations.')
param cosmosContainerName string = 'conversations'

@description('Disable Cosmos DB local (key-based) auth. When true, only Entra ID RBAC is allowed.')
param cosmosDisableLocalAuth bool = true

// ---------- Storage ----------
@description('Allow shared key (account key) access on the storage account.')
param storageAllowSharedKeyAccess bool = false

// ---------- Naming ----------
// A short hash derived from the resource group ID makes globally-unique names
// (Foundry custom subdomain, AI Services, Search, Cosmos, Key Vault, Storage)
// stable per-RG but distinct across deployments / tenants. Override any name
// individually with the *Name parameters below.
var sanitizedBase = toLower(replace(baseName, '-', ''))
var uniqueSuffix = substring(uniqueString(resourceGroup().id, baseName), 0, 5)

@description('Override the Foundry account name. Leave empty to auto-generate (recommended).')
param foundryNameOverride string = ''
@description('Override the AI Services (multi-service) account name. Leave empty to auto-generate.')
param aiServicesNameOverride string = ''
@description('Override the AI Search service name. Leave empty to auto-generate.')
param searchNameOverride string = ''
@description('Override the Cosmos DB account name. Leave empty to auto-generate.')
param cosmosAccountNameOverride string = ''
@description('Override the Key Vault name. Leave empty to auto-generate. Max 24 chars.')
param keyVaultNameOverride string = ''
@description('Override the storage account name. Leave empty to auto-generate. Max 24 chars, lowercase alphanumeric.')
param storageAccountNameOverride string = ''

var foundryName = empty(foundryNameOverride) ? '${baseName}-foundry-${uniqueSuffix}' : foundryNameOverride
var aiServicesName = empty(aiServicesNameOverride) ? '${baseName}-aiservices-${uniqueSuffix}' : aiServicesNameOverride
var searchName = empty(searchNameOverride) ? toLower('${baseName}-search-${uniqueSuffix}') : searchNameOverride
var cosmosAccountName = empty(cosmosAccountNameOverride) ? toLower('${baseName}-cosmos-${uniqueSuffix}') : cosmosAccountNameOverride
// Key Vault name must be 3-24 chars, alphanumeric + hyphens.
var keyVaultName = empty(keyVaultNameOverride) ? take(toLower('${baseName}-kv-${uniqueSuffix}'), 24) : keyVaultNameOverride
var logAnalyticsName = '${baseName}-law-${uniqueSuffix}'
var appInsightsName = '${baseName}-appi-${uniqueSuffix}'
// Storage account names must be 3-24 lowercase alphanumeric (no hyphens).
var storageAccountName = empty(storageAccountNameOverride) ? take('${sanitizedBase}st${uniqueSuffix}', 24) : storageAccountNameOverride

// ---------- Modules ----------

module foundry 'modules/foundry.bicep' = {
  name: 'foundry-deploy'
  params: {
    name: foundryName
    location: location
    skuName: foundrySkuName
    customSubDomainName: toLower(foundryName)
    publicNetworkAccess: foundryPublicNetworkAccess
    tags: tags
    modelDeployments: modelDeployments
  }
}

module aiServices 'modules/aiservices.bicep' = {
  name: 'aiservices-deploy'
  params: {
    name: aiServicesName
    location: location
    customSubDomainName: toLower(aiServicesName)
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: aiServicesDisableLocalAuth
    tags: tags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-deploy'
  params: {
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    location: location
    tags: tags
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault-deploy'
  params: {
    name: keyVaultName
    location: location
    tags: tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage-deploy'
  params: {
    name: storageAccountName
    location: location
    tags: tags
    allowSharedKeyAccess: storageAllowSharedKeyAccess
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos-deploy'
  params: {
    accountName: cosmosAccountName
    location: location
    tags: tags
    databaseName: cosmosDatabaseName
    containerName: cosmosContainerName
    disableLocalAuth: cosmosDisableLocalAuth
  }
}

module search 'modules/search.bicep' = {
  name: 'search-deploy'
  params: {
    name: searchName
    location: location
    tags: tags
    skuName: searchSkuName
    authMode: searchAuthMode
  }
}

// ---------- Foundry connections ----------
// Note: RBAC role assignments for the Foundry project's managed identity and
// for local developer access are handled out-of-band by scripts/setup_rbac.py.

module foundryConnections 'modules/foundry-connections.bicep' = {
  name: 'foundry-connections-deploy'
  params: {
    foundryAccountName: foundry.outputs.name
    projectName: foundry.outputs.projectName
    searchId: search.outputs.id
    searchEndpoint: search.outputs.endpoint
    cosmosId: cosmos.outputs.id
    cosmosEndpoint: cosmos.outputs.endpoint
    storageId: storage.outputs.id
    storageBlobEndpoint: storage.outputs.blobEndpoint
    appInsightsId: monitoring.outputs.appInsightsId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

// ---------- Outputs ----------
output foundryId string = foundry.outputs.id
output foundryName string = foundry.outputs.name
output foundryEndpoint string = foundry.outputs.endpoint
output foundryPrincipalId string = foundry.outputs.principalId
output foundryProjectId string = foundry.outputs.projectId
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectPrincipalId string = foundry.outputs.projectPrincipalId
output foundryDeploymentNames array = foundry.outputs.deploymentNames

output aiServicesId string = aiServices.outputs.id
output aiServicesName string = aiServices.outputs.name
output aiServicesEndpoint string = aiServices.outputs.endpoint

output cosmosAccountName string = cosmos.outputs.name
output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabaseName string = cosmos.outputs.databaseName
output cosmosContainerName string = cosmos.outputs.containerName

output searchName string = search.outputs.name
output searchEndpoint string = search.outputs.endpoint

output keyVaultName string = keyVault.outputs.name
output keyVaultUri string = keyVault.outputs.uri

output storageAccountName string = storage.outputs.name
output storageBlobEndpoint string = storage.outputs.blobEndpoint

output logAnalyticsName string = monitoring.outputs.logAnalyticsName
output appInsightsName string = monitoring.outputs.appInsightsName
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
