# Hermes Development Guide

A practical, hands-on reference for modifying the Hermes codebase. This guide
complements `AGENTS.md` (contribution philosophy) with **how things actually
work** — the data flows, pitfalls, and patterns you need to know to make
changes without breaking things.

**Audience:** Maintainers, contributors, and AI assistants working on this
codebase for the first time.

---

## Table of Contents

1. [Project Layout](#1-project-layout)
2. [The Profile System](#2-the-profile-system)
3. [Configuration System](#3-configuration-system)
4. [Provider Architecture](#4-provider-architecture)
5. [Model Picker Pipeline](#5-model-picker-pipeline)
6. [Adding a New Provider](#6-adding-a-new-provider)
7. [Desktop & Web Dashboard UI](#7-desktop--web-dashboard-ui)
8. [Gateway & Messaging](#8-gateway--messaging)
9. [Tools & Toolsets](#9-tools--toolsets)
10. [Plugins](#10-plugins)
11. [Common Pitfalls](#11-common-pitfalls)
12. [Debugging Tips](#12-debugging-tips)
13. [Lessons from Real Changes](#13-lessons-from-real-changes)

---

## 1. Project Layout

```
hermes-agent/
├── run_agent.py          # AIAgent class — core conversation loop (~5.5k LOC)
├── cli.py                # HermesCLI class — interactive CLI (~16k LOC)
├── model_tools.py        # Tool orchestration, discover_builtin_tools()
├── toolsets.py           # Toolset definitions
├── hermes_constants.py   # get_hermes_home(), profile-aware paths
├── hermes_state.py       # SessionDB — SQLite session store
│
├── agent/                # Core agent runtime (120 files)
│   ├── transports/       # LLM API transports (chat_completions, anthropic, etc.)
│   ├── context_engine.py # Context management
│   ├── context_compressor.py
│   ├── memory_manager.py
│   ├── system_prompt.py
│   ├── skill_commands.py
│   └── ...
│
├── providers/            # Provider registry + base class
│   ├── __init__.py       # register_provider(), get_provider_profile()
│   └── base.py           # ProviderProfile dataclass
│
├── plugins/              # Plugin ecosystem
│   ├── model-providers/  # One dir per LLM provider (openrouter, anthropic, ...)
│   ├── memory/           # Memory backends (honcho, mem0, ...)
│   ├── platforms/        # Messaging platform adapters
│   ├── kanban/           # Multi-agent work queue
│   ├── image_gen/        # Image generation providers
│   └── ...
│
├── hermes_cli/           # CLI framework
│   ├── config.py         # DEFAULT_CONFIG, load_config(), OPTIONAL_ENV_VARS
│   ├── auth.py           # PROVIDER_REGISTRY, credential resolution
│   ├── models.py         # CANONICAL_PROVIDERS, model fetching, OPENROUTER_MODELS
│   ├── model_switch.py   # list_authenticated_providers() — the picker pipeline
│   ├── provider_catalog.py  # Unified provider catalog for GUI
│   ├── inventory.py      # build_models_payload() — API endpoint data
│   ├── web_server.py     # FastAPI server for dashboard + desktop
│   └── ...
│
├── gateway/              # Messaging gateway
│   ├── run.py            # Main gateway (~22k LOC)
│   ├── platforms/        # Per-platform adapters (telegram, discord, slack, ...)
│   └── ...
│
├── tools/                # Tool implementations (97 files)
│   ├── registry.py       # Tool registration
│   ├── file_tools.py, terminal_tool.py, browser_tool.py, ...
│   └── ...
│
├── apps/desktop/         # Electron desktop app (TypeScript/React)
│   └── src/
│       ├── app/settings/ # Settings UI (constants.ts, providers-settings.tsx)
│       ├── components/   # Shared components
│       └── lib/          # Utilities (model-status-label.ts, etc.)
│
├── web/                  # Web dashboard (TypeScript/React)
│   └── src/
│       ├── pages/        # EnvPage.tsx, ModelsPage.tsx, ConfigPage.tsx
│       └── components/   # ModelPickerDialog, OAuthProvidersCard, etc.
│
├── tui_gateway/          # Python JSON-RPC backend for TUI
├── ui-tui/               # Ink (React) terminal UI
│
├── config.yaml           # Main config (HERMES_HOME/config.yaml)
├── .env                  # API keys (HERMES_HOME/.env)
└── profiles/             # Per-profile isolated instances
```

### Key Entry Points

| File | Role | LOC |
|------|------|-----|
| `run_agent.py` | `AIAgent` class — the conversation loop | ~5,500 |
| `cli.py` | `HermesCLI` — interactive CLI orchestrator | ~16,000 |
| `gateway/run.py` | Gateway server — messaging platform routing | ~22,000 |
| `hermes_cli/web_server.py` | FastAPI server for dashboard + desktop | ~18,000 |

---

## 2. The Profile System

Hermes supports **profiles** — multiple isolated instances, each with its own
`HERMES_HOME` directory containing independent config, API keys, memory,
sessions, and skills.

### How Profiles Work

```
G:\Hermes\                          # Root HERMES_HOME (default profile)
├── config.yaml                     # Root config
├── .env                            # Root API keys
└── profiles/
    ├── fast_coder/                 # Profile: fast_coder
    │   ├── config.yaml             # Profile-specific config
    │   └── .env                    # Profile-specific API keys
    ├── researcher/
    │   ├── config.yaml
    │   └── .env
    └── ...
```

### Critical: Profile Isolation

**Every profile has its own `.env` file.** The active profile's `.env` is
what gets loaded, NOT the root `.env`. This is the #1 source of "my API key
is set but the provider doesn't show up" bugs.

```python
# CORRECT — always use get_hermes_home()
from hermes_constants import get_hermes_home
env_path = get_hermes_home() / '.env'

# WRONG — breaks profiles
env_path = Path.home() / '.hermes' / '.env'
```

When adding API keys for a new provider, you MUST add them to:
1. The root `.env` (G:\Hermes\.env)
2. **Every profile's `.env`** (profiles/*/\ .env)

### Profile Activation

Profiles are activated via:
- CLI flag: `hermes -p fast_coder`
- Environment variable: `HERMES_HOME=G:\Hermes\profiles\fast_coder`
- Config: `active_profile` file

---

## 3. Configuration System

### Three Config Layers

| Layer | File | Purpose |
|-------|------|---------|
| Config | `config.yaml` | Behavioral settings (provider, model, timeouts, features) |
| Secrets | `.env` | API keys, tokens, passwords ONLY |
| Auth Store | `auth.json` | OAuth tokens, credential pool |

**Rule:** Non-secret settings (timeouts, thresholds, feature flags) go in
`config.yaml`. Secrets go in `.env`. Never put API keys in `config.yaml`.

### Config Loading Paths

| Loader | Used by | Location |
|--------|---------|----------|
| `load_cli_config()` | CLI mode | `cli.py` |
| `load_config()` | `hermes tools`, `hermes setup` | `hermes_cli/config.py` |
| Direct YAML load | Gateway runtime | `gateway/run.py` + `gateway/config.py` |

### Adding Config Keys

1. Add to `DEFAULT_CONFIG` in `hermes_cli/config.py`
2. Bump `_config_version` ONLY if you need to actively migrate existing user
   config (renaming keys, changing structure). New keys in existing sections
   are auto-merged.

### Adding API Keys (`.env`)

1. Add to `OPTIONAL_ENV_VARS` in `hermes_cli/config.py` with metadata:
```python
"NEW_API_KEY": {
    "description": "What it's for",
    "prompt": "Display name",
    "url": "https://...",
    "password": True,
    "category": "provider",  # provider, tool, messaging, setting
},
```

Or, if the provider has a plugin with `env_vars=("NEW_API_KEY",)`, the
`_inject_profile_env_vars()` function auto-adds it to `OPTIONAL_ENV_VARS`
at import time.

---

## 4. Provider Architecture

### How Providers Are Registered

Providers are plugins under `plugins/model-providers/<name>/`. Each plugin:

1. Has `__init__.py` that calls `register_provider(ProfileInstance)`
2. Has `plugin.yaml` manifest (`kind: model-provider`)
3. Is discovered lazily by `providers/__init__.py._discover_providers()`

```python
# plugins/model-providers/myprovider/__init__.py
from providers import register_provider
from providers.base import ProviderProfile

myprovider = ProviderProfile(
    name="myprovider",
    env_vars=("MYPROVIDER_API_KEY",),
    display_name="My Provider",
    base_url="https://api.myprovider.com/v1",
    fallback_models=("model-a", "model-b"),
)
register_provider(myprovider)
```

### ProviderProfile Key Fields

```python
@dataclass
class ProviderProfile:
    name: str                           # Canonical slug (e.g. "openrouter")
    aliases: tuple = ()                 # Alternative names
    env_vars: tuple = ()                # API key env var names
    base_url: str = ""                  # Inference endpoint
    models_url: str = ""                # Models catalog endpoint (if different)
    auth_type: str = "api_key"          # api_key | oauth_device_code | ...
    display_name: str = ""              # Human-readable name
    description: str = ""
    signup_url: str = ""
    fallback_models: tuple = ()         # Static model list (fallback)
    supports_vision: bool = False       # Accepts image content in messages
    default_max_tokens: int | None = None
    # Hooks (override in subclass):
    #   prepare_messages(), build_extra_body(), build_api_kwargs_extras(),
    #   fetch_models(), get_max_tokens(), default_vision_model()
```

### Discovery Order

1. Bundled plugins: `plugins/model-providers/<name>/`
2. User plugins: `$HERMES_HOME/plugins/model-providers/<name>/`
3. Legacy single-file: `providers/<name>.py` (back-compat)

User plugins override bundled ones (last-writer-wins).

### Auto-Extension into CANONICAL_PROVIDERS

Provider plugins are auto-added to `CANONICAL_PROVIDERS` in
`hermes_cli/models.py:1093-1110` if they:
- Have `auth_type="api_key"` (non-OAuth)
- Are not already in the static list

This means adding a plugin directory is **sufficient** to expose a new
provider in the model picker — no edits to `models.py` needed.

---

## 5. Model Picker Pipeline

The model picker is a multi-step pipeline. Understanding it is critical for
debugging "my model doesn't show up" issues.

### Flow

```
User opens /model or hermes model
  │
  ▼
list_authenticated_providers()          # model_switch.py:1471
  │
  ├─ For each CANONICAL_PROVIDERS entry:
  │   ├─ Check: is_runtime_provider_routable(slug)?
  │   ├─ Check: has_creds? (env var set or auth store)
  │   └─ Get models: cached_provider_model_ids(slug)
  │       │
  │       ├─ OpenRouter: fetch_openrouter_models()
  │       │   ├─ Curated list (~37 models)
  │       │   ├─ Live /api/v1/models cross-check
  │       │   └─ + Free models from live catalog (NEW)
  │       │
  │       ├─ Special providers (codex, nous, anthropic, etc.)
  │       │   └─ Dedicated live fetch paths
  │       │
  │       └─ Generic providers (rewind, deepseek, etc.)
  │           ├─ profile.fetch_models() — live API
  │           └─ Merge with fallback_models or _PROVIDER_MODELS
  │
  ├─ Filter: explicit_only? (hides unconfigured)
  ├─ Deduplicate: aggregator overlap removal
  └─ Sort: canonical order or priority
```

### Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `list_authenticated_providers()` | `model_switch.py:1471` | Main picker pipeline |
| `cached_provider_model_ids()` | `models.py:2783` | Disk-cached model list |
| `provider_model_ids()` | `models.py:2412` | Live model fetch per provider |
| `fetch_openrouter_models()` | `models.py:1433` | OpenRouter-specific fetch |
| `build_models_payload()` | `inventory.py:111` | API endpoint response builder |

### Why Models Don't Show Up

Common reasons, in order of likelihood:

1. **API key not in the active profile's `.env`** — The most common issue.
   Each profile has its own `.env`. Check `get_hermes_home() / '.env'`.

2. **Stale disk cache** — `provider_models_cache.json` caches model lists
   for 1 hour. After changing provider code, clear with:
   ```python
   from hermes_cli.models import clear_provider_models_cache
   clear_provider_models_cache('provider_name')
   ```

3. **Provider not routable** — `is_runtime_provider_routable()` fails if
   `resolve_provider()` throws `AuthError`. Check the provider is registered.

4. **No credentials detected** — `has_creds` check at `model_switch.py:1719`
   requires `os.environ.get(ev)` to return a truthy value.

5. **Tool-calling filter** — Models without `"tools"` in `supported_parameters`
   are filtered out (for OpenRouter and similar aggregators).

---

## 6. Adding a New Provider (Step-by-Step)

### Step 1: Create the Plugin Directory

```
plugins/model-providers/myprovider/
├── __init__.py
└── plugin.yaml
```

### Step 2: Write `__init__.py`

```python
from providers import register_provider
from providers.base import ProviderProfile

myprovider = ProviderProfile(
    name="myprovider",
    env_vars=("MYPROVIDER_API_KEY",),
    display_name="My Provider",
    description="My Provider — description",
    signup_url="https://...",
    base_url="https://api.myprovider.com/v1",
    fallback_models=("model-a", "model-b"),
    supports_vision=True,  # if the API accepts images
)
register_provider(myprovider)
```

For non-standard API response formats, override `fetch_models()`:

```python
class MyProviderProfile(ProviderProfile):
    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
        # Custom fetch logic
        # Return list[str] of model IDs, or None on failure
        ...
```

### Step 3: Write `plugin.yaml`

```yaml
name: myprovider-provider
kind: model-provider
version: 1.0.0
description: My Provider
author: Your Name
```

### Step 4: Add API Key to `.env`

Add to **every profile's `.env`** AND the root `.env`:

```
MYPROVIDER_API_KEY=sk-...
```

### Step 5: Add to HERMES_OVERLAYS (Required for model switch)

**This step is mandatory.** The provider plugin registers for catalog and
auth, but the model switch uses a separate `HERMES_OVERLAYS` dict in
`hermes_cli/providers.py`. Without an overlay entry, `resolve_provider_full()`
returns None and model switching fails with "Unknown provider".

```python
# hermes_cli/providers.py — add to HERMES_OVERLAYS dict
"myprovider": HermesOverlay(
    transport="openai_chat",          # or "anthropic_messages", "codex_responses"
    extra_env_vars=("MYPROVIDER_API_KEY",),
    base_url_override="https://api.myprovider.com/v1",
    base_url_env_var="MYPROVIDER_BASE_URL",  # optional: env var for base URL override
),
```

### Step 6: Add to UI Hardcoded Lists (if needed)

For the provider to show in the Settings/Env pages with proper metadata:

**Desktop** — `apps/desktop/src/app/settings/constants.ts`:
```typescript
{
  prefix: 'MYPROVIDER_',
  name: 'My Provider',
  description: 'Description here',
  docsUrl: 'https://...',
  priority: N  // Higher = later in list
}
```

**Web Dashboard** — `web/src/pages/EnvPage.tsx`:
```typescript
{ prefix: "MYPROVIDER_", name: "My Provider", priority: N },
```

### Step 7: Clear Cache and Test

```python
from hermes_cli.models import clear_provider_models_cache
clear_provider_models_cache('myprovider')
```

Restart the app. The provider should appear in the model picker.

---

## 7. Desktop & Web Dashboard UI

### Architecture

```
Desktop (Electron)                    Web Dashboard
├── apps/desktop/src/                 ├── web/src/
│   ├── app/settings/                 │   ├── pages/
│   │   ├── constants.ts              │   │   ├── EnvPage.tsx
│   │   ├── providers-settings.tsx    │   │   ├── ModelsPage.tsx
│   │   └── helpers.ts                │   │   └── ConfigPage.tsx
│   ├── components/onboarding/        │   └── components/
│   │   ├── providers.tsx             │       ├── ModelPickerDialog.tsx
│   │   └── index.tsx                 │       └── OAuthProvidersCard.tsx
│   └── lib/                          │
│       └── model-status-label.ts     │
└── talks to backend via JSON-RPC     └── talks to backend via REST API
```

### Backend API Endpoints

| Endpoint | Serves | Used by |
|----------|--------|---------|
| `GET /api/model/options` | Provider + model lists | Model picker (both) |
| `GET /api/env` | Env vars with provider metadata | Settings → Keys tab |
| `GET /api/providers/oauth` | OAuth provider status | Settings → Accounts |
| `GET /api/model/set` | Set current model/provider | Model picker selection |

### Provider Display in UI

Provider names, descriptions, and docs URLs come from **two sources**:

1. **Dynamic** — `provider_catalog.py` derives metadata from
   `CANONICAL_PROVIDERS` + `ProviderProfile`. This is the authoritative source.

2. **Presentation overlay** — Hardcoded `PROVIDER_GROUPS` in:
   - `apps/desktop/src/app/settings/constants.ts` (Desktop Keys tab)
   - `web/src/pages/EnvPage.tsx` (Web Env page)
   - `hermes_cli/models.py:1139-1147` (CLI/TUI model picker grouping)

The overlay adds sort priority, descriptions, and docs URLs. If a provider
isn't in the overlay, it still appears but with generic display info from the
backend catalog.

### Model Name Display

`apps/desktop/src/lib/model-status-label.ts` handles model name prettification:
- `modelBaseId()` strips vendor prefix: `anthropic/claude-sonnet-4` → `claude-sonnet-4`
- `prettifyBase()` applies formatting: `claude-sonnet-4` → "Claude Sonnet 4"
- `modelDisplayParts()` splits into name + variant tag (Fast, Thinking, Preview)

---

## 8. Gateway & Messaging

### Gateway Entry Point

`gateway/run.py` (~22k LOC) is the main gateway. It:
- Routes messages from 20+ platforms to the agent
- Manages sessions, slash commands, and approvals
- Handles streaming, delivery, and error recovery

### Platform Adapters

Each platform has an adapter in `gateway/platforms/`:
- `telegram.py`, `discord.py`, `slack.py`, `whatsapp.py`, `signal.py`, etc.
- Each implements `connect()`, `send()`, `disconnect()`
- Use `acquire_scoped_lock()` / `release_scoped_lock()` to prevent
  credential conflicts across profiles

### Message Flow

```
Platform (Telegram, Discord, etc.)
  │
  ▼
Platform Adapter (gateway/platforms/*.py)
  │
  ▼
Gateway Runner (gateway/run.py)
  ├─ Slash command dispatch
  ├─ Approval/control commands
  └─ Agent session
      │
      ▼
    AIAgent (run_agent.py)
      ├─ System prompt construction
      ├─ Tool calls → model_tools.py
      └─ LLM API call → agent/transports/
```

---

## 9. Tools & Toolsets

### Tool Registration

Tools register via `tools/registry.py`:

```python
from tools.registry import registry

def my_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True})

registry.register(
    name="my_tool",
    toolset="my_toolset",
    schema={"name": "my_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: my_tool(param=args.get("param", "")),
    check_fn=lambda: bool(os.getenv("MY_API_KEY")),
    requires_env=["MY_API_KEY"],
)
```

### Toolset Wiring

Tools are grouped into toolsets in `toolsets.py`. Each platform picks a
base toolset. `_HERMES_CORE_TOOLS` is the default bundle.

Tools only appear in the agent's tool list if their name is in an enabled
toolset. Adding a tool without wiring it into a toolset = invisible.

### Tool Discovery

Any `tools/*.py` file with a top-level `registry.register()` call is
imported automatically by `model_tools.py`. No manual import list needed.
But the tool must still be in a toolset to be exposed to the agent.

---

## 10. Plugins

### Plugin Types

| Type | Location | Discovery |
|------|----------|-----------|
| Model providers | `plugins/model-providers/<name>/` | Lazy (providers module) |
| Memory backends | `plugins/memory/<name>/` | Memory manager |
| Platform adapters | `plugins/platforms/<name>/` | Gateway |
| General plugins | `plugins/<name>/` | PluginManager |
| Context engines | `plugins/context_engine/<name>/` | Context engine |

### Plugin Manifest

```yaml
# plugin.yaml
name: my-plugin
version: 1.0.0
description: What it does
kind: model-provider  # optional — auto-detected for provider plugins
```

### Plugin Registration

```python
# __init__.py
def register(ctx):
    # ctx.register_tool(...)
    # ctx.register_cli_command(...)
    # Hooks: pre_tool_call, post_tool_call, pre_llm_call, etc.
    pass
```

### User Plugins

User plugins live in `$HERMES_HOME/plugins/<name>/` and override bundled
plugins of the same name.

---

## 11. Common Pitfalls

### 1. Profile Isolation — The #1 Bug Source

**Symptom:** "I set the API key but the provider doesn't show up."

**Cause:** Each profile has its own `.env`. The active profile's `.env` is
loaded, not the root one.

**Fix:** Add the key to `profiles/<active_profile>/.env`.

```python
# Always verify which .env is being loaded:
from hermes_constants import get_hermes_home
print(get_hermes_home() / '.env')  # This is the file that matters
```

### 2. Stale Disk Cache

**Symptom:** "I changed the provider code but the models didn't update."

**Cause:** `provider_models_cache.json` caches model lists for 1 hour.

**Fix:**
```python
from hermes_cli.models import clear_provider_models_cache
clear_provider_models_cache('provider_name')  # or None to clear all
```

### 3. Response Format Mismatch

**Symptom:** "fetch_models() returns empty list."

**Cause:** The provider's API returns a different JSON structure than expected.
Base class looks for `data.get("data", [])` but some providers use
`data.get("models", [])` or other keys.

**Fix:** Override `fetch_models()` in the provider profile to handle the
actual response format.

### 4. WAF / User-Agent Blocking

**Symptom:** "HTTP 403 Forbidden" from the models endpoint.

**Cause:** Some providers' WAFs block default Python `urllib` User-Agent.

**Fix:** Set `User-Agent: hermes-cli` header. The base `fetch_models()`
already does this, but custom overrides must include it.

### 5. HERMES_OVERLAYS Missing (Model Switch Fails)

**Symptom:** "Unknown provider 'myprovider'" when trying to switch models.

**Cause:** The provider plugin registers for catalog/auth via
`providers.register_provider()`, but the model switch uses a separate
`HERMES_OVERLAYS` dict in `hermes_cli/providers.py`. Without an overlay
entry, `resolve_provider_full()` returns None.

**Fix:** Add a `HermesOverlay` entry to `HERMES_OVERLAYS` in
`hermes_cli/providers.py`. Every new provider needs BOTH a plugin AND
an overlay entry.

### 6. CANONICAL_PROVIDERS Auto-Extension

**Symptom:** "My provider plugin exists but doesn't appear in the picker."

**Cause:** The auto-extension at `models.py:1093-1110` only adds providers
with `auth_type="api_key"`. OAuth providers need manual addition.

**Fix:** For OAuth providers, add a `ProviderEntry` to `CANONICAL_PROVIDERS`
in `models.py`.

### 7. OpenRouter Live Fetch 403

**Symptom:** "OpenRouter only shows curated models, not all free ones."

**Cause:** The OpenRouter API returns 403 from certain networks (WAF).
The fetch fails silently and falls back to the curated cache.

**Fix:** Network issue. The code falls back gracefully. With VPN or
different network, the live fetch succeeds and free models appear.

### 8. probe_api_models Response Format (Model Switch Fails)

**Symptom:** "Model `x` was not found in this provider's model listing"
even though the model exists in the picker.

**Cause:** `probe_api_models()` in `models.py` fetches the live model
list to validate model switches. It only checked `data.get("data", [])`
but some providers (Rewind) return `{"models": [...]}` instead. The
list comes back empty and every model is rejected.

**Fix:** Check both `"data"` and `"models"` keys:
```python
items = data.get("data") or data.get("models") or []
```

### 9. Frontend Not Rebuilt

**Symptom:** "I changed constants.ts but the UI still shows old data."

**Cause:** The frontend app uses compiled `dist/` files, not source `.ts`.

**Fix:** Rebuild the frontend:
```bash
cd apps/desktop && npm run build   # Desktop
cd web && npm run build            # Web dashboard
```

---

## 12. Debugging Tips

### Verify Provider Registration

```python
from providers import list_providers
for p in list_providers():
    if 'myprovider' in p.name:
        print(f"Found: {p.name} — {p.base_url}")
```

### Verify API Key Loading

```python
from dotenv import load_dotenv
from hermes_constants import get_hermes_home
load_dotenv(get_hermes_home() / '.env')
import os
print(os.environ.get('MYPROVIDER_API_KEY', 'NOT SET'))
```

### Verify Model List

```python
from hermes_cli.models import cached_provider_model_ids
models = cached_provider_model_ids('myprovider', force_refresh=True)
print(f"Models: {len(models)}")
for m in models[:10]:
    print(f"  {m}")
```

### Verify Picker Pipeline

```python
from hermes_cli.model_switch import list_authenticated_providers
providers = list_authenticated_providers()
for p in providers:
    if 'myprovider' in p.get('slug', ''):
        print(f"Picker: {p['name']} — {p.get('total_models', 0)} models")
```

### Verify Backend API Response

```python
from hermes_cli.provider_catalog import provider_catalog
for d in provider_catalog():
    if 'myprovider' in d.slug:
        print(f"Catalog: {d.label} — tab={d.tab} — env={d.api_key_env_vars}")
```

### Check Disk Cache

```python
import json
from hermes_constants import get_hermes_home
cache_path = get_hermes_home() / 'provider_models_cache.json'
with open(cache_path) as f:
    cache = json.load(f)
print(json.dumps(cache.get('myprovider', {}), indent=2))
```

---

## 13. Lessons from Real Changes

### Adding Rewind AI Provider (2026-07-21)

**What we did:**
1. Created provider plugin with custom `fetch_models()` override
2. Added API key to all 10 profiles
3. Added UI entries to desktop + web hardcoded lists
4. Modified OpenRouter to collect free models from live catalog

**What we learned:**

- **Profile isolation is real.** Adding the API key to root `.env` is not
  enough. Every profile needs its own copy. Check `get_hermes_home()` to
  know which `.env` is active.

- **Stale caches hide fixes.** After modifying provider code, the old
  model list persists in `provider_models_cache.json` for up to 1 hour.
  Always `clear_provider_models_cache()` after provider changes.

- **API response formats vary.** Rewind returns `{"models": [...]}` not
  `{"data": [...]}`. The base `ProviderProfile.fetch_models()` assumes
  the standard format. Override for non-standard providers.

- **WAFs block Python requests.** Some providers block requests without
  proper `User-Agent` headers. The base class handles this, but custom
  `fetch_models()` overrides must include it.

- **Free model filtering requires live API access.** The curated list is
  a static subset. To show all free models, you need the live catalog
  fetch to succeed (network, VPN, etc.).

- **UI has two layers.** The backend catalog (dynamic) determines WHAT
  shows. The hardcoded `PROVIDER_GROUPS` determines HOW it shows (name,
  description, docs URL, sort order). Both need updating for a polished
  experience.

- **The picker pipeline has multiple gates.** A provider must pass ALL of:
  1. `is_runtime_provider_routable()` — registered and recognized
  2. `has_creds` — API key in environment
  3. Tool-calling support — model must support tools
  4. Not filtered by `explicit_only` or dedup logic

### Modifying OpenRouter Free Model Collection (2026-07-21)

**What we did:** Modified `fetch_openrouter_models()` to also scan ALL
live models and append free ones not in the curated list.

**What we learned:**

- **The curated list is intentional.** It's a hand-picked agentic subset.
  Blindly showing all 400+ OpenRouter models would overwhelm users.
  Adding free models is a targeted extension, not a replacement.

- **Free detection has two methods.** OpenRouter uses pricing metadata
  (prompt + completion = 0). Rewind uses `:free` suffix. Different
  providers need different free-detection logic.

- **Live API availability varies by network.** OpenRouter's API returned
  403 without VPN. The code must fall back gracefully to the curated cache.

---

## Quick Reference

### Common Commands

```bash
# Clear model cache
python -c "from hermes_cli.models import clear_provider_models_cache; clear_provider_models_cache()"

# Check provider registration
python -c "from providers import list_providers; [print(p.name) for p in list_providers()]"

# Check CANONICAL_PROVIDERS
python -c "from hermes_cli.models import CANONICAL_PROVIDERS; [print(p.slug) for p in CANONICAL_PROVIDERS]"

# Check env var loading
python -c "from dotenv import load_dotenv; from hermes_constants import get_hermes_home; load_dotenv(get_hermes_home() / '.env'); import os; print(os.environ.get('MY_API_KEY', 'NOT SET'))"

# Verify picker output
python -c "from hermes_cli.model_switch import list_authenticated_providers; [print(p['slug'], p.get('total_models', 0)) for p in list_authenticated_providers()]"
```

### Key File Locations

| What | Where |
|------|-------|
| Provider plugin | `plugins/model-providers/<name>/__init__.py` |
| Provider registry | `providers/__init__.py` |
| Provider base class | `providers/base.py` |
| Model lists | `hermes_cli/models.py` |
| Picker pipeline | `hermes_cli/model_switch.py` |
| Provider catalog | `hermes_cli/provider_catalog.py` |
| Auth/credentials | `hermes_cli/auth.py` |
| Config defaults | `hermes_cli/config.py` |
| Desktop UI constants | `apps/desktop/src/app/settings/constants.ts` |
| Web UI env page | `web/src/pages/EnvPage.tsx` |
| Model name display | `apps/desktop/src/lib/model-status-label.ts` |
| Disk cache | `$HERMES_HOME/provider_models_cache.json` |
