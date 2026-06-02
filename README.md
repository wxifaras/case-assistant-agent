---
title: Case Assistant Agent
description: Local development and setup guide for the Case Assistant Agent, including backend, frontends, ingestion pipeline, and supporting scripts.
---

## Overview

Case Assistant Agent is an Azure-based, agentic RAG application built on **Foundry IQ** (Microsoft Foundry's agentic retrieval / knowledge base layer), with:

* A FastAPI backend for chat, ingestion orchestration, and pipeline status APIs
* A React simple chat frontend for local developer testing
* A Teams bot frontend for Microsoft 365 integration scenarios
* An Azure AI Search ingestion pipeline that processes blob content into a vector-enabled index
* A Foundry IQ knowledge base wired to the Azure AI Search index, surfaced to the Foundry agent via the native `azure_ai_search` tool
* A SharePoint delta-sync pipeline with scheduled queue-based execution
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

* `setup_rbac.py`: assigns required Azure RBAC roles for dev and managed identities
* `setup_cosmos_rbac.py`: sets up Cosmos data-plane custom role assignments
* `check_cosmos_rbac.py`: verifies Cosmos RBAC setup

## Known Limitations

* Protected or encrypted Office documents can fail ingestion depending on protection mode and policy.
* Authentication is configurable, but local defaults may run with API auth disabled unless enabled in configuration.
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
