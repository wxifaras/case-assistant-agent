#!/usr/bin/env sh
# Post-provision RBAC hook for `azd up` / `azd provision`.
# Reads bicep outputs from azd's environment and calls scripts/setup_rbac.py
# to assign the roles required for local development and for the Foundry
# project's managed identity (when AZURE_RBAC_PRINCIPAL_ID is set).

set -e

echo ""
echo "=== azd postprovision: RBAC setup ==="

if [ -z "${AZURE_RESOURCE_GROUP:-}" ]; then
    echo "ERROR: AZURE_RESOURCE_GROUP is not set. Did azd provision succeed?" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$SCRIPT_DIR/../backend/.venv/bin/python"
if [ -x "$VENV_PY" ]; then
    PYTHON="$VENV_PY"
else
    PYTHON="python3"
fi

SCRIPT_PATH="$SCRIPT_DIR/setup_rbac.py"

run_setup() {
    "$PYTHON" "$SCRIPT_PATH" \
        --subscription           "$AZURE_SUBSCRIPTION_ID" \
        --resource-group         "$AZURE_RESOURCE_GROUP" \
        --cosmos-account         "$COSMOSACCOUNTNAME" \
        --storage-account        "$STORAGEACCOUNTNAME" \
        --search-service         "$SEARCHNAME" \
        --ai-services-account    "$FOUNDRYNAME" \
        --ai-multiservice-account "$AISERVICESNAME" \
        --key-vault              "$KEYVAULTNAME" \
        --app-insights           "$APPINSIGHTSNAME" \
        "$@"
}

# 1. Signed-in user (required for local dev with DefaultAzureCredential).
echo ""
echo "--- Granting roles to signed-in user ---"
run_setup

# 2. Foundry project's managed identity.
if [ -n "${FOUNDRYPROJECTPRINCIPALID:-}" ]; then
    echo ""
    echo "--- Granting roles to Foundry project managed identity ---"
    run_setup --principal-id "$FOUNDRYPROJECTPRINCIPALID" --principal-name "Foundry project MI"
fi

# 3. Optional extra principal via azd env var.
if [ -n "${AZURE_RBAC_PRINCIPAL_ID:-}" ]; then
    NAME="${AZURE_RBAC_PRINCIPAL_NAME:-azd-configured principal}"
    echo ""
    echo "--- Granting roles to $AZURE_RBAC_PRINCIPAL_ID ---"
    run_setup --principal-id "$AZURE_RBAC_PRINCIPAL_ID" --principal-name "$NAME"
fi

echo ""
echo "=== RBAC setup complete ==="
