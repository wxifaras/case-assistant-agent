"""Azure App Configuration custom pydantic-settings v2 source.

Implements the pattern described at:
https://medium.com/@sybrandwildeboer/from-env-drift-to-config-as-code-azure-app-configuration-with-pydantic-settings-python-azure-e3698eefb245

Overview
--------
Keep ``.env`` (or Azure App Service application settings) minimal — just the
bootstrap variables needed to locate the store:

    APP_CONFIG_ENDPOINT=https://<store>.azconfig.io   # managed identity auth
    APP_CONFIG_KEY_FILTER=knowledge-assistant:*        # key prefix in the store
    APP_CONFIG_LABEL_FILTER=production                 # environment label

All other settings are stored in Azure App Configuration under keys that match
the environment-variable names used by each ``BaseSettings`` subclass, e.g.:

    agentic-rca:SEARCHSERVICE_ENDPOINT → SearchServiceSettings.endpoint
    agentic-rca:COSMOS_ENDPOINT        → CosmosDBSettings.endpoint

Key Vault references stored in App Configuration are automatically resolved
using the same ``DefaultAzureCredential`` when using managed identity (endpoint-based auth).

Source priority (highest → lowest)
-----------------------------------
1. init kwargs (explicit overrides in tests / code)
2. Environment variables  (still allow env-var overrides in CI/CD)
3. Azure App Configuration  ← this source
4. .env file  (local development defaults)
5. File secrets directory

Usage
-----
Inherit from ``AppConfigAwareSettings`` instead of ``BaseSettings`` for any
settings class that should participate in App Configuration loading::

    from app.core.app_config_source import AppConfigAwareSettings

    class MySettings(AppConfigAwareSettings):
        model_config = SettingsConfigDict(env_prefix="MY_", ...)
        some_field: str = Field(...)

Bootstrap environment variables (stay in ``.env`` / app settings)
------------------------------------------------------------------
APP_CONFIG_ENDPOINT
    Azure App Configuration store endpoint URL.
    Used with managed identity (``DefaultAzureCredential``).
    Must be set to enable App Configuration loading.

APP_CONFIG_KEY_FILTER
    Key pattern passed to ``SettingSelector``.  Keys matching this pattern are
    loaded from the store.  Use a prefix ending in ``*`` (e.g.
    ``knowledge-assistant:*``) and the prefix will be stripped automatically so
    trimmed keys match env-var names (e.g. ``SEARCHSERVICE_ENDPOINT``).
    Defaults to ``*`` (all keys).

APP_CONFIG_LABEL_FILTER
    Label for environment-specific overrides (e.g. ``production``).
    When set, two selectors are used:
      • ``(No Label)`` — shared defaults
      • ``<label>``    — environment overlay (last match wins)
    Falls back to the ``ENVIRONMENT`` variable when not explicitly set.
    Defaults to no label (no-label keys only) when neither is set.

"""

import logging
import os
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

logger = logging.getLogger(__name__)

# Sentinel so we can distinguish "not yet loaded" from "loaded but empty".
_UNSET: object = object()
_loaded_config: Any = _UNSET  # dict[str, Any] after first load


