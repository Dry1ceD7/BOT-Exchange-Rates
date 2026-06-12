#!/usr/bin/env python3
# Golden-master characterization tests. Any failure means user-visible output
# changed. Regenerate fixtures ONLY in a commit that explicitly declares and
# justifies the behavior change.
"""
tests/test_golden_master.py
---------------------------------------------------------------------------
Golden-master characterization lane for the two real write paths:

  1. LedgerEngine.process_batch on a deterministic one-month ledger
     (USD/EUR/GBP rows spanning a week with a Saturday, a Sunday, and the
     New Year's Day holiday) — locks every ExRate master-sheet cell, every
     injected IFS/XLOOKUP formula string verbatim, and the audit CSV shape.
  2. update_exrate_standalone (standard USD/EUR path, manual date range) —
     locks every cell of the rewritten standalone ExRate sheet.

The BOT API is mocked at the same seam the engine test suite uses; the
fixed rate/holiday dataset lives in tests/golden/build_fixtures.py. The
committed tests/golden/expected_*.json files are the frozen masters.

To regenerate after an INTENTIONAL behavior change:
    python tests/golden/build_fixtures.py --regen
and commit the new JSONs with a message declaring and justifying the change.
---------------------------------------------------------------------------
"""

import re

import pytest

from core.exrate_sheet import exrate_fixed_letters
from tests.golden.build_fixtures import (
    EXPECTED_FILES,
    load_expected,
    run_ledger_scenario,
    run_realistic_scenario,
    run_standalone_scenario,
)

# =========================================================================
#  SCENARIO FIXTURES (each real write path runs once per module)
# =========================================================================

@pytest.fixture(scope="module")
def ledger_result(tmp_path_factory):
    """Run the full process_batch ledger scenario once for this module."""
    return run_ledger_scenario(tmp_path_factory.mktemp("golden_ledger"))


@pytest.fixture(scope="module")
def standalone_result(tmp_path_factory):
    """Run the standalone USD/EUR ExRate scenario once for this module."""
    return run_standalone_scenario(tmp_path_factory.mktemp("golden_standalone"))


@pytest.fixture(scope="module")
def realistic_result(tmp_path_factory):
    """Run the 16-sheet production-shape ledger scenario once."""
    return run_realistic_scenario(tmp_path_factory.mktemp("golden_realistic"))


# =========================================================================
#  COMMITTED ARTIFACTS
# =========================================================================

class TestGoldenArtifactsCommitted:
    """The expected-output JSONs must exist as committed repo artifacts."""

    def test_all_expected_files_exist(self):
        missing = [str(p) for p in EXPECTED_FILES.values() if not p.is_file()]
        assert missing == [], (
            "Missing golden masters; generate them with "
            "'python tests/golden/build_fixtures.py --regen' and commit: "
            f"{missing}"
        )


# =========================================================================
#  LEDGER WRITE PATH (process_batch)
# =========================================================================

class TestGoldenLedger:
    """Characterization of the standard ledger batch write path."""

    def test_exrate_sheet_matches_golden(self, ledger_result):
        """Every ExRate cell — dates, exact rate strings, labels, formats."""
        assert ledger_result["exrate"] == load_expected("ledger_exrate")

    def test_ledger_formulas_match_golden(self, ledger_result):
        """Every injected EX Rate formula string, verbatim, plus the
        normalized Date cells and their number formats."""
        assert ledger_result["ledger"] == load_expected("ledger_formulas")

    def test_audit_csv_matches_golden(self, ledger_result):
        """Audit CSV header and full row shape (volatile timestamps
        excluded by the snapshot serializer)."""
        assert ledger_result["audit"] == load_expected("ledger_audit")

    def test_weekend_and_holiday_rows_carry_date_and_label_only(
        self, ledger_result,
    ):
        """Readable invariant on top of the frozen JSON: a weekend/holiday
        row has a Date and a label but EVERY rate cell blank — no rate is
        ever fabricated for a non-trading day (no carry-forward)."""
        labelled = [
            row for row in ledger_result["exrate"]["rows"] if row[-1]
        ]
        assert labelled, "expected weekend/holiday rows in the golden window"
        for row in labelled:
            assert row[0] is not None
            assert all(v is None for v in row[1:-1]), row


# =========================================================================
#  REALISTIC PRODUCTION-SHAPE LEDGER (16 sheets, dual Date columns)
# =========================================================================

class TestGoldenRealistic:
    """Characterization of the production workbook shape: 12 month tabs
    (NO | Date(invoice) | Thai detail | Cur | Date(export-entry) |
    EX Rate | Amount, header at row 3 under a Crystal-style preamble),
    a PI sheet, 'Exrate USD'/'Exrate EUR' historical tabs, and a
    pre-existing ExRate master with prior history."""

    def test_exrate_sheet_matches_golden(self, realistic_result):
        """Every ExRate cell — including the PRESERVED pre-existing
        history row and the appended GBP column."""
        assert realistic_result["exrate"] == load_expected(
            "realistic_exrate"
        )

    def test_ledger_tabs_match_golden(self, realistic_result):
        """Every month tab: injected formulas verbatim, normalized
        export-entry dates, untouched invoice dates/Thai details, and
        the bounded max_row (last data row + buffer)."""
        assert realistic_result["ledger"] == load_expected(
            "realistic_formulas"
        )

    def test_audit_csv_matches_golden(self, realistic_result):
        assert realistic_result["audit"] == load_expected(
            "realistic_audit"
        )

    def test_skip_sheets_round_trip_untouched(self, realistic_result):
        """PI and the historical 'Exrate USD'/'Exrate EUR' tabs are
        outside the ledger scan — their content must survive verbatim."""
        passthrough = realistic_result["ledger"]["passthrough"]
        assert passthrough["PI"][0] == ["PI No", "Customer", "Value"]
        assert passthrough["Exrate USD"][0] == ["Date", "Rate"]
        assert passthrough["Exrate EUR"][0] == ["Date", "Rate"]
        # No formula/IFS leakage into any of them.
        for rows in passthrough.values():
            for row in rows:
                assert not any(
                    isinstance(v, str) and v.startswith("=") for v in row
                )

    def test_prior_master_history_row_preserved(self, realistic_result):
        """The pre-existing 2024-12-16 master row survives the run (the
        history-preservation contract) with its exact 4dp values."""
        rows = {row[0]: row for row in realistic_result["exrate"]["rows"]}
        assert rows["2024-12-16"][1:5] == [
            "33.9876", "34.2587", "35.3399", "36.0601",
        ]


