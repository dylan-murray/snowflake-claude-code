"""Manual check: run model discovery against a real Snowflake account.

Prints the raw SHOW CORTEX BASE MODELS shape alongside what the proxy makes of
it, so a column-name or type mismatch is obvious rather than silently landing
in the fallback path. Reads the same config/env as the CLI and opens no proxy,
launches no Claude Code, and issues no inference calls.

    uv run python scripts/check_discovery.py
"""

from __future__ import annotations

from snowflake.connector import DictCursor

from snowflake_claude_code.auth import ConnectionManager
from snowflake_claude_code.config import DEFAULT_MODEL, Config
from snowflake_claude_code.models import FAMILIES, advertised, discover, resolve


def main() -> None:
    config = Config.load()
    config.validate()

    print(f"Authenticating to Snowflake ({config.account})...")
    manager = ConnectionManager(config)
    manager.open()
    print("Authenticated.\n")

    try:
        with manager.connection.cursor(DictCursor) as cur:
            rows = cur.execute("SHOW CORTEX BASE MODELS").fetchall()

        print(f"raw rows: {len(rows)}")
        if rows:
            print(f"raw columns: {sorted(rows[0])}\n")
            claude = [r for r in rows if "claude" in str(r.get("name", r.get("NAME", ""))).lower()]
            print(f"raw claude rows: {len(claude)}")
            for row in claude:
                name = row.get("name", row.get("NAME"))
                status = row.get("lifecycle_status", row.get("LIFECYCLE_STATUS"))
                regions = row.get("available_regions", row.get("AVAILABLE_REGIONS"))
                print(f"  {name!r:32} lifecycle={status!r:8} regions={str(regions)[:60]}")
        else:
            print("  (no rows — the role may lack model grants)")

        available = discover(manager.connection)
        print(f"\nparsed claude models: {len(available)}")
        for model in sorted(available, key=lambda m: (m.family, m.version)):
            print(f"  {model.name:24} {model.lifecycle}")

        print("\nalias resolution:")
        for family in FAMILIES:
            marker = "  <- default" if family == DEFAULT_MODEL else ""
            print(f"  {family:8} -> {resolve(family, available)}{marker}")

        print(f"\n/v1/models would advertise: {list(advertised(available))}")

        if not available:
            print("\nWARNING: discovery returned nothing; the CLI would use the fallback list.")
    finally:
        manager.close()


if __name__ == "__main__":
    main()