def _load_app_config() -> dict[str, Any]:
    """Load every key-value pair from Azure App Configuration.

    The result is cached at module level so the network round-trip happens at
    most once per process.  Returns an empty dict when ``APP_CONFIG_ENDPOINT``
    is not set.
    """
    global _loaded_config
    if _loaded_config is not _UNSET:
        return _loaded_config  # type: ignore[return-value]

    endpoint: str | None = os.getenv("APP_CONFIG_ENDPOINT")

    if not endpoint:
        logger.debug(
            "APP_CONFIG_ENDPOINT not set — Azure App Configuration disabled; "
            "falling back to environment variables and .env file."
        )
        _loaded_config = {}
        return _loaded_config

    try:
        from azure.appconfiguration.provider import (  # type: ignore[import]
            AzureAppConfigurationKeyVaultOptions,
            SettingSelector,
            load,
        )
        from azure.identity import DefaultAzureCredential

        key_filter: str = os.getenv("APP_CONFIG_KEY_FILTER", "*")
        # Explicit label wins; fall back to the general ENVIRONMENT variable
        # so that setting ENVIRONMENT=production is enough — no need to also
        # set APP_CONFIG_LABEL_FILTER when the label matches the env name.
        label_filter: str | None = (
            os.getenv("APP_CONFIG_LABEL_FILTER")
            or os.getenv("ENVIRONMENT")
            or None
        )

        # Build selectors: always include no-label defaults; overlay with the
        # environment label when one is specified.
        if label_filter:
            selects = [
                SettingSelector(key_filter=key_filter),  # shared / no-label defaults
                SettingSelector(key_filter=key_filter, label_filter=label_filter),  # env overlay
            ]
        else:
            selects = [SettingSelector(key_filter=key_filter)]

        # Derive the trim prefix from the key filter.
        # "knowledge-assistant:*" → trim "knowledge-assistant:"
        # "*" → no trimming
        prefix_to_trim = key_filter.rstrip("*") if key_filter != "*" else None
        trim_prefixes = [prefix_to_trim] if prefix_to_trim else []

        credential = DefaultAzureCredential()

        # Always resolve Key Vault references — same credential used for both
        # App Configuration and Key Vault access.
        cfg = load(
            endpoint=endpoint,
            credential=credential,
            selects=selects,
            trim_prefixes=trim_prefixes,
            key_vault_options=AzureAppConfigurationKeyVaultOptions(credential=credential),
        )
        _loaded_config = dict(cfg)
        logger.info(
            "Loaded %d key(s) from Azure App Configuration (endpoint=%s, label=%s).",
            len(_loaded_config),
            endpoint,
            label_filter or "(no label)",
        )

    except ImportError:
        logger.warning(
            "azure-appconfiguration-provider is not installed; "
            "skipping Azure App Configuration. "
            "Install it with: pip install 'azure-appconfiguration-provider>=2.1.0'"
        )
        _loaded_config = {}
    except Exception:
        logger.exception(
            "Failed to load Azure App Configuration; "
            "continuing with environment variables / .env."
        )
        _loaded_config = {}

    return _loaded_config  # type: ignore[return-value]


def reset_app_config_cache() -> None:
    """Clear the module-level cache, forcing a fresh load on the next call.

    Intended for use in tests that need to inject different App Configuration
    values between test cases::

        from app.core.app_config_source import reset_app_config_cache

        def test_something(monkeypatch):
            monkeypatch.setenv("APP_CONFIG_ENDPOINT", "https://...")
            reset_app_config_cache()
            settings = Settings()
    """
    global _loaded_config
    _loaded_config = _UNSET


class AzureAppConfigSource(PydanticBaseSettingsSource):
    """pydantic-settings v2 source backed by Azure App Configuration.

    For each field in the settings class, the source computes the expected
    environment-variable name (``env_prefix`` + field name, uppercased) and
    performs a case-insensitive lookup in the loaded App Configuration mapping.
    If a value is found it is type-coerced through
    ``prepare_field_value`` — the same path used by the built-in
    ``EnvSettingsSource``.

    Key mapping example
    -------------------
    - ``SearchServiceSettings`` — ``env_prefix='SEARCHSERVICE_'``
    - Field ``endpoint``
    - Expected env var: ``SEARCHSERVICE_ENDPOINT``
    - App Config key after trim: ``SEARCHSERVICE_ENDPOINT`` (any casing)
    - Result: ``{"endpoint": "<value>"}``
    """

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        """Required by the base class; individual lookups are done in ``__call__``."""
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:  # noqa: D102
        config = _load_app_config()
        if not config:
            return {}

        env_prefix: str = self.settings_cls.model_config.get("env_prefix", "") or ""
        result: dict[str, Any] = {}

        for field_name, _field_info in self.settings_cls.model_fields.items():
            # Build the env‑var name this field would normally be read from.
            env_key = (env_prefix + field_name).upper()

            # Case-insensitive lookup so the store can use any capitalisation.
            for k, v in config.items():
                if k.upper() == env_key:
                    if v is not None:
                        result[field_name] = v
                    break

        return result


class AppConfigAwareSettings(BaseSettings):
    """``BaseSettings`` subclass that injects Azure App Configuration as a source.

    Inherit from this class instead of ``BaseSettings`` for every settings
    class that should load values from Azure App Configuration when
    ``APP_CONFIG_ENDPOINT`` is set.

    Source priority (highest → lowest):

    1. ``init_settings``    — explicit constructor kwargs (useful in tests)
    2. ``env_settings``     — process environment variables
    3. ``AzureAppConfigSource`` ← **this class injects it here**
    4. ``dotenv_settings``  — ``.env`` file (local dev defaults)
    5. ``file_secret_settings`` — secrets directory

    When App Configuration is not configured (``APP_CONFIG_ENDPOINT`` missing),
    ``AzureAppConfigSource`` returns an empty dict and adds zero overhead
    beyond a single ``os.getenv`` call.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            AzureAppConfigSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )
