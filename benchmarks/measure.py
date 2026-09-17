"""Synthetic evidence-preserving retrieval benchmark; no private corpus input."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_corpus(root: Path, count: int) -> None:
    root.mkdir()
    filler = (
        "Routine background information describes a different component. "
        "Keep the historical account available, but retrieve it only when "
        "it contributes evidence to the current question.\n"
    )
    for index in range(count):
        sections = [f"# Synthetic note {index}\n"]
        for section in range(12):
            sections.append(f"\n## Background {section}\n" + filler * 8)
            if section == 5:
                sections.append(
                    f"\n## Exact answer\n"
                    f"needle{index:06d}: answer{index:06d}; "
                    "retain this exact fact and its source line.\n"
                )
        (root / f"note-{index:06d}.md").write_text(
            "".join(sections), encoding="utf-8"
        )


def baseline(root: Path, query: str) -> dict:
    results = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if query in text:
            results.append({"path": path.name, "content": text})
    return {"results": results}


def wire_size(value: dict) -> int:
    return len((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))


def run(count: int, repeat: int, baseline_only: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix="context-slice-benchmark-") as directory:
        root = Path(directory) / "notes"
        make_corpus(root, count)
        indices = [(37 + trial * 97) % count for trial in range(repeat)]
        baseline_ms, baseline_bytes = [], []
        for index in indices:
            start = time.perf_counter()
            result = baseline(root, f"needle{index:06d}")
            baseline_ms.append((time.perf_counter() - start) * 1000)
            assert len(result["results"]) == 1
            assert f"answer{index:06d}" in json.dumps(result)
            baseline_bytes.append(wire_size(result))
        report = {
            "fixture": "synthetic-markdown-v1",
            "documents": count,
            "repeat": repeat,
            "baseline_median_ms": round(statistics.median(baseline_ms), 3),
            "baseline_median_bytes": statistics.median(baseline_bytes),
            "provider_tokens": "not measured",
            "timing_scope": "in-process retrieval; CLI startup is not included",
        }
        if baseline_only:
            return report

        from context_slice.engine import Engine

        start = time.perf_counter()
        with Engine(root, Path(directory) / "state") as engine:
            cold = engine.refresh()
            cold_ms = (time.perf_counter() - start) * 1000
            warm_ms, output_bytes = [], []
            for index in indices:
                start = time.perf_counter()
                result = engine.brief(f"needle{index:06d}", limit=1, max_bytes=4096)
                warm_ms.append((time.perf_counter() - start) * 1000)
                assert result["results"], "The planted fact was not retrieved"
                assert f"answer{index:06d}" in json.dumps(result), "Evidence was lost"
                assert wire_size(result) <= 4096, "The wire budget was exceeded"
                assert result["refresh"]["read_files"] == 0, "Warm refresh re-read unchanged documents"
                output_bytes.append(wire_size(result))

        baseline_latency = statistics.median(baseline_ms)
        warm_latency = statistics.median(warm_ms)
        latency_gain = 1 - warm_latency / baseline_latency
        byte_gain = 1 - statistics.median(output_bytes) / statistics.median(baseline_bytes)
        report.update(
            cold_index_ms=round(cold_ms, 3),
            cold_indexed_files=cold["read_files"],
            warm_median_ms=round(warm_latency, 3),
            warm_median_bytes=statistics.median(output_bytes),
            latency_reduction_pct=round(latency_gain * 100, 2),
            returned_byte_reduction_pct=round(byte_gain * 100, 2),
            break_even_queries=(
                math.ceil(cold_ms / (baseline_latency - warm_latency))
                if baseline_latency > warm_latency else None
            ),
            accepted=latency_gain >= 0.4 and byte_gain >= 0.8,
            correct_facts=repeat,
        )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1500)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 10 <= args.documents <= 10000 or not 3 <= args.repeat <= 30:
        parser.error("Use 10-10000 documents and 3-30 repetitions.")
    report = run(args.documents, args.repeat, args.baseline)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if args.baseline or report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
