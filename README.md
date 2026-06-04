---
title: Case Assistant Agent
description: Local development and setup guide for the Case Assistant Agent, including backend, frontends, ingestion pipeline, SharePoint auth flows, and supporting scripts.
ms.date: 2026-06-03
---

## Overview

Case Assistant Agent is an Azure-based, agentic RAG application built on **Foundry IQ** (Microsoft Foundry's agentic retrieval / knowledge base layer), with:

* A FastAPI backend for chat, ingestion orchestration, and pipeline status APIs
* A React simple chat frontend for local developer testing
* A Teams bot frontend for Microsoft 365 integration scenarios
* An Azure AI Search ingestion pipeline that processes blob content into a vector-enabled index
* A Foundry IQ knowledge base wired to the Azure AI Search index, surfaced to the Foundry agent via the native `azure_ai_search` tool
* A SharePoint delta-sync pipeline with scheduled queue-based execution
* A protocol-based SharePoint Graph adapter layer with swappable backends (`httpx` or Microsoft Graph SDK)
* A Service Bus queue worker that runs SharePoint sync jobs and conditionally triggers indexers
* Cosmos DB chat history storage with hierarchical partition keys
* A Foundry hosted agent (deployed via `scripts/deploy_agent.py`) that performs retrieval through Foundry IQ at runtime

## Repository Structure

* backend: FastAPI API, agents, workflows, ingestion services, tests
* frontend/simple-chat: React and Vite chat client
* frontend/teams-bot: Teams bot sample implementation
* infra: Bicep templates and modules for Azure resources
* scripts: RBAC and Cosmos helper scripts

## Prerequisites

* Python 3.12 or later
* Node.js 18 or later
* npm
* Azure CLI (`az`) authenticated for local Azure access
* Optional: Azure Developer CLI (`azd`) for provisioning and deployment workflows

## Quick Start

### 1) Clone and move into the repo

```powershell
git clone https://github.com/wxifaras/case-assistant-agent.git
Set-Location case-assistant-agent
```

### 2) Configure backend environment

Copy the example file and set required values.

```powershell
Copy-Item backend/.env.example backend/.env
```

At minimum, set values for:

* `SEARCHSERVICE_ENDPOINT`
* `SEARCHSERVICE_API_KEY` (if not using managed identity)
* `BLOBSTORAGE_RESOURCE_ID` or `BLOBSTORAGE_CONNECTION_STRING`
* `AZURE_OPENAI_ENDPOINT`
* `COSMOS_ENDPOINT` or `COSMOS_CONNECTION_STRING`
* `SERVICEBUS_QUEUE_NAME` plus either `SERVICEBUS_CONNECTION_STRING` or `SERVICEBUS_FQDN`

### 3) Create virtual environment and install backend dependencies

```powershell
py -3.14 -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -e "backend[dev]"
```

### 4) Start backend API

```powershell
Set-Location backend
../backend/.venv/Scripts/python -m uvicorn app.api.main:app --reload --reload-dir app --port 8000
```

API docs are available at `<http://localhost:8000/docs>`.

### 5) Start simple chat frontend

Open a second terminal:

```powershell
Set-Location frontend/simple-chat
npm install
npm run dev
```

The simple chat app runs on `<http://localhost:8081>`.

## Local Development Workflows

### Backend tests

```powershell
Set-Location backend
../backend/.venv/Scripts/python -m pytest tests -v
```

With coverage:

```powershell
Set-Location backend
../backend/.venv/Scripts/python -m pytest tests --cov=app --cov-report=html --cov-report=term
```

### API tests (httpyac)

```powershell
httpyac send backend/tests/api-tests.http --all
```

### Simple chat frontend commands

```powershell
Set-Location frontend/simple-chat
npm run dev
npm run build
npm run preview
```

### Teams bot frontend commands

```powershell
Set-Location frontend/teams-bot
npm install
npm run dev:teamsfx
npm run build
npm run lint
```

If local Teams bot config is missing:

```powershell
Set-Location frontend/teams-bot
if (-not (Test-Path .localConfigs)) { Copy-Item .localConfigs.sample .localConfigs }
```

## API Surface

Routes are mounted under `/api`.

* Health
  * `GET /api/health`
* Chat
  * Chat and conversation history endpoints under `/api/chat`
* Ingestion pipeline
  * `POST /api/pipeline/setup-pipeline`
  * `POST /api/pipeline/run-indexer`
  * `GET /api/pipeline/indexer-status`
* SharePoint sites and sync
  * `GET /api/sharepoint/sites`
  * `GET /api/sharepoint/sites/member-of`
  * `GET /api/sharepoint/sites/members`
  * `POST /api/sharepoint/sites/sync-site`
  * `POST /api/sharepoint/sites/sync`

### SharePoint routes reference

Site discovery and membership routes:

* `GET /api/sharepoint/sites`
  * Lists sites visible to the configured app identity
  * Query params: `search`, `max_results`, `include_libraries`
* `GET /api/sharepoint/sites/member-of`
  * Lists sites where a specific user is a member
  * Query params: `user_id`, `search`, `max_results`, optional `tenant_id`
* `GET /api/sharepoint/sites/members`
  * Lists members for a specific site
  * Query params: `site_hostname`, `site_path`, optional `tenant_id`

Sync routes:

* `POST /api/sharepoint/sites/sync-site`
  * Runs sync for one site request
* `POST /api/sharepoint/sites/sync`
  * Runs sync for multiple sites in one request

Both sync routes use the same auth dependency (`get_sync_graph_access_token`) and pass the resolved delegated token into the sync service when present.

Single-site sync payload example:

```json
{
  "site_hostname": "contoso.sharepoint.com",
  "site_path": "/sites/BainCaseAssistant",
  "library_name": "Documents",
  "folder_path": "Cases/2026",
  "destination_container": "case-assistant-documents",
  "tenant_id": "00000000-0000-0000-0000-000000000000"
}
```

Multi-site sync payload example:

```json
{
  "tenant_id": "00000000-0000-0000-0000-000000000000",
  "sites": [
    {
      "site_hostname": "contoso.sharepoint.com",
      "site_path": "/sites/BainCaseAssistant",
      "library_name": "Documents"
    },
    {
      "site_hostname": "contoso.sharepoint.com",
      "site_path": "/sites/Legal",
      "drive_id": "b!abc123xyz",
      "destination_container": "legal-documents"
    }
  ]
}
```

Sync payload notes:

* `site_hostname` and `site_path` can be omitted only when SharePoint defaults are configured in `backend/.env`
* Provide either `drive_id`, `library_name`, or a configured default `SHAREPOINT_LIBRARY_NAME`
* `tenant_id` can be omitted only when `AZURE_TENANT_ID` is configured

## Ingestion Pipeline

The ingestion system creates and runs three indexer paths:

* Multimodal indexer for binary documents (for example PDF and Office files)
* Markdown indexer for `.md`
* JSON indexer for `.json`

Core flow:

* Blob data source with change tracking and soft-delete detection
* Search index with vector and semantic configuration
* Skillsets for extraction, chunking, embeddings, and multimodal enrichment
* Indexers that project enriched content into the search index

## SharePoint Authentication and Permissions

The SharePoint sync pipeline supports three auth flows, controlled by two `.env` flags.

Flag behavior:

* `API_REQUIRE_JWT_VALIDATION`
  * `false`: No JWKS signature validation is performed on incoming bearer tokens.
  * `true`: Incoming bearer tokens are validated (issuer, audience, signature, and claims checks).
* `API_OBO_ENABLED`
  * `false`: Server does not perform On-Behalf-Of token exchange.
  * `true`: Server may exchange a validated user token for a Microsoft Graph delegated token.

Effective combinations:

* `false` + `false`:
  * Development passthrough mode.
  * Caller token can be forwarded to Graph without server-side signature validation.
* `true` + `false`:
  * Strict app-only mode.
  * Caller token is validated, then sync uses server identity (`DefaultAzureCredential`) for Graph.
* `true` + `true`:
  * Strict delegated mode.
  * Caller user token is validated and exchanged through OBO before Graph calls.
* `false` + `true`:
  * Behaves as passthrough in practice because OBO requires validated JWT input.

### Auth flow modes

| Mode | `API_REQUIRE_JWT_VALIDATION` | `API_OBO_ENABLED` | How Graph token is obtained |
|---|---|---|---|
| App-only (default) | `false` or `true` | any | `DefaultAzureCredential` on the server — no `Authorization` header needed from the caller |
| Delegated / OBO | `true` | `true` | Caller sends a user JWT; server exchanges it for a Graph token via On-Behalf-Of |
| Passthrough (dev) | `false` | any | Caller sends a raw bearer token; server forwards it to Graph without JWKS validation |

### Graph backend selection

SharePoint Graph calls are routed through a factory-selected adapter backend.

| Setting | Values | Default | Behavior |
|---|---|---|---|
| `SHAREPOINT_GRAPH_BACKEND` | `httpx`, `sdk` | `httpx` | Selects raw `httpx` Graph calls or Microsoft Graph SDK implementation |

Implementation files:

* `backend/app/ingestion/sharepoint/graph_adapter.py` (protocols)
* `backend/app/ingestion/sharepoint/httpx_graph_adapter.py`
* `backend/app/ingestion/sharepoint/msgraph_adapter.py`
* `backend/app/core/container_group_sharepoint.py` (factory wiring)

### Required `.env` settings

```ini
# Validate RS256 JWT signatures from callers (set false for passthrough / local dev)
API_REQUIRE_JWT_VALIDATION=false
# Exchange delegated user tokens for Graph tokens via OBO (requires JWT validation)
API_OBO_ENABLED=false
# Required when JWT validation is enabled
# API_AUTH_AUDIENCE=api://<your-client-id>
# Comma-separated list of allowed caller app client IDs
# API_ALLOWED_APP_CLIENT_IDS=<client-id-1>,<client-id-2>
```

### Microsoft Graph permissions required

The identity used by the server (managed identity or service principal) needs these **application** permissions with admin consent:

| Permission | Required for |
|---|---|
| `Sites.Read.All` | Site discovery (`GET /api/sharepoint/sites`) and resolving sync targets |
| `Files.Read.All` | Drive and file enumeration, file download during sync |
| `Group.Read.All` | Resolving Microsoft 365 groups connected to SharePoint sites |
| `GroupMember.Read.All` | Transitive group members and owners for membership endpoints |
| `User.Read.All` | Resolving user identity attributes (UPN, email) in membership responses |

For the **delegated / OBO flow**, the caller app registration also needs these **delegated** permissions consented by the user or an admin:

| Permission | Required for |
|---|---|
| `Sites.Read.All` | Delegated site and file access on behalf of the signed-in user |
| `Files.Read.All` | Delegated file enumeration and download |
| `Group.Read.All` | Delegated group membership lookups |

The resource app registration (the one `API_AUTH_AUDIENCE` points to) must expose an `access_as_user` OAuth 2.0 scope and optionally a `Sync.Site` app role for app-only callers. Both are created by `scripts/create_service_principal.py`.

### Testing auth flows locally

Use `backend/tests/test_auth_flows.py` to validate each mode against a running local server:

```powershell
# App-only (server uses DefaultAzureCredential — no token needed from caller)
& '.\backend\.venv\Scripts\python.exe' backend/tests/test_auth_flows.py --flow app-only

# Delegated / OBO (opens browser; requires API_REQUIRE_JWT_VALIDATION=true + API_OBO_ENABLED=true)
& '.\backend\.venv\Scripts\python.exe' backend/tests/test_auth_flows.py --flow delegated

# Passthrough (requires API_REQUIRE_JWT_VALIDATION=false; opens browser for token)
& '.\backend\.venv\Scripts\python.exe' backend/tests/test_auth_flows.py --flow passthrough

# Use Graph SDK backend while testing (optional)
$env:SHAREPOINT_GRAPH_BACKEND = "sdk"
& '.\backend\.venv\Scripts\python.exe' backend/tests/test_auth_flows.py --flow both
```

Override defaults without editing the script via CLI flags or environment variables:

```powershell
$env:TEST_SITE_URL = "https://contoso.sharepoint.com/sites/MySite"
& '.\backend\.venv\Scripts\python.exe' backend/tests/test_auth_flows.py --flow app-only

# Or fully via flags
& '.\backend\.venv\Scripts\python.exe' backend/tests/test_auth_flows.py `
    --flow delegated `
    --client-id <client-id> `
    --tenant-id <tenant-id> `
    --site-url https://contoso.sharepoint.com/sites/MySite
```

The `GET /api/health` endpoint reports the active auth settings:

```powershell
(Invoke-RestMethod 'http://localhost:8000/api/health').api |
    Select-Object require_jwt_validation, obo_enabled
```

## SharePoint Delta Sync and Scheduling

SharePoint sync now uses delta tracking and queue-based orchestration.

Core behavior:

* Detects per-file changes as `added`, `updated`, `unchanged`, and `deleted`
* Uploads only changed files to Blob Storage and updates sync state in Cosmos DB
* Schedules per-site sync requests through Service Bus
* Processes queued sync requests in the backend worker
* Runs search indexers only when sync detects changes

Indexer trigger policy in queue worker:

* Runs indexer when `added + updated + deleted > 0`
* Skips indexer when no changes are detected, including all-unchanged or zero-discovery runs

### Scheduler Function App

The scheduler Function App is in `backend/functions/sharepoint_sync_scheduler`.

It exposes:

* Timer trigger `ScheduleSharePointSync` using `SHAREPOINT_SYNC_SCHEDULE`
* HTTP trigger `POST /api/schedule/sharepoint-sync` for on-demand queueing

Required scheduler settings:

* `SHAREPOINT_SYNC_SCHEDULE`
* `SYNC_DEFAULT_TENANT_ID`
* `SYNC_API_BASE_URL`
* `SYNC_API_SITES_PATH`
* `SERVICEBUS_QUEUE_NAME`
* `SERVICEBUS_CONNECTION_STRING` or `SERVICEBUS_FQDN`

Run the scheduler locally:

```powershell
Set-Location backend/functions/sharepoint_sync_scheduler
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
func host start --port 7072
```

Trigger a manual scheduler run:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:7072/api/schedule/sharepoint-sync" -ContentType "application/json" -Body "{}"
```

## Foundry IQ Agent

The repository includes a Foundry hosted agent that uses **Foundry IQ** for retrieval. The agent is wired to an Azure AI Search index through the native `azure_ai_search` tool, which Foundry IQ resolves via the project connection created in `infra/modules/foundry-connections.bicep`.

* Agent YAML: `backend/app/agents/case_assistant_agent.yaml` (declares the `azure_ai_search` tool, target index, semantic/query config, and instructions)
* Deployment CLI: `scripts/deploy_agent.py`
* Project connection (Bicep): `infra/modules/foundry-connections.bicep` (resource name `aisearch`)

Set these environment variables in `backend/.env` (or your shell) before deploying:

* `FOUNDRY_PROJECT_ENDPOINT` (for example `https://<account>.services.ai.azure.com/api/projects/<project>`)
* `FOUNDRY_MODEL` (model deployment name used by the agent)

To enable runtime invocation from the backend, also set:

* `FOUNDRY_AGENT_ENABLED=true`
* `FOUNDRY_AGENT_NAME=<agent-name>`
* Optional: `FOUNDRY_AGENT_TIMEOUT_SECONDS=90`

Deploy or update the Foundry IQ agent:

```powershell
backend/.venv/Scripts/python scripts/deploy_agent.py --endpoint $env:FOUNDRY_PROJECT_ENDPOINT deploy
```

Deploy with explicit model override:

```powershell
backend/.venv/Scripts/python scripts/deploy_agent.py --endpoint $env:FOUNDRY_PROJECT_ENDPOINT deploy --model <model-deployment-name>
```

List agents in the project:

```powershell
backend/.venv/Scripts/python scripts/deploy_agent.py --endpoint $env:FOUNDRY_PROJECT_ENDPOINT list
```

Delete an agent by name:

```powershell
backend/.venv/Scripts/python scripts/deploy_agent.py --endpoint $env:FOUNDRY_PROJECT_ENDPOINT delete <agent-name>
```

When `FOUNDRY_PROJECT_ENDPOINT` is configured, API startup also attempts to configure Foundry tracing instrumentation.

## Configuration Model

The backend settings loader resolves values in this priority order:

1. Constructor overrides (tests)
2. Environment variables
3. Azure App Configuration (when `APP_CONFIG_ENDPOINT` is set)
4. `backend/.env`

Most local setups can start with `backend/.env` copied from `backend/.env.example`.

## Infrastructure and Provisioning

Infrastructure is defined under `infra/` and orchestrated by `azure.yaml`.

### AZD quick flow

```powershell
azd auth login
azd env new <env-name>
azd up
```

### AZD prerequisites

* Install Azure Developer CLI: <https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd>
* Sign in with both CLIs:

```powershell
az login
azd auth login
```

### AZD environment configuration

You can set optional environment values before provisioning:

```powershell
azd env set AZURE_BASE_NAME <base-name>
azd env set AZURE_RBAC_PRINCIPAL_ID <principal-object-id>
```

* `AZURE_BASE_NAME` controls resource naming prefix
* `AZURE_RBAC_PRINCIPAL_ID` grants RBAC to an additional managed identity or service principal

### AZD command reference

Provision infrastructure only:

```powershell
azd provision
```

Deploy application code to existing infrastructure:

```powershell
azd deploy
```

Provision and deploy in one command:

```powershell
azd up
```

Show environment values:

```powershell
azd env get-values
```

Remove deployed resources:

```powershell
azd down
```

Post-provision hooks run RBAC assignment scripts:

* `scripts/azd-postprovision-rbac.ps1` (Windows)
* `scripts/azd-postprovision-rbac.sh` (POSIX)

The post-provision hook runs automatically after `azd provision` and `azd up`.

## Utility Scripts

Scripts are in `scripts/`:

* `create_service_principal.py`
  * Creates or reuses an Entra app registration and service principal
  * Adds the `access_as_user` OAuth 2.0 delegated scope to the app registration
  * Adds the `Sync.Site` application role for app-only callers
  * Optional: creates a client secret for local service principal auth
  * Prints identifiers needed by RBAC setup (`app_id`, `service_principal_object_id`)
* `backend/tests/test_auth_flows.py`
  * End-to-end test script for all three SharePoint auth flow modes (app-only, delegated, passthrough)
  * Supports `--flow`, `--client-id`, `--tenant-id`, `--api-base`, `--site-url` flags
  * Falls back to `TEST_*` environment variables when flags are not supplied
* `setup_rbac.py`
  * Assigns Azure RBAC roles for local development and SharePoint sync dependencies
  * Can target the signed-in user or a specific principal via `--principal-id`
  * Optional: grants Microsoft Graph app permissions with `--grant-sharepoint-app-permissions`
  * Graph permission mapping:
    * `Sites.Read.All`: required for site discovery and site metadata reads (`GET /api/sharepoint/sites`, `GET /api/sharepoint/sites/members`) and for resolving sync site targets
    * `Files.Read.All`: required to enumerate drives/items and download SharePoint files during sync (`POST /api/sharepoint/sites/sync-site`, `POST /api/sharepoint/sites/sync`)
    * `Group.Read.All`: required to resolve connected Microsoft 365 groups from sites for membership lookups
    * `GroupMember.Read.All`: required to read transitive group members and owners for `member-of` and `members` endpoints
    * `User.Read.All`: required to read user identity attributes (for example id, UPN, email) used in membership matching and response shaping
* `setup_cosmos_rbac.py`
  * Creates Cosmos DB custom data-plane role and assignment
* `check_cosmos_rbac.py`
  * Verifies Cosmos RBAC setup

Recommended order for service principal setup:

1. Create or reuse the app registration and service principal

```powershell
python scripts/create_service_principal.py --name case-assistant-sharepoint-sync --create-secret
```

1. Assign Azure RBAC roles and optional Graph app permissions

```powershell
python scripts/setup_rbac.py --subscription <subscription-id> --resource-group <resource-group> --principal-id <service-principal-object-id> --principal-type ServicePrincipal --grant-sharepoint-app-permissions
```

1. Optionally ensure Cosmos DB custom data-plane role assignment

```powershell
python scripts/setup_cosmos_rbac.py --resource-group <resource-group> --account-name <cosmos-account-name> --principal-id <service-principal-object-id>
```

## Known Limitations

* Protected or encrypted Office documents can fail ingestion depending on protection mode and policy.
* SharePoint sync runs with `API_REQUIRE_JWT_VALIDATION=false` by default (app-only / passthrough mode). Set to `true` and configure `API_AUTH_AUDIENCE` to enforce JWT validation for delegated callers.
* SharePoint Graph backend defaults to `httpx`. Set `SHAREPOINT_GRAPH_BACKEND=sdk` to use the Microsoft Graph SDK adapter.
* The `docs/` folder is currently minimal. Most implementation guidance is in source and this README.

## Troubleshooting

* Backend fails to start:
  * Verify `backend/.env` exists and required endpoints and keys are populated.
  * Verify Azure login state with `az account show` when using managed identity or Azure CLI credentials.
* Frontend cannot call backend:
  * Confirm backend is running on port 8000.
  * Confirm simple-chat is running on port 8081.
* Indexer errors:
  * Call `GET /api/pipeline/indexer-status` and inspect `errors` and `warnings` fields in response data.

## License

See `LICENSE` for licensing details.