# =========================================================================
#  FORMULA GRAMMAR PROPERTY (frozen AND fresh payloads)
# =========================================================================

# One guarded lookup: IFERROR(IF(<lookup>="","",<lookup>),"") with the SAME
# lookup repeated verbatim. XLOOKUP arguments never contain parentheses, so
# [^()]* is exact.
_XLOOKUP_RE = re.compile(r"_xlfn\.XLOOKUP\([^()]*\)")
_GUARDED_RE = re.compile(
    r'IFERROR\(IF\((_xlfn\.XLOOKUP\([^()]*\))="","",'
    r'(_xlfn\.XLOOKUP\([^()]*\))\),""\)'
)
# round-11: WHOLE-COLUMN references ($A:$A) are the formula contract —
# row-pinned $A$2:$A$N ranges went stale when the master grew without
# re-injection. The regex deliberately rejects the pinned shape so a
# regression to row-pinned ranges fails the allowlist below.
_EXRATE_RANGE_RE = re.compile(r"ExRate!\$([A-Z]{1,3}):\$([A-Z]{1,3})")
_LOCAL_REF_RE = re.compile(r"\b([A-Z]{1,3})(\d+)\b")


class TestGoldenFormulaGrammar:
    """Property-style invariants over EVERY formula in both the FROZEN and
    the FRESHLY-GENERATED ledger payloads. A `--regen` re-freezes whatever
    the code currently emits (the mechanism that once froze the
    single-guard 0-render bug); these checks hold regardless of
    regeneration:

      (a) every _xlfn.XLOOKUP is double-guarded
          IFERROR(IF(<lookup>="","",<lookup>),"") — blank-never-0;
      (b) every A1-style reference resolves to the row's OWN source-date /
          Cur columns or to the allowed ExRate columns (lookup key A +
          the rate-type fixed letters + appended extra-currency letters)
          — no formula can silently bind a foreign column again.
    """

    # buying_transfer is the pinned golden rate type; GBP is the single
    # appended extra currency (first extra slot -> column F).
    _ALLOWED_EXRATE = (
        {"A"}
        | set(exrate_fixed_letters("buying_transfer").values())
        | {"F"}
    )

    def _assert_grammar(self, formula, row, date_letter, cur_letter):
        # (a) double-guarded lookups, condition == result, none unguarded.
        guards = _GUARDED_RE.findall(formula)
        assert guards, formula
        for cond, result in guards:
            assert cond == result, formula
        n_lookups = len(_XLOOKUP_RE.findall(formula))
        assert n_lookups == 2 * len(guards), formula
        assert "_xlfn.XLOOKUP" not in _GUARDED_RE.sub("", formula), formula

        # (b) reference allowlist.
        exrate_letters: set[str] = set()

        def _collect(match):
            exrate_letters.update((match.group(1), match.group(2)))
            return ""

        remainder = _EXRATE_RANGE_RE.sub(_collect, formula)
        assert exrate_letters <= self._ALLOWED_EXRATE, formula
        local_refs = set(_LOCAL_REF_RE.findall(remainder))
        assert local_refs <= {
            (date_letter, str(row)), (cur_letter, str(row)),
        }, formula

    def _check_simple_payload(self, payload):
        formulas = [
            (row["row"], row["formula"])
            for row in payload["rows"] if row["formula"]
        ]
        assert formulas, "golden ledger payload carries no formulas"
        for row, formula in formulas:
            # Simple golden layout: A=Date, B=Cur.
            self._assert_grammar(formula, row, "A", "B")

    def _check_realistic_payload(self, payload):
        formulas = [
            (row["row"], row["formula"])
            for tab in payload["tabs"].values()
            for row in tab["rows"] if row["formula"]
        ]
        assert formulas, "realistic payload carries no formulas"
        for row, formula in formulas:
            # Production layout: E=export-entry Date, D=Cur.
            self._assert_grammar(formula, row, "E", "D")

    def test_frozen_ledger_formulas_grammar(self):
        self._check_simple_payload(load_expected("ledger_formulas"))

    def test_fresh_ledger_formulas_grammar(self, ledger_result):
        self._check_simple_payload(ledger_result["ledger"])

    def test_frozen_realistic_formulas_grammar(self):
        self._check_realistic_payload(load_expected("realistic_formulas"))

    def test_fresh_realistic_formulas_grammar(self, realistic_result):
        self._check_realistic_payload(realistic_result["ledger"])


# =========================================================================
#  STANDALONE EXRATE WRITE PATH (update_exrate_standalone)
# =========================================================================

class TestGoldenStandalone:
    """Characterization of the standard USD/EUR standalone updater."""

    def test_exrate_sheet_matches_golden(self, standalone_result):
        assert standalone_result["exrate"] == load_expected(
            "standalone_exrate"
        )
