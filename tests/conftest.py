from __future__ import annotations

import pytest

from tamperlint.synth import benign, forge
from tamperlint.synth.genuine import make_invoice, make_statement


@pytest.fixture(scope="session")
def corpus() -> dict[str, bytes]:
    """A small labelled corpus built once per test session."""
    statement, truth = make_statement(1)
    invoice, invoice_truth = make_invoice(2)
    signed = benign.sign(statement)
    return {
        # genuine and benign-but-edited
        "genuine": statement,
        "linearized": benign.linearize(statement),
        "resaved": benign.full_resave(statement),
        "sticky_note": benign.add_sticky_note(statement),
        "signed": signed,
        "signed_then_note": benign.add_sticky_note(signed),
        "invoice_genuine": invoice,
        # forged
        "overlay_edit": forge.statement_amount_edit(statement, truth, 3, 99999.0),
        "overlay_recalc": forge.statement_amount_edit(
            statement, truth, 3, 99999.0, recalc_balances=True
        ),
        "overlay_touchup_ilovepdf": forge.statement_amount_edit(
            statement, truth, 3, 99999.0, touchup=True, producer="iLovePDF"
        ),
        "overlay_shift_flattened": benign.full_resave(
            forge.statement_amount_edit(statement, truth, 3, 99999.0, baseline_shift=1.2)
        ),
        "signed_then_edited": forge.statement_amount_edit(signed, truth, 3, 99999.0),
        "invoice_total_rebuilt": make_invoice(2, total_override=invoice_truth.total + 5000)[0],
        # known limitation: rebuilt from scratch with consistent numbers
        "statement_rebuilt_edit": make_statement(1, overrides={3: {"credit": 99999.0}})[0],
    }
