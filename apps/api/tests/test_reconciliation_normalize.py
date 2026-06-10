from app.services.reconciliation import normalize_row, normalize_value
from tests.factories import make_column, make_table


class TestNullish:
    def test_none_passes_through(self):
        assert normalize_value(None, "string") is None

    def test_blank_string_is_none(self):
        assert normalize_value("   ", "integer") is None

    def test_llm_null_literals_are_none(self):
        for literal in ("null", "None", "N/A", "na", "undefined"):
            assert normalize_value(literal, "string") is None


class TestString:
    def test_strips_and_nfc_normalizes(self):
        assert normalize_value("  Acme Corp  ", "string") == "Acme Corp"
        # Decomposed e + combining acute must collapse to the precomposed é.
        assert normalize_value("Café", "string") == "Café"

    def test_non_string_coerced(self):
        assert normalize_value(42, "string") == "42"


class TestDate:
    def test_fuzzy_date_parses_to_isoformat(self):
        assert normalize_value("Jan 5, 2024", "date") == "2024-01-05"

    def test_unparseable_date_is_none(self):
        assert normalize_value("not a date at all", "date") is None

    def test_non_string_date_is_none(self):
        assert normalize_value(20240105, "date") is None


class TestInteger:
    def test_bool_to_int(self):
        assert normalize_value(True, "integer") == 1

    def test_float_truncates(self):
        assert normalize_value(3.9, "integer") == 3

    def test_currency_and_thousands_stripped(self):
        assert normalize_value("$1,234", "integer") == 1234

    def test_ascii_negative_preserved(self):
        assert normalize_value("-500", "integer") == -500

    def test_unicode_minus_preserved(self):
        # − is the unicode minus sign LLMs sometimes emit.
        assert normalize_value("−500", "integer") == -500

    def test_garbage_is_none(self):
        assert normalize_value("abc", "integer") is None


class TestFloat:
    def test_currency_string(self):
        assert normalize_value("€1,234.56", "float") == 1234.56

    def test_unicode_minus_preserved(self):
        assert normalize_value("−1,234.50", "float") == -1234.5

    def test_garbage_is_none(self):
        assert normalize_value("n/a-ish", "float") is None


class TestBoolean:
    def test_truthy_strings(self):
        for s in ("true", "T", "yes", "Y", "1"):
            assert normalize_value(s, "boolean") is True

    def test_falsy_strings(self):
        for s in ("false", "F", "no", "N", "0"):
            assert normalize_value(s, "boolean") is False

    def test_numeric_coercion(self):
        assert normalize_value(0, "boolean") is False
        assert normalize_value(1.0, "boolean") is True

    def test_ambiguous_is_none(self):
        assert normalize_value("maybe", "boolean") is None


def test_unknown_type_passes_through():
    assert normalize_value("anything", "jsonb") == "anything"


def test_normalize_row_touches_only_declared_columns():
    table = make_table(
        "items",
        [make_column("qty", col_type="integer"), make_column("name")],
    )
    row = {"qty": "1,000", "name": "  Widget ", "__chunk_index": 3, "extra": " raw "}
    out = normalize_row(row, table)
    assert out["qty"] == 1000
    assert out["name"] == "Widget"
    assert out["__chunk_index"] == 3
    assert out["extra"] == " raw "  # undeclared key untouched
