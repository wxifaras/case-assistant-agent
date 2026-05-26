// Creates Foundry project connections to dependent Azure resources.
// All connections use AAD (managed identity) auth — no keys stored.
//
// Note on Key Vault: a Foundry-level KV connection is intentionally NOT created.
// The Foundry control plane treats an account-level KV connection as the
// workspace's primary associated KV, which conflicts with the project's other
// connections ("Please make sure delete all workspace connections before
// switching key vault"). The Foundry project's managed identity is granted
// 'Key Vault Secrets User' RBAC by scripts/setup_rbac.py, so application code
// can reach the vault directly via DefaultAzureCredential.

@description('Name of the parent Foundry (AI Services) account.')
param foundryAccountName string

@description('Name of the Foundry project to attach connections to.')
param projectName string

@description('Azure AI Search resource ID.')
param searchId string

@description('Azure AI Search endpoint (https://<name>.search.windows.net).')
param searchEndpoint string

@description('Cosmos DB account resource ID.')
param cosmosId string

@description('Cosmos DB document endpoint (https://<account>.documents.azure.com:443/).')
param cosmosEndpoint string

@description('Storage account resource ID.')
param storageId string

@description('Storage blob endpoint.')
param storageBlobEndpoint string

@description('Application Insights component resource ID.')
param appInsightsId string

@description('Application Insights connection string.')
@secure()
param appInsightsConnectionString string

resource foundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: foundry
  name: projectName
}

resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'aisearch'
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: searchId
    }
  }
}

resource cosmosConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'cosmosdb'
  properties: {
    category: 'CosmosDb'
    target: cosmosEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: cosmosId
    }
  }
}

resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'storage'
  properties: {
    category: 'AzureStorageAccount'
    target: storageBlobEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: storageId
    }
  }
}

resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsightsId
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: appInsightsConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsightsId
    }
  }
}
