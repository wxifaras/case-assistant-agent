// Azure AI Search service.

@description('Name of the AI Search service. Must be globally unique, 2-60 lowercase alphanumeric/hyphens.')
@minLength(2)
@maxLength(60)
param name string

@description('Azure region.')
param location string = resourceGroup().location

@description('Tags to apply.')
param tags object = {}

@description('SKU name for AI Search.')
@allowed([
  'free'
  'basic'
  'standard'
  'standard2'
  'standard3'
  'storage_optimized_l1'
  'storage_optimized_l2'
])
param skuName string = 'standard'

@description('Number of replicas.')
@minValue(1)
@maxValue(12)
param replicaCount int = 1

@description('Number of partitions.')
@allowed([
  1
  2
  3
  4
  6
  12
])
param partitionCount int = 1

@description('Public network access setting.')
@allowed([
  'enabled'
  'disabled'
])
param publicNetworkAccess string = 'enabled'

@description('Auth options. Use "aadOnly" to require Entra ID, or "both" to allow keys + AAD.')
@allowed([
  'aadOnly'
  'both'
])
param authMode string = 'both'

@description('Enable semantic search (free or standard tier).')
@allowed([
  'disabled'
  'free'
  'standard'
])
param semanticSearch string = 'standard'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'default'
    publicNetworkAccess: publicNetworkAccess
    semanticSearch: semanticSearch
    disableLocalAuth: authMode == 'aadOnly'
    authOptions: authMode == 'aadOnly'
      ? null
      : {
          aadOrApiKey: {
            aadAuthFailureMode: 'http401WithBearerChallenge'
          }
        }
  }
}

output id string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
output principalId string = search.identity.principalId
