import pytest

from app.services.ddl import build_ddl, build_ddl_with_notes, validate_model
from tests.factories import make_column, make_model, make_rel, make_table


def _invoice_model():
    companies = make_table("Companies", [make_column("Company Name", pk=True)])
    invoices = make_table(
        "Invoices",
        [
            make_column("Invoice Number", pk=True),
            make_column("Company Name"),
            make_column("Total", col_type="float"),
        ],
    )
    rel = make_rel("Invoices", "Company Name", "Companies", "Company Name")
    return make_model([companies, invoices], [rel])


class TestValidateModel:
    def test_snake_cases_identifiers(self):
        result = validate_model(_invoice_model())
        names = [t.table_name for t in result.model.tables]
        assert names == ["companies", "invoices"]
        assert result.model.tables[1].columns[0].name == "invoice_number"

    def test_reserved_word_table_rejected(self):
        model = make_model([make_table("select", [make_column("a")])])
        with pytest.raises(ValueError, match="reserved word"):
            validate_model(model)

    def test_reserved_internal_column_rejected(self):
        model = make_model([make_table("t", [make_column("id")])])
        with pytest.raises(ValueError, match="reserved provenance"):
            validate_model(model)

    def test_duplicate_column_rejected(self):
        model = make_model([make_table("t", [make_column("a"), make_column("A")])])
        with pytest.raises(ValueError, match="duplicate column"):
            validate_model(model)

    def test_relationship_to_non_pk_column_downgraded(self):
        parent = make_table("parent", [make_column("name")])  # NOT a pk
        child = make_table("child", [make_column("cname", pk=True), make_column("name")])
        rel = make_rel("child", "name", "parent", "name")
        result = validate_model(make_model([parent, child], [rel]))
        assert result.model.relationships[0].enabled is False
        assert any("not is_primary_key" in n for n in result.notes)

    def test_relationship_to_missing_table_downgraded(self):
        child = make_table("child", [make_column("cname", pk=True), make_column("pid")])
        rel = make_rel("child", "pid", "nowhere", "pid")
        result = validate_model(make_model([child], [rel]))
        assert result.model.relationships[0].enabled is False


class TestBuildDdl:
    def test_create_table_shape(self):
        stmts = build_ddl(_invoice_model(), "job_abc")
        create_invoices = next(s for s in stmts if '"invoices"' in s and s.startswith("CREATE"))
        assert '"id" BIGSERIAL PRIMARY KEY' in create_invoices
        assert '"total" NUMERIC' in create_invoices
        for prov in ("source_page_numbers", "extraction_confidence", "reconciliation_notes"):
            assert prov in create_invoices
        assert create_invoices.startswith('CREATE TABLE "job_abc".')

    def test_unique_constraints_for_pk_columns(self):
        stmts = build_ddl(_invoice_model(), "job_abc")
        assert any('"uq_companies_company_name" UNIQUE' in s for s in stmts)
        assert any('"uq_invoices_invoice_number" UNIQUE' in s for s in stmts)

    def test_fk_emitted_only_for_enabled_relationships(self):
        stmts = build_ddl(_invoice_model(), "job_abc")
        assert any("FOREIGN KEY" in s for s in stmts)

        model = _invoice_model()
        model.relationships[0].enabled = False
        stmts_disabled = build_ddl(model, "job_abc")
        assert not any("FOREIGN KEY" in s for s in stmts_disabled)

    def test_output_is_byte_deterministic(self):
        assert build_ddl(_invoice_model(), "job_abc") == build_ddl(_invoice_model(), "job_abc")

    def test_build_ddl_with_notes_returns_all_artifacts(self):
        stmts, normalized, notes = build_ddl_with_notes(_invoice_model(), "job_abc")
        assert stmts
        assert normalized.tables[0].table_name == "companies"
        assert notes == []
