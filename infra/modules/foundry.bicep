// Provisions an Azure AI Foundry account (Cognitive Services / kind=AIServices)
// with local authentication disabled, plus a default Foundry project.
// Access requires Microsoft Entra ID (RBAC).

@description('Name of the Azure AI Foundry (AI Services) account.')
@minLength(2)
@maxLength(64)
param name string

@description('Azure region for the Foundry account.')
param location string = resourceGroup().location

@description('SKU name for the AI Services account.')
param skuName string = 'S0'

@description('Custom subdomain name. Required for Entra ID (AAD) authentication. Must be globally unique.')
param customSubDomainName string = toLower(name)

@description('Public network access setting.')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Enabled'

@description('Tags to apply to the resource.')
param tags object = {}

@description('Name of the default Foundry project to create under the account.')
param projectName string = 'default'

@description('Display name for the Foundry project.')
param projectDisplayName string = 'Default project'

@description('Description for the Foundry project.')
param projectDescription string = 'Default project for the Case Assistant Agent.'

@description('''Model deployments to create under the Foundry account. Each item:
{
  name: string                  // deployment name used by clients
  model: { format, name, version }
  sku:   { name, capacity }     // capacity is in 1000s of TPM for OpenAI models
  raiPolicyName?: string        // defaults to 'Microsoft.DefaultV2'
  versionUpgradeOption?: string // 'OnceNewDefaultVersionAvailable' (default) | 'OnceCurrentVersionExpired' | 'NoAutoUpgrade'
}''')
param modelDeployments array = []

resource foundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    // Disable local (key-based) authentication. Only Entra ID (AAD) is allowed.
    disableLocalAuth: true
    customSubDomainName: customSubDomainName
    publicNetworkAccess: publicNetworkAccess
    allowProjectManagement: true
    networkAcls: {
      defaultAction: 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectDisplayName
    description: projectDescription
  }
}

// Model deployments are created at the account scope (not the project).
// They are visible to all projects under the account. @batchSize(1) serializes
// the deploys to avoid 409 conflicts that the Cognitive Services control plane
// returns on parallel deployment creates.
@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = [for d in modelDeployments: {
  parent: foundry
  name: d.name
  sku: d.sku
  properties: {
    model: d.model
    raiPolicyName: d.?raiPolicyName ?? 'Microsoft.DefaultV2'
    versionUpgradeOption: d.?versionUpgradeOption ?? 'OnceNewDefaultVersionAvailable'
  }
}]

@description('Resource ID of the Foundry account.')
output id string = foundry.id

@description('Name of the Foundry account.')
output name string = foundry.name

@description('Endpoint of the Foundry account.')
output endpoint string = foundry.properties.endpoint

@description('Principal ID of the system-assigned managed identity of the Foundry account.')
output principalId string = foundry.identity.principalId

@description('Resource ID of the default Foundry project.')
output projectId string = project.id

@description('Name of the default Foundry project.')
output projectName string = project.name

@description('Principal ID of the system-assigned managed identity of the Foundry project.')
output projectPrincipalId string = project.identity.principalId

@description('Names of the model deployments created under the Foundry account.')
output deploymentNames array = [for (d, i) in modelDeployments: deployments[i].name]
