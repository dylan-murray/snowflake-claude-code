# ❄️ snowflake-claude-code

[![PyPI](https://img.shields.io/pypi/v/snowflake-claude-code.svg?cacheSeconds=3600)](https://pypi.org/project/snowflake-claude-code/)
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

The proxy forwards whatever model ID you pass to Cortex, so **any Cortex-hosted model your account has access to will work** — not just Claude. Claude models are the primary target (Claude Code itself is built around them), but non-Claude models work fine for plain chat.

### Anthropic · Claude (recommended for Claude Code)
`claude-sonnet-4-6` *(default · 1M context)*, `claude-sonnet-4-5`, `claude-opus-4-6`, `claude-opus-4-5`, `claude-haiku-4-5`, `claude-4-sonnet`, `claude-3-7-sonnet`

### Meta · Llama
`llama4-maverick`, `llama4-scout`, `llama3.1-405b`, `llama3.3-70b`, `llama3.1-70b`, `llama3.1-8b`, `llama3-70b`, `llama3-8b`

### Mistral AI
`mistral-large2`, `mistral-large`, `mixtral-8x7b`, `mistral-7b`

### OpenAI
`openai-gpt-5.2`, `openai-gpt-5.1`, `openai-gpt-5`, `openai-gpt-5-mini`, `openai-gpt-5-nano`, `openai-gpt-5-chat`, `openai-gpt-4.1`, `openai-o4-mini`, `openai-gpt-oss-120b`, `openai-gpt-oss-20b`

### Google
`gemini-3.1-pro`

### DeepSeek
`deepseek-r1`

### Snowflake
`snowflake-arctic`, `snowflake-llama-3.3-70b`, `snowflake-llama-3.1-405b`

**Availability caveats:**
- **Region.** Not every model is live in every Snowflake region. Use [cross-region inference](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference) or check `SHOW CORTEX FUNCTIONS` for your region.
- **Entitlement.** Your account may not have access to every model — models are enabled per-account.
- **Tool calling.** Claude Code relies on tool-use for file reads, edits, and shell commands. Claude models support this; non-Claude models vary and may silently fail on agentic flows. Stick to Claude if you want the full Claude Code experience.

See the [Snowflake Cortex LLM functions docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions#availability) for the authoritative current list.

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

```
snowflake_claude_code/
├── cli.py        Parse config, start proxy, launch `claude` subprocess
├── proxy.py      FastAPI app: /v1/messages, /v1/models, /v1/health
├── translate.py  Anthropic ⇄ Cortex format translation + SSE adapter
├── auth.py       Snowflake connector + re-auth on 401
└── config.py     Layered config loader
```

The proxy binds to `127.0.0.1` only. The Snowflake token lives in process memory for the session lifetime and is cleared on exit.

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
