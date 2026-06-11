import json
from types import SimpleNamespace

from app.services.reconciliation import entity_resolution, needs_resolution
from tests.factories import make_column, make_table


def _fake_openai(payload: dict | None = None, raise_exc: Exception | None = None):
    """Build a stand-in for openai.OpenAI returning a canned chat completion."""

    class _Completions:
        @staticmethod
        def create(**kwargs):
            if raise_exc is not None:
                raise raise_exc
            message = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class _FakeOpenAI:
        def __init__(self, api_key=None):
            self.chat = SimpleNamespace(completions=_Completions())

    return _FakeOpenAI


PEOPLE = make_table(
    "people",
    [make_column("name", pk=True), make_column("email")],
)


def test_needs_resolution_thresholds():
    assert needs_resolution([], ["name"]) is False
    assert needs_resolution([{"name": "a"}], ["name"]) is False
    assert needs_resolution([{"name": "a"}, {"name": "b"}], ["name"]) is True


def test_single_row_never_constructs_client(monkeypatch):
    class _Boom:
        def __init__(self, api_key=None):
            raise AssertionError("OpenAI must not be constructed for single rows")

    monkeypatch.setattr("openai.OpenAI", _Boom)
    rows = [{"name": "Jane Doe", "email": None}]
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert out == rows
    assert notes == []


def test_merges_two_rows_into_one_entity(monkeypatch):
    rows = [
        {"name": "Doe, Jane", "email": None, "__chunk_index": 0},
        {"name": "Jane Doe", "email": "jane@example.com", "__chunk_index": 1},
    ]
    payload = {
        "entities": [
            {
                "row_indices": [0, 1],
                "merged": {"name": "Jane Doe", "email": "jane@example.com"},
            }
        ]
    }
    monkeypatch.setattr("openai.OpenAI", _fake_openai(payload))
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert len(out) == 1
    assert out[0]["email"] == "jane@example.com"
    # Internal markers come from the richest source row (row 1: 2 non-null fields).
    assert out[0]["__chunk_index"] == 1
    assert any("2 rows → 1 entities" in n for n in notes)


def test_llm_failure_returns_rows_unchanged(monkeypatch):
    rows = [{"name": "A"}, {"name": "B"}]
    monkeypatch.setattr("openai.OpenAI", _fake_openai(raise_exc=RuntimeError("api down")))
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert out == rows
    assert any("LLM call failed" in n for n in notes)


def test_unaccounted_rows_pass_through(monkeypatch):
    rows = [{"name": "A"}, {"name": "B"}]
    payload = {"entities": [{"row_indices": [0], "merged": {"name": "A"}}]}
    monkeypatch.setattr("openai.OpenAI", _fake_openai(payload))
    out, notes = entity_resolution(rows, PEOPLE, ["name"])
    assert len(out) == 2
    assert {"name": "B"} in out
    assert any("not in LLM response" in n for n in notes)
