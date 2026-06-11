# apps/api/tests/test_reconciliation_fk.py
from app.services.reconciliation import resolve_foreign_keys
from tests.factories import make_column, make_rel, make_table

COMPANIES = make_table("companies", [make_column("company_name", pk=True)])
CONTACTS = make_table("contacts", [make_column("contact_name", pk=True), make_column("company")])
TABLE_DEFS = {"companies": COMPANIES, "contacts": CONTACTS}
REL = make_rel("contacts", "company", "companies", "company_name")


def _tables(child_company_value):
    return {
        "companies": [{"company_name": "Acme Corp"}],
        "contacts": [{"contact_name": "Jane", "company": child_company_value}],
    }


def test_exact_match_normalizes_casing():
    tables = _tables("acme corp")
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    assert tables["contacts"][0]["company"] == "Acme Corp"
    assert "__notes" not in tables["contacts"][0]


def test_token_set_match_repairs_and_annotates():
    tables = _tables("Corp, Acme")
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    child = tables["contacts"][0]
    assert child["company"] == "Acme Corp"
    assert any("token-set match" in n for n in child["__notes"])


def test_no_match_annotates_without_rewriting():
    tables = _tables("Globex")
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    child = tables["contacts"][0]
    assert child["company"] == "Globex"
    assert any("no matching" in n for n in child["__notes"])


def test_null_fk_skipped():
    tables = _tables(None)
    resolve_foreign_keys(tables, TABLE_DEFS, [REL])
    assert tables["contacts"][0]["company"] is None
    assert "__notes" not in tables["contacts"][0]


def test_disabled_relationship_is_ignored():
    rel = make_rel("contacts", "company", "companies", "company_name", enabled=False)
    tables = _tables("Corp, Acme")
    resolve_foreign_keys(tables, TABLE_DEFS, [rel])
    assert tables["contacts"][0]["company"] == "Corp, Acme"


def test_non_string_fk_miss_annotates():
    companies = make_table("companies", [make_column("cid", col_type="integer", pk=True)])
    contacts = make_table(
        "contacts", [make_column("contact_name", pk=True), make_column("cid", col_type="integer")]
    )
    rel = make_rel("contacts", "cid", "companies", "cid")
    tables = {
        "companies": [{"cid": 1}],
        "contacts": [{"contact_name": "Jane", "cid": 99}],
    }
    resolve_foreign_keys(tables, {"companies": companies, "contacts": contacts}, [rel])
    assert any("no matching" in n for n in tables["contacts"][0]["__notes"])
