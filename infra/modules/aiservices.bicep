// Azure AI multi-service Cognitive Services account (kind=CognitiveServices).
// Provides access to Language, Document Intelligence, Vision, Speech,
// Translator, and Content Safety under one endpoint/key. Distinct from the
// Foundry account (kind=AIServices) which handles Azure OpenAI workloads.

@description('Name of the Cognitive Services multi-service account.')
@minLength(2)
@maxLength(64)
param name string

@description('Azure region.')
param location string = resourceGroup().location

@description('Tags to apply.')
param tags object = {}

@description('SKU name. S0 enables all services under the multi-service account.')
param skuName string = 'S0'

@description('Custom subdomain. Required for Entra ID auth. Must be globally unique.')
param customSubDomainName string = toLower(name)

@description('Public network access setting.')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Enabled'

@description('Disable local (key-based) auth. When true, only Entra ID is allowed.')
param disableLocalAuth bool = false

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'CognitiveServices'
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: customSubDomainName
    disableLocalAuth: disableLocalAuth
    publicNetworkAccess: publicNetworkAccess
    networkAcls: {
      defaultAction: 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
  }
}

output id string = aiServices.id
output name string = aiServices.name
output endpoint string = aiServices.properties.endpoint
output principalId string = aiServices.identity.principalId
