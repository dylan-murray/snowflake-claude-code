from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from snowflake_claude_code.models import (
    CortexModel,
    advertised,
    by_family_then_newest,
    discover,
    fallback_models,
    parse_model,
    resolve,
)


def _rows(*specs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"name": name, "lifecycle_status": status} for name, status in specs]


def _conn(rows: list[dict[str, str]] | Exception) -> MagicMock:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    if isinstance(rows, Exception):
        cursor.execute.side_effect = rows
    else:
        cursor.execute.return_value.fetchall.return_value = rows
    return conn


class TestParseModel:
    @pytest.mark.parametrize(
        ("name", "family", "version"),
        [
            ("claude-sonnet-5", "sonnet", (5,)),
            ("claude-opus-4-8", "opus", (4, 8)),
            ("claude-haiku-4-5", "haiku", (4, 5)),
        ],
    )
    def test_parses_versioned_claude_names(self, name, family, version):
        model = parse_model(name, "GA")
        assert model is not None
        assert (model.family, model.version) == (family, version)

    @pytest.mark.parametrize("name", ["claude-4-sonnet", "mistral-large2", "llama3.1-70b", "claude", ""])
    def test_skips_unrecognized_names(self, name):
        assert parse_model(name, "GA") is None

    def test_normalizes_lifecycle_case(self):
        model = parse_model("claude-sonnet-5", "ga")
        assert model is not None and model.is_ga

    def test_normalizes_uppercase_names_from_show(self):
        """SHOW CORTEX BASE MODELS reports names uppercased; the inference API
        takes them lowercased."""
        model = parse_model("CLAUDE-SONNET-5", "GA")

        assert model is not None
        assert model.name == "claude-sonnet-5"
        assert (model.family, model.version) == ("sonnet", (5,))

    def test_tolerates_null_lifecycle(self):
        model = parse_model("CLAUDE-SONNET-5", None)

        assert model is not None and not model.is_ga


class TestDiscover:
    def test_returns_parsed_claude_models(self):
        conn = _conn(_rows(("claude-sonnet-5", "GA"), ("mistral-large2", "GA")))

        models = discover(conn)

        assert [m.name for m in models] == ["claude-sonnet-5"]

    def test_reads_uppercase_columns(self):
        conn = _conn([{"NAME": "claude-opus-4-8", "LIFECYCLE_STATUS": "GA"}])

        assert [m.name for m in discover(conn)] == ["claude-opus-4-8"]

    def test_handles_real_show_output_shape(self):
        """Shape observed against a live account: uppercased names, NULL
        lifecycle on some rows, non-Claude models mixed in."""
        conn = _conn(
            [
                {"name": "ARCTIC-EXTRACT", "lifecycle_status": "GA"},
                {"name": "ARCTIC-PARSE-DOCUMENT", "lifecycle_status": None},
                {"name": "CLAUDE-SONNET-5", "lifecycle_status": "GA"},
                {"name": "CLAUDE-OPUS-4-8", "lifecycle_status": "GA"},
                {"name": "MISTRAL-LARGE2", "lifecycle_status": None},
            ]
        )

        models = discover(conn)

        assert [m.name for m in models] == ["claude-sonnet-5", "claude-opus-4-8"]
        assert resolve("sonnet", models) == "claude-sonnet-5"

    def test_returns_empty_when_query_fails(self):
        assert discover(_conn(RuntimeError("no grants"))) == []


class TestResolve:
    def test_alias_picks_newest_ga_in_family(self):
        available = [
            parse_model("claude-opus-4-8", "GA"),
            parse_model("claude-opus-5", "PUPR"),
            parse_model("claude-opus-4-7", "GA"),
        ]

        assert resolve("opus", available) == "claude-opus-4-8"

    def test_alias_prefers_higher_major_over_more_segments(self):
        available = [parse_model("claude-sonnet-4-6", "GA"), parse_model("claude-sonnet-5", "GA")]

        assert resolve("sonnet", available) == "claude-sonnet-5"

    @pytest.mark.parametrize("alias", ["opus", "OPUS", " claude-opus "])
    def test_alias_forms(self, alias):
        available = [parse_model("claude-opus-4-8", "GA")]

        assert resolve(alias, available) == "claude-opus-4-8"

    @pytest.mark.parametrize(
        "requested", ["claude-opus-5", "claude-sonnet-4-6", "mistral-large2", "claude-opus-4-6"]
    )
    def test_explicit_ids_pass_through(self, requested):
        assert resolve(requested, fallback_models()) == requested

    def test_falls_back_when_discovery_empty(self):
        assert resolve("sonnet", ()) == "claude-sonnet-5"

    @pytest.mark.parametrize(
        "pinned",
        [
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-opus-4-6",
            "claude-opus-4-5",
            "claude-haiku-4-5",
            "claude-sonnet-4-6[1m]",
            "claude-4-sonnet",
            "mistral-large2",
            "llama3.1-70b",
        ],
    )
    def test_pre_alias_model_values_are_unchanged(self, pinned):
        """Configs and flags written before family aliases existed must keep
        selecting exactly the model they name."""
        assert resolve(pinned, fallback_models()) == pinned
        assert resolve(pinned, ()) == pinned

    def test_preview_only_family_still_falls_back_to_known_ga(self):
        available = [CortexModel("claude-opus-9", "opus", (9,), "PRPR")]

        assert resolve("opus", available) == "claude-opus-4-8"

    def test_exits_when_no_ga_model_anywhere(self, monkeypatch):
        monkeypatch.setattr("snowflake_claude_code.models._FALLBACK", ())
        available = [CortexModel("claude-opus-9", "opus", (9,), "PRPR")]

        with pytest.raises(SystemExit):
            resolve("opus", available)


class TestAdvertised:
    def test_orders_newest_first_within_family(self):
        names = advertised(
            [
                parse_model("claude-sonnet-4-6", "GA"),
                parse_model("claude-opus-4-8", "GA"),
                parse_model("claude-sonnet-5", "GA"),
            ]
        )

        assert names == ("claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6")

    def test_omits_retired_models(self):
        names = advertised([parse_model("claude-sonnet-5", "GA"), parse_model("claude-opus-4-5", "EOL")])

        assert names == ("claude-sonnet-5",)

    def test_uses_fallback_when_discovery_empty(self):
        assert advertised(()) == tuple(m.name for m in _ordered_fallback())


def _ordered_fallback() -> list[CortexModel]:
    return sorted(fallback_models(), key=by_family_then_newest)
