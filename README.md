# ❄️ snowflake-claude-code

[![PyPI](https://img.shields.io/pypi/v/snowflake-claude-code.svg)](https://pypi.org/project/snowflake-claude-code/)
[![Python](https://img.shields.io/pypi/pyversions/snowflake-claude-code.svg)](https://pypi.org/project/snowflake-claude-code/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/dylan-murray/snowflake-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/dylan-murray/snowflake-claude-code/actions/workflows/ci.yml)

Run [Claude Code](https://claude.com/product/claude-code) against Claude models hosted on [Snowflake Cortex](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions) — so that prompts, code, and responses stay inside your Snowflake governance boundary instead of being sent to Anthropic's public API.

The CLI authenticates to Snowflake (SSO or PAT), starts a local FastAPI proxy that translates Anthropic Messages API calls to Cortex Inference calls (and streams back SSE in Anthropic format), then launches Claude Code pointed at the proxy.

```
claude-code  →  FastAPI proxy (localhost:4000)  →  Snowflake Cortex Inference REST API
```

## 🔒 Governance & data sovereignty

The whole point of this project: **nothing from your Claude Code session leaves Snowflake.** Every prompt, file read, tool call, and model response flows through Cortex inference inside your existing Snowflake account. No traffic to `api.anthropic.com`, no separate API key to manage, no additional vendor to onboard.

Concretely:

- 🚫 **No external data egress.** The proxy runs on `127.0.0.1` only. Claude Code's `ANTHROPIC_BASE_URL` points at localhost — outbound traffic from Claude Code cannot reach Anthropic's servers.
- 🛡️ **Existing Snowflake IAM applies.** Access to Claude models is gated by whatever role, warehouse, and network policy controls already govern your Snowflake account. Revoking a user's Snowflake access revokes their AI access.
- 🔑 **Same authentication you already audit.** Browser SSO goes through your Snowflake IdP (Okta, Azure AD, etc.). Programmatic access tokens use your existing PAT lifecycle.
- 📝 **Auditable.** Every inference call is recorded in `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY` with timestamp, user ID, model, token counts, and request ID. Join to `SNOWFLAKE.ACCOUNT_USAGE.USERS` for who-did-what.
- 🌍 **Data residency honored.** Cortex inference runs in the region(s) your account is provisioned in. Use Snowflake's [cross-region inference](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference) controls to keep data in an approved geography.
- 🧠 **No model training on your data.** Per [Snowflake's Cortex terms](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions#data-usage), inputs and outputs are not used to train Snowflake's or the model provider's models.

If your org has already decided Snowflake is the right place for sensitive data (customer records, contracts, source code, PHI, PII), this keeps Claude Code on the same side of that line.

## ✨ Why else

- 💰 **Consolidated AI spend.** Cortex token usage rolls up alongside the rest of your warehouse costs — no separate Anthropic billing relationship or budget to reconcile.
- 🔐 **No Anthropic API keys to rotate.** Auth is your existing Snowflake SSO/PAT.
- 🧩 **Same Claude Code UX.** `/model`, streaming, tool calls, and slash commands all work normally.

## 📦 Install

Requires Python 3.10+ and the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed globally.

```bash
# Recommended: isolated install via uv
uv tool install snowflake-claude-code

# Or via pipx
pipx install snowflake-claude-code

# Or plain pip (consider using a virtualenv)
pip install snowflake-claude-code
```

Then make sure Claude Code itself is available on your `PATH`:

```bash
npm install -g @anthropic-ai/claude-code
```

### One-shot run without installing

Run straight from PyPI, no global install:

```bash
uvx snowflake-claude-code --account MYORG-MYACCOUNT --user me@company.com
```

## 🚀 Usage

```bash
snowflake-claude-code \
  --account MYORG-MYACCOUNT \
  --user me@company.com \
  --model claude-sonnet-4-6
```

This opens your browser for Snowflake SSO, starts the proxy on `127.0.0.1:4000`, and drops you into Claude Code.

### 🚩 Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--account` | — | Snowflake account identifier (e.g. `MYORG-MYACCOUNT`) |
| `--user` | — | Snowflake username (required for SSO) |
| `--model` | `claude-sonnet-4-6` | Cortex model ID to use by default |
| `--port` | `4000` | Local port for the proxy |
| `--token` | — | Snowflake programmatic access token (skips SSO) |
| `--verbose`, `-v` | off | Debug logging |

### 🌱 Environment variables

Any flag can be set via an env var:

```bash
export SNOWFLAKE_ACCOUNT=MYORG-MYACCOUNT
export SNOWFLAKE_USER=me@company.com
export SNOWFLAKE_MODEL=claude-opus-4-6
export SNOWFLAKE_PORT=4000
export SNOWFLAKE_TOKEN=pat-...   # optional, skips SSO
```

### 📁 Config file

Or put them in `~/.snowflake-claude-code/config.toml`:

```toml
account = "MYORG-MYACCOUNT"
user = "me@company.com"
default_model = "claude-sonnet-4-6"
port = 4000
# token = "pat-..."  # optional
```

**Precedence:** CLI flags > env vars > config file > defaults.

## 🤖 Supported models

The proxy advertises these Claude models via `GET /v1/models` (also what appears in your startup banner):

| Model ID | Notes |
|----------|-------|
| `claude-sonnet-4-6` | Current default — 1M context built-in |
| `claude-sonnet-4-5` | Previous-generation Sonnet |
| `claude-opus-4-6` | Most capable Claude model on Cortex |
| `claude-opus-4-5` | Previous-generation Opus |
| `claude-haiku-4-5` | Fastest, cheapest |

**Region & availability matter.** Not every Claude model is available in every Snowflake region natively. Use Snowflake's [cross-region inference](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference) if a model isn't available locally, or check `SHOW CORTEX FUNCTIONS` for your region.

**Non-Claude models:** the proxy forwards whatever model ID you pass, so Cortex-hosted Mistral / Llama / DeepSeek models will also work for plain chat — pass `--model mistral-large2` at launch. Tool calling compatibility varies by model.

### 🎛️ Picker behavior

Claude Code's built-in `/model` picker shows Anthropic's canonical aliases (Sonnet, Opus, Haiku, 1M-context variants). The picker currently cannot be fully replaced with a custom catalog, so we instead:
- Add your **launched model** as a custom picker entry labeled "Via Snowflake Cortex".
- Swap models for a session by relaunching with a different `--model`.

If the picker points at a model Cortex doesn't host (e.g. an Opus version that isn't mirrored), you'll get a `400` from Cortex with the actual error message — nothing silently breaks.

## 🔍 Verifying requests hit Snowflake

Inside a session, you can confirm routing via Snowflake:

```sql
SELECT
    START_TIME,
    MODEL_NAME,
    TOKENS,
    USER_ID,
    INFERENCE_REGION
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY
WHERE START_TIME >= CURRENT_DATE()
ORDER BY START_TIME DESC;
```

(There's a typical 45m–3h lag on `ACCOUNT_USAGE` views. For near-real-time, run with `--verbose` to see every proxied request in your terminal.)

## 🏗️ Architecture

- **CLI** (`snowflake_claude_code/cli.py`) — parses config, authenticates, starts the proxy in a background thread, launches the `claude` subprocess with `ANTHROPIC_BASE_URL` pointing at it.
- **Proxy** (`snowflake_claude_code/proxy.py`) — FastAPI app exposing `/v1/messages`, `/v1/models`, `/v1/health`. Translates requests/responses via the translator module.
- **Translator** (`snowflake_claude_code/translate.py`) — pure functions mapping between Anthropic and Cortex formats, plus a `StreamAdapter` for SSE.
- **Auth** (`snowflake_claude_code/auth.py`) — `snowflake-connector-python` wrapper (browser SSO or OAuth/PAT).

The proxy binds to `127.0.0.1` only — nothing is exposed over the network. The Snowflake token lives in memory for the session; no disk persistence.

## 🛠️ Development

```bash
git clone https://github.com/dylan-murray/snowflake-claude-code.git
cd snowflake-claude-code
uv sync --group dev

uv run pytest              # run the test suite
uv run ruff check .        # lint
uv run ruff format .       # format
```

CI runs `ruff check`, `ruff format --check`, and `pytest` on Python 3.10–3.13 via GitHub Actions.

## ⚠️ Known limitations

- **Token expiry.** Snowflake SSO tokens last 1–4 hours. The proxy transparently re-auths on a 401 from Cortex and retries the request, so long-running sessions stay alive. `keyring` is installed by default so the refresh doesn't trigger a new browser popup if your cached token is still valid.
- **Model catalog.** Claude Code hardcodes its picker entries; `availableModels` only filters built-in aliases, it doesn't add custom ones. `ANTHROPIC_CUSTOM_MODEL_OPTION` supports just one extra entry, which is what we use. If Anthropic ever exposes multi-entry custom catalogs, the full `CORTEX_MODELS` list can replace this workaround.
- **Long-context.** Snowflake Cortex base models (e.g. `claude-sonnet-4-6`) already expose 1M context natively. Picking the "1M context" entry in Claude Code routes to the same base model ID.

## 📄 License

MIT — see [LICENSE](LICENSE).
