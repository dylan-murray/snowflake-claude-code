# ❄️ snowflake-claude-code

[![PyPI](https://img.shields.io/pypi/v/snowflake-claude-code.svg)](https://pypi.org/project/snowflake-claude-code/)
[![CI](https://github.com/dylan-murray/snowflake-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/dylan-murray/snowflake-claude-code/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Run [Claude Code](https://claude.com/product/claude-code) against Claude models served by [Cortex](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions) inside your Snowflake account, so prompts and responses never leave your Snowflake governance boundary.

```
Claude Code  →  FastAPI proxy (127.0.0.1:4000)  →  Snowflake Cortex Inference
```

The CLI authenticates to Snowflake (SSO or PAT), starts a local proxy that translates Anthropic Messages API calls to Cortex Inference calls (SSE streaming included), and launches Claude Code pointed at the proxy.

## ⚡ Quick start

```bash
uv tool install snowflake-claude-code
npm install -g @anthropic-ai/claude-code
snowflake-claude-code --account MYORG-MYACCOUNT --user me@company.com
```

Browser pops for Snowflake SSO, proxy spins up, Claude Code launches.

## 🔒 Why

**Your Claude Code session never talks to Anthropic.** Every prompt, file read, tool call, and model response goes over TLS to the same Snowflake endpoint your warehouse queries already use — governed by your existing Snowflake trust boundary, not a new third-party LLM vendor.

- 🚫 **No traffic to Anthropic.** The proxy binds to `127.0.0.1` only; the only outbound endpoint is your Snowflake account's API.
- 🛡️ **Snowflake IAM applies.** Role, warehouse, and network policy controls gate model access. Revoke Snowflake → revoke AI.
- 🔑 **Familiar auth.** Browser SSO flows through your existing IdP; PATs for headless.
- 📝 **Full audit trail.** Every call lands in `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY`.
- 🌍 **Data residency honored.** Inference runs in your account's region.
- 🧠 **No training on your data.** Per [Snowflake Cortex terms](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions#data-usage).
- 💰 **Consolidated spend.** Cortex tokens roll up with your warehouse costs.
- ♻️ **Transparent re-auth.** Expired tokens trigger a silent refresh mid-session.

## 📦 Install

Requires Python 3.10+ and the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI.

```bash
uv tool install snowflake-claude-code     # recommended
pipx install snowflake-claude-code        # or pipx
pip install snowflake-claude-code         # or pip
uvx snowflake-claude-code ...             # or run without installing
```

And Claude Code itself:

```bash
npm install -g @anthropic-ai/claude-code
```

## 🚀 Usage

```bash
snowflake-claude-code \
  --account MYORG-MYACCOUNT \
  --user me@company.com \
  --model claude-sonnet-4-6
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--account` | — | Snowflake account identifier |
| `--user` | — | Snowflake username (required for SSO) |
| `--model` | `claude-sonnet-4-6` | Cortex model ID |
| `--port` | `4000` | Local proxy port |
| `--token` | — | Snowflake PAT (skips browser SSO) |
| `--verbose`, `-v` | off | Debug logging |

Any flag can be set via an env var (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, etc.) or `~/.snowflake-claude-code/config.toml`.

Precedence: **CLI flags > env vars > config file > defaults.**

## 🤖 Supported models

| Model ID | Notes |
|----------|-------|
| `claude-sonnet-4-6` | Default — 1M context built-in |
| `claude-sonnet-4-5` | Previous-generation Sonnet |
| `claude-opus-4-6` | Most capable Claude model on Cortex |
| `claude-opus-4-5` | Previous-generation Opus |
| `claude-haiku-4-5` | Fastest, cheapest |

Not every model is available in every Snowflake region — use [cross-region inference](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference) or check `SHOW CORTEX FUNCTIONS` for your region.

Non-Claude Cortex models work for plain chat too (`--model mistral-large2`, `--model llama3.1-70b`). Tool-calling compatibility varies.

### /model picker

Claude Code's built-in picker lists its own aliases (Sonnet, Opus, Haiku, 1M-context variants) — we can't replace that catalog. The model you launched with appears as a custom picker entry labeled "Via Snowflake Cortex". Swap by relaunching with a different `--model`.

## 🔍 Verify traffic is hitting Snowflake

```sql
SELECT START_TIME, MODEL_NAME, TOKENS, USER_ID, INFERENCE_REGION
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY
WHERE START_TIME >= CURRENT_DATE()
ORDER BY START_TIME DESC;
```

`ACCOUNT_USAGE` views lag 45 min–3 hours. For real-time, run with `--verbose`.

## 🏗️ Architecture

![snowflake-claude-code architecture](docs/architecture.svg)

**Data path.** Claude Code sends Anthropic-format requests to the local FastAPI proxy via `ANTHROPIC_BASE_URL` — it has no idea Anthropic isn't on the other end. The proxy translates each request to a Cortex `CompleteRequest` (tool schemas, streaming, and all), forwards it over TLS to your Snowflake account, and streams the response back in Anthropic SSE format.

**Auth path.** `ConnectionManager` opens a Snowflake session via browser SSO (or a PAT, if configured) on first launch. When the Snowflake token expires, a 401 from Cortex triggers a silent re-auth and retry — long-running Claude Code sessions stay alive.

**Audit path.** Every inference call lands in `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY` with timestamp, user ID, model, token counts, and request ID.

**Boundaries.** The proxy binds to `127.0.0.1` only — nothing is exposed over the network. The Snowflake token lives in process memory for the session lifetime and is cleared on exit.

> Diagram source: [`docs/architecture.d2`](docs/architecture.d2). Re-render with [`d2`](https://d2lang.com):
> ```bash
> d2 --theme 0 --pad 40 docs/architecture.d2 docs/architecture.svg
> ```

### Code layout

```
snowflake_claude_code/
├── cli.py        Parse config, start proxy, launch `claude` subprocess
├── proxy.py      FastAPI app: /v1/messages, /v1/models, /v1/health
├── translate.py  Anthropic ⇄ Cortex format translation + SSE adapter
├── auth.py       Snowflake connector + ConnectionManager (re-auth on 401)
└── config.py     Layered config loader (CLI > env > TOML > defaults)
```

## 🛠️ Development

```bash
git clone https://github.com/dylan-murray/snowflake-claude-code.git
cd snowflake-claude-code
uv sync --group dev

uv run pytest
uv run ruff check .
uv run ruff format .
```

CI runs on Python 3.10–3.13.

## 📄 License

MIT — see [LICENSE](LICENSE).
