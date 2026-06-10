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

import pytest

from tests.golden.build_fixtures import (
    EXPECTED_FILES,
    load_expected,
    run_ledger_scenario,
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
#  STANDALONE EXRATE WRITE PATH (update_exrate_standalone)
# =========================================================================

class TestGoldenStandalone:
    """Characterization of the standard USD/EUR standalone updater."""

    def test_exrate_sheet_matches_golden(self, standalone_result):
        assert standalone_result["exrate"] == load_expected(
            "standalone_exrate"
        )
