"""Run tamperlint over a folder of real documents and summarise what it reports.

    python evals/run_corpus.py path/to/folder results.jsonl --workers 6
    python evals/run_corpus.py --summary results.jsonl

Each file is analysed in a worker process with a time limit, so one pathological file cannot
stall the run; a crashed or timed-out worker is replaced. Results are appended as one JSON line
per file, and an interrupted run resumes where it stopped. Use it on documents you are allowed
to process: nothing leaves the machine, and the results hold rule IDs and short messages only.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import statistics
import time
from collections import Counter
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any


def analyse(path_str: str) -> dict[str, Any]:
    from tamperlint import check
    from tamperlint.util import quiet_parser_logs

    quiet_parser_logs()
    path = Path(path_str)
    record: dict[str, Any] = {"file": path.name, "bytes": path.stat().st_size}
    start = time.perf_counter()
    try:
        report = check(path.read_bytes(), name=path.name)
        record.update(
            verdict=report.verdict.value,
            score=report.score,
            doc_type=report.doc_type,
            pages=report.file.pages,
            findings=[
                {"rule": f.rule_id, "severity": f.severity.value, "message": f.message[:300]}
                for f in report.findings
            ],
            limitations=report.limitations,
        )
    except Exception as exc:  # unreadable, encrypted or unsupported files are results too
        record.update(verdict="ERROR", error=f"{type(exc).__name__}: {exc}"[:300])
    record["seconds"] = round(time.perf_counter() - start, 2)
    return record


def _worker(conn: Connection) -> None:
    while (path := conn.recv()) is not None:
        conn.send(analyse(path))


class _Slot:
    def __init__(self) -> None:
        self.conn, child = mp.Pipe()
        self.proc = mp.Process(target=_worker, args=(child,), daemon=True)
        self.proc.start()
        self.file: Path | None = None
        self.started = 0.0
        self.served = 0

    def give(self, path: Path) -> None:
        self.file, self.started = path, time.time()
        self.conn.send(str(path))

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.join(5)


def _step(slot: _Slot, timeout: float, write: Any) -> _Slot:
    """Collect a finished result, or replace a worker that died or ran out of time."""
    if slot.file is None:
        return slot
    name = slot.file.name
    if slot.conn.poll():
        try:
            write(slot.conn.recv())
        except (EOFError, OSError) as exc:
            write({"file": name, "verdict": "ERROR", "error": f"worker died: {exc}"})
            slot.stop()
            return _Slot()
        slot.file, slot.served = None, slot.served + 1
        if slot.served >= 25:  # recycle workers to bound memory
            slot.conn.send(None)
            return _Slot()
    elif not slot.proc.is_alive():
        write({"file": name, "verdict": "ERROR", "error": "worker crashed"})
        return _Slot()
    elif time.time() - slot.started > timeout:
        write({"file": name, "verdict": "TIMEOUT", "seconds": timeout})
        slot.stop()
        return _Slot()
    return slot


def run(folder: Path, out: Path, workers: int, timeout: float) -> None:
    files = sorted(p for p in folder.iterdir() if p.is_file())
    done = set()
    if out.exists():
        done = {json.loads(line)["file"] for line in out.read_text(encoding="utf-8").splitlines()}
    todo = [p for p in files if p.name not in done]
    print(f"{len(files)} files, {len(todo)} to analyse with {workers} workers")
    slots = [_Slot() for _ in range(workers)]
    with out.open("a", encoding="utf-8") as fh:

        def write(record: dict[str, Any]) -> None:
            fh.write(json.dumps(record) + "\n")
            fh.flush()

        while todo or any(s.file for s in slots):
            for i in range(len(slots)):
                slots[i] = _step(slots[i], timeout, write)
                if slots[i].file is None and todo:
                    slots[i].give(todo.pop(0))
            time.sleep(0.05)
    for slot in slots:
        slot.stop()


def summary(results: Path) -> None:
    records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    verdicts = Counter(r["verdict"] for r in records)
    print(f"{len(records)} files")
    for verdict, n in verdicts.most_common():
        print(f"  {verdict:13s} {n:5d}  {n / len(records):6.1%}")
    seconds = sorted(r["seconds"] for r in records if "seconds" in r)
    if seconds:
        p95 = seconds[int(0.95 * (len(seconds) - 1))]
        print(f"  median {statistics.median(seconds):.1f} s, 95th percentile {p95:.1f} s per file")
    for verdict in ("SUSPICIOUS", "INCONCLUSIVE"):
        rules: Counter[str] = Counter()
        for r in records:
            if r["verdict"] == verdict:
                rules.update({f["rule"] for f in r.get("findings", []) if _decisive(f)})
        if rules:
            listed = ", ".join(f"{rule} {n}" for rule, n in rules.most_common())
            print(f"  rules behind {verdict}: {listed}")


def _decisive(finding: dict[str, Any]) -> bool:
    """High and medium findings are the ones that set a verdict."""
    return finding.get("severity", finding.get("sev")) in ("high", "medium")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", type=Path, nargs="?")
    parser.add_argument("out", type=Path, nargs="?", default=Path("corpus-results.jsonl"))
    parser.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 2) - 2))
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds per file")
    parser.add_argument("--summary", type=Path, help="summarise an existing results file")
    args = parser.parse_args()
    if args.summary:
        summary(args.summary)
    elif args.folder:
        run(args.folder, args.out, args.workers, args.timeout)
        summary(args.out)
    else:
        parser.error("give a folder to analyse or --summary RESULTS")


if __name__ == "__main__":
    main()
