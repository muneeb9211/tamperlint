"""A handful of ready-made SPECIMEN documents for demos and the quick start."""

from __future__ import annotations

from pathlib import Path

from tamperlint.synth import benign, forge, raster
from tamperlint.synth.genuine import make_invoice, make_statement


def build_samples(seed: int = 1) -> dict[str, bytes]:
    statement, truth = make_statement(seed)
    invoice, invoice_truth = make_invoice(seed)
    signed = benign.sign(statement, signer=f"{truth.bank} (SPECIMEN)")
    gray = raster.decode(raster.jpeg(raster.scan(raster.render(statement, dpi=150), seed=seed), 80))
    w = gray.shape[1]
    scale = 150 / 72

    def row_top(i: int) -> int:  # 12 pt above the row's baseline, in scan pixels
        return int((truth.page_height - truth.rows[i].cells["balance"].y - 12) * scale)

    def row_bottom(i: int) -> int:  # 6 pt below the baseline: rows are 18 pt apart
        return int((truth.page_height - truth.rows[i].cells["balance"].y + 6) * scale)

    # Debit rows below the first few, so every edit lands mid-table.
    first, second = [i for i, row in enumerate(truth.rows) if row.debit and i >= 3][:2]
    return {
        "statement_genuine.pdf": statement,
        "statement_signed.pdf": signed,
        "statement_edited.pdf": forge.statement_amount_edit(statement, truth, first, 99_999.0),
        # A careful forger hides a large expense and fixes every later balance, so the account
        # looks better funded and the arithmetic still adds up.
        "statement_edited_flattened.pdf": benign.full_resave(
            forge.statement_amount_edit(
                statement,
                truth,
                first,
                round(truth.rows[first].debit / 10, 2),
                recalc_balances=True,
                baseline_shift=1.0,
            )
        ),
        "statement_signed_then_edited.pdf": forge.statement_amount_edit(
            signed, truth, second, 1_250.0
        ),
        "invoice_genuine.pdf": invoice,
        "invoice_total_inflated.pdf": make_invoice(
            seed, total_override=invoice_truth.total + 5_000
        )[0],
        "scan_genuine.jpg": raster.jpeg(gray, 85),
        # three whole transaction rows pasted over three later rows: duplicated transactions
        "scan_rows_copied.jpg": raster.jpeg(
            raster.copy_move(gray, (80, row_top(3), w - 80, row_bottom(5)), (80, row_top(9))),
            90,
        ),
    }


def write_samples(directory: Path, seed: int = 1) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in build_samples(seed).items():
        path = directory / name
        path.write_bytes(data)
        paths.append(path)
    return paths
