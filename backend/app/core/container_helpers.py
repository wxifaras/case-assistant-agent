"""Factory helpers for dependency-injector providers.

This module keeps construction logic separate from provider wiring so
`Container` definitions remain easier to scan and maintain.
"""

from azure.core.credentials import AzureKeyCredential
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient, SearchIndexerClient

from app.models import CosmosDBOptions, SearchServiceOptions


def make_search_credential(options: SearchServiceOptions) -> AzureKeyCredential | DefaultAzureCredential:
    """Return the appropriate credential for Azure AI Search."""
    return AzureKeyCredential(options.api_key) if options.api_key else DefaultAzureCredential()


def create_cosmos_client(options: CosmosDBOptions) -> CosmosClient:
    """Create a Cosmos client from a connection string or endpoint."""
    if options.connection_string:
        return CosmosClient.from_connection_string(options.connection_string)
    if options.endpoint:
        return CosmosClient(options.endpoint, credential=DefaultAzureCredential())
    raise ValueError("CosmosDBOptions must include either connection_string or endpoint.")


def create_search_index_client(options: SearchServiceOptions) -> SearchIndexClient:
    """Create a SearchIndexClient using the configured credential path."""
    return SearchIndexClient(endpoint=options.endpoint, credential=make_search_credential(options))


def create_search_indexer_client(options: SearchServiceOptions) -> SearchIndexerClient:
    """Create a SearchIndexerClient using the configured credential path."""
    return SearchIndexerClient(endpoint=options.endpoint, credential=make_search_credential(options))


def create_search_client(options: SearchServiceOptions) -> SearchClient:
    """Create a SearchClient for querying the configured index."""
    return SearchClient(
        endpoint=options.endpoint,
        index_name=options.index_name,
        credential=make_search_credential(options),
    )
