// Grants a principal (typically the Foundry project's managed identity) the
// roles required for the agent's Azure AI Search tool to read indexes and
// query documents.
//
// Roles assigned:
//   - Search Index Data Reader      → read documents from indexes
//   - Search Service Contributor    → read index schema / metadata (required
//                                     for the Foundry agent tool to enumerate
//                                     indexes and resolve field mappings)

@description('Name of the existing Azure AI Search service.')
param searchServiceName string

@description('Principal (object) ID to grant Search reader roles to.')
param principalId string

@description('Principal type. Use ServicePrincipal for managed identities.')
@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param principalType string = 'ServicePrincipal'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchServiceName
}

// Built-in role definition IDs (tenant-agnostic)
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource indexDataReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, principalId, searchIndexDataReaderRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
    principalId: principalId
    principalType: principalType
  }
}

resource serviceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, principalId, searchServiceContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalId: principalId
    principalType: principalType
  }
}
