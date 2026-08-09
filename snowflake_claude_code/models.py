"""Discovery of the Claude models a Snowflake account can actually reach.

``SHOW CORTEX BASE MODELS`` reports lifecycle status and is filtered to models
the current role holds grants on, so the proxy advertises the live set instead
of a hardcoded list that goes stale with every Cortex release. Discovery is
best-effort: when the query fails the last-known set below is used instead.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

FAMILIES = ("opus", "sonnet", "haiku")

# Last-known-good set, used only when discovery fails. Lifecycle values mirror
# what Cortex reported when this was written; discovery supersedes them.
_FALLBACK: tuple[tuple[str, str], ...] = (
    ("claude-sonnet-5", "GA"),
    ("claude-sonnet-4-6", "GA"),
    ("claude-sonnet-4-5", "GA"),
    ("claude-opus-5", "PUPR"),
    ("claude-opus-4-8", "GA"),
    ("claude-opus-4-7", "GA"),
    ("claude-opus-4-6", "GA"),
    ("claude-opus-4-5", "GA"),
    ("claude-haiku-4-5", "GA"),
)

_NAME_RE = re.compile(r"^claude-(opus|sonnet|haiku)-(\d+(?:-\d+)*)$")

_RETIRED = frozenset({"EOL"})


@dataclass(frozen=True, slots=True, order=False)
class CortexModel:
    name: str
    family: str
    version: tuple[int, ...]
    lifecycle: str

    @property
    def is_ga(self) -> bool:
        return self.lifecycle == "GA"

    @property
    def is_retired(self) -> bool:
        return self.lifecycle in _RETIRED


def parse_model(name: str, lifecycle: str) -> CortexModel | None:
    """Return a CortexModel for recognizably-versioned Claude names, else None.

    Non-Claude Cortex models and the legacy ``claude-4-sonnet`` spelling are
    skipped: they can still be selected with an explicit --model, they just
    don't take part in family resolution.
    """
    match = _NAME_RE.match(name.strip())
    if match is None:
        return None
    version = tuple(int(part) for part in match.group(2).split("-"))
    return CortexModel(
        name=name.strip(),
        family=match.group(1),
        version=version,
        lifecycle=lifecycle.strip().upper(),
    )


def fallback_models() -> list[CortexModel]:
    parsed = (parse_model(name, lifecycle) for name, lifecycle in _FALLBACK)
    return [m for m in parsed if m is not None]


def discover(conn: object) -> list[CortexModel]:
    """List Claude models via SHOW CORTEX BASE MODELS. Empty on any failure.

    The statement needs no running warehouse, so this costs a round trip and
    no credits.
    """
    try:
        from snowflake.connector import DictCursor

        with conn.cursor(DictCursor) as cur:  # type: ignore[attr-defined]
            rows = cur.execute("SHOW CORTEX BASE MODELS").fetchall()
    except Exception as exc:
        logger.debug("Cortex model discovery failed: %s", exc)
        return []

    models = []
    for row in rows:
        name = _column(row, "name")
        if not name:
            continue
        model = parse_model(name, _column(row, "lifecycle_status") or "")
        if model is not None:
            models.append(model)
    return models


def _column(row: object, key: str) -> str:
    if not isinstance(row, dict):
        return ""
    value = row.get(key, row.get(key.upper(), ""))
    return value if isinstance(value, str) else ""


def resolve(requested: str, available: Sequence[CortexModel]) -> str:
    """Expand a family alias ("opus") to the newest GA model in that family.

    Anything else — a full model ID, a non-Claude Cortex model — passes through
    untouched, so preview and third-party models stay selectable by name.
    """
    alias = requested.strip().lower().removeprefix("claude-")
    if alias not in FAMILIES:
        return requested.strip()

    pool = _newest_ga(available, alias) or _newest_ga(fallback_models(), alias)
    if pool is None:
        raise SystemExit(
            f"Error: no generally available '{alias}' model found on Cortex. "
            "Pass --model with an explicit Cortex model ID."
        )
    return pool.name


def _newest_ga(models: Iterable[CortexModel], family: str) -> CortexModel | None:
    candidates = [m for m in models if m.family == family and m.is_ga]
    return max(candidates, key=lambda m: m.version) if candidates else None


def advertised(available: Sequence[CortexModel]) -> tuple[str, ...]:
    """Model IDs to expose on /v1/models, newest first within each family."""
    models = [m for m in (available or fallback_models()) if not m.is_retired]
    ordered = sorted(models, key=lambda m: (FAMILIES.index(m.family), tuple(-p for p in m.version)))
    return tuple(m.name for m in ordered)
