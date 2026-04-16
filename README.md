<div align="center">

# ❄️ snowflake-claude-code

**Claude Code, governed by Snowflake.**

Run [Claude Code](https://claude.com/product/claude-code) against Claude models hosted in your [Snowflake Cortex](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions) account — so prompts, code, and responses never leave your data boundary.

[![PyPI](https://img.shields.io/pypi/v/snowflake-claude-code?style=flat-square&color=0080ff)](https://pypi.org/project/snowflake-claude-code/)
[![Python](https://img.shields.io/pypi/pyversions/snowflake-claude-code?style=flat-square&color=3776ab)](https://pypi.org/project/snowflake-claude-code/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/dylan-murray/snowflake-claude-code/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/dylan-murray/snowflake-claude-code/actions/workflows/ci.yml)
[![Snowflake](https://img.shields.io/badge/Snowflake-Cortex-29B5E8?style=flat-square&logo=snowflake&logoColor=white)](https://docs.snowflake.com/en/user-guide/snowflake-cortex)
[![Anthropic](https://img.shields.io/badge/Claude_Code-compatible-d97757?style=flat-square)](https://claude.com/product/claude-code)

</div>

```
 ┌──────────────┐     ┌─────────────────────┐     ┌────────────────────────┐
 │  Claude Code │ ──▶ │  FastAPI Proxy      │ ──▶ │  Snowflake Cortex      │
 │  (your CLI)  │     │  127.0.0.1:4000     │     │  Inference REST API    │
 └──────────────┘     └─────────────────────┘     └────────────────────────┘
                         Anthropic ⇄ Cortex         Your account. Your data.
                         format translation         Your governance.
```

---

## ⚡ Quick start

```bash
# 1. Install
uv tool install snowflake-claude-code

# 2. Make sure Claude Code is on your PATH
npm install -g @anthropic-ai/claude-code

# 3. Go
snowflake-claude-code --account MYORG-MYACCOUNT --user me@company.com
```

Browser pops for Snowflake SSO → local proxy spins up → Claude Code launches pointed at it. That's the whole setup.

> [!TIP]
> Want to try it without installing globally? `uvx snowflake-claude-code --account ... --user ...`

---

## 🔒 Why: governance & data sovereignty

> **Nothing from your Claude Code session leaves Snowflake.** Every prompt, file read, tool call, and model response flows through Cortex inference inside your existing Snowflake account. No traffic to `api.anthropic.com`. No separate API key to manage. No additional vendor to onboard.

| | |
|---|---|
| 🚫 **Zero egress** | Proxy binds to `127.0.0.1` only. Claude Code cannot reach Anthropic's servers. |
| 🛡️ **Snowflake IAM** | Role, warehouse, and network policy controls gate model access. Revoke Snowflake → revoke AI. |
| 🔑 **Familiar auth** | Browser SSO flows through your Snowflake IdP (Okta, Azure AD, Ping). PAT for headless. |
| 📝 **Full audit trail** | Every call lands in `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY`. |
| 🌍 **Data residency** | Inference runs in your account's region. Cross-region policy honored. |
| 🧠 **No training on your data** | Per [Snowflake Cortex terms](https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions#data-usage). |

If your org has already decided Snowflake is the right place for sensitive data — customer records, contracts, source code, PHI, PII — this keeps Claude Code on the same side of that line.

### What else you get

- 💰 **Consolidated AI spend** — Cortex tokens roll up with warehouse costs. One budget, one invoice.
- 🔐 **No Anthropic keys to rotate** — SSO or PAT, same lifecycle as everything else.
- 🧩 **Identical Claude Code UX** — `/model`, streaming, tool calls, slash commands, all of it.
- ♻️ **Transparent re-auth** — expired tokens trigger a silent refresh mid-session, no session drop.

---

## 📦 Install

Requires **Python 3.10+** and the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI globally installed.

<table>
<tr><th>Method</th><th>Command</th></tr>
<tr><td>uv (recommended)</td><td><code>uv tool install snowflake-claude-code</code></td></tr>
<tr><td>pipx</td><td><code>pipx install snowflake-claude-code</code></td></tr>
<tr><td>pip</td><td><code>pip install snowflake-claude-code</code></td></tr>
<tr><td>One-shot (no install)</td><td><code>uvx snowflake-claude-code ...</code></td></tr>
</table>

And Claude Code itself:

```bash
npm install -g @anthropic-ai/claude-code
```

---

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
| `--account` | — | Snowflake account identifier (e.g. `MYORG-MYACCOUNT`) |
| `--user` | — | Snowflake username (required for SSO) |
| `--model` | `claude-sonnet-4-6` | Cortex model ID |
| `--port` | `4000` | Local proxy port |
| `--token` | — | Snowflake PAT (skips browser SSO) |
| `--verbose`, `-v` | off | Debug logging |

### Configuration precedence

```
CLI flags  >  env vars  >  ~/.snowflake-claude-code/config.toml  >  built-in defaults
```

<details>
<summary><b>Environment variables</b></summary>

```bash
export SNOWFLAKE_ACCOUNT=MYORG-MYACCOUNT
export SNOWFLAKE_USER=me@company.com
export SNOWFLAKE_MODEL=claude-opus-4-6
export SNOWFLAKE_PORT=4000
export SNOWFLAKE_TOKEN=pat-...       # optional, skips SSO
```

</details>

<details>
<summary><b>Config file</b> (<code>~/.snowflake-claude-code/config.toml</code>)</summary>

```toml
account = "MYORG-MYACCOUNT"
user = "me@company.com"
default_model = "claude-sonnet-4-6"
port = 4000
# token = "pat-..."   # optional
```

</details>

---

## 🤖 Supported models

Advertised via `GET /v1/models` and in the startup banner:

| Model | ID | Tier |
|---|---|---|
| **Sonnet 4.6** ⭐ | `claude-sonnet-4-6` | Default · 1M context built-in |
| Sonnet 4.5 | `claude-sonnet-4-5` | Previous-gen |
| **Opus 4.6** | `claude-opus-4-6` | Most capable on Cortex |
| Opus 4.5 | `claude-opus-4-5` | Previous-gen |
| **Haiku 4.5** | `claude-haiku-4-5` | Fastest & cheapest |

> [!NOTE]
> **Not every model is live in every region.** Use Snowflake's [cross-region inference](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference) to reach models not available natively, or check `SHOW CORTEX FUNCTIONS` for your region.

> [!TIP]
> **Non-Claude Cortex models work too.** The proxy forwards whatever model ID you pass — `--model mistral-large2` or `--model llama3.1-70b` work for plain chat. Tool-calling compatibility varies by model.

<details>
<summary><b>How <code>/model</code> interacts with the picker</b></summary>

Claude Code's picker shows its built-in aliases (Sonnet, Opus, Haiku, 1M-context variants) — we can't replace that catalog. What we *can* do: the model you launched with appears as a custom entry labeled **"Via Snowflake Cortex"**. Swap models for a session by relaunching with `--model <other>`.

If the picker points at a model Cortex doesn't host (e.g. an Opus version that isn't mirrored in your region), you'll get a `400` with Cortex's actual error message. Nothing silently breaks.

</details>

---

## 🔍 Verify requests hit Snowflake

After running a session, confirm in Snowflake:

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

Join `SNOWFLAKE.ACCOUNT_USAGE.USERS` on `USER_ID` for who-did-what.

> [!NOTE]
> `ACCOUNT_USAGE` views lag 45 min–3 hours. For real-time, run with `--verbose` — every proxied request logs to your terminal.

---

## 🏗️ Architecture

```
snowflake_claude_code/
├── cli.py         → Parse config, start proxy, launch `claude` subprocess
├── proxy.py       → FastAPI app; /v1/messages, /v1/models, /v1/health
├── translate.py   → Anthropic ⇄ Cortex format translation + SSE StreamAdapter
├── auth.py        → Snowflake connector + ConnectionManager (re-auth on 401)
└── config.py      → Layered config loader (CLI > env > file > defaults)
```

The proxy binds to `127.0.0.1` only. Your Snowflake token lives in process memory for the session lifetime and is cleared on exit — never persisted to disk.

---

## 🛠️ Development

```bash
git clone https://github.com/dylan-murray/snowflake-claude-code.git
cd snowflake-claude-code
uv sync --group dev

uv run pytest              # test suite
uv run ruff check .        # lint
uv run ruff format .       # format
```

CI runs `ruff check`, `ruff format --check`, and `pytest` on Python 3.10–3.13.

---

## ⚠️ Known limitations

<details>
<summary><b>Token expiry</b></summary>

Snowflake SSO tokens last 1–4 hours. The proxy transparently re-auths on a 401 from Cortex and retries the request, so long-running sessions stay alive. `keyring` is installed by default, so the refresh doesn't trigger a new browser popup if your cached token is still valid.

</details>

<details>
<summary><b>Model picker catalog</b></summary>

Claude Code hardcodes its picker entries; `availableModels` only filters built-in aliases. `ANTHROPIC_CUSTOM_MODEL_OPTION` supports one extra entry, which is what we use. If Anthropic exposes multi-entry custom catalogs, the full model list can replace this workaround.

</details>

<details>
<summary><b>Long-context handling</b></summary>

Snowflake Cortex base models (e.g. `claude-sonnet-4-6`) already expose 1M context natively. Picking "1M context" in Claude Code routes to the same base model ID.

</details>

---

## 📄 License

MIT — see [LICENSE](LICENSE).
