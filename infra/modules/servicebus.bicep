@description('Service Bus namespace name. 6-50 chars, letters/numbers/hyphens.')
@minLength(6)
@maxLength(50)
param namespaceName string

@description('Azure region for the Service Bus namespace.')
param location string

@description('Tags applied to Service Bus resources.')
param tags object = {}

@description('Service Bus namespace SKU tier.')
@allowed([
  'Standard'
])
param skuName string = 'Standard'

@description('Queue name used for SharePoint sync requests.')
@minLength(1)
@maxLength(260)
param queueName string

@description('Queue lock duration in ISO-8601 format.')
param queueLockDuration string = 'PT1M'

@description('Maximum delivery attempts before dead-lettering.')
@minValue(1)
@maxValue(10)
param queueMaxDeliveryCount int = 5

resource namespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: namespaceName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuName
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
    minimumTlsVersion: '1.2'
  }
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  name: queueName
  parent: namespace
  properties: {
    lockDuration: queueLockDuration
    maxDeliveryCount: queueMaxDeliveryCount
    deadLetteringOnMessageExpiration: true
  }
}

output id string = namespace.id
output name string = namespace.name
output fqdn string = '${namespace.name}.servicebus.windows.net'
output queueId string = queue.id
output queueName string = queue.name
