"""Fixed synthetic passage-precision evaluator; never reads a real corpus."""

from pathlib import Path
import argparse
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context_slice.engine import Engine


def evaluate() -> dict:
    with tempfile.TemporaryDirectory(prefix="context-slice-quality-") as directory:
        root = Path(directory) / "notes"
        (root / "docs").mkdir(parents=True)
        (root / "notes").mkdir()
        for index in range(230):
            (root / "docs" / f"artifact-{index}.md").write_text(
                "# Session artifacts\n\nCurrent session evidence:\n"
                "`C:\\Archive\\copilot\\session\\performance\\result.json`\n"
                "`C:\\Archive\\rotation\\policy\\result.json`\n"
                "`C:\\Archive\\\u4e0a\u4e0b\u6587\\\u590d\u7528\\result.json`\n",
                encoding="utf-8", newline="\n",
            )
        fixtures = {
            "notes/session.md": (
                "# Session acceleration\n"
                "Copilot calls and performance benefit from batching verified local reads.\n"
                "The exact decision is keep-evidence-while-batching.\n"
            ),
            "notes/rotation.md": (
                "# Maintenance\n"
                "The rotation policy keeps the previous slot until health is confirmed.\n"
            ),
            "notes/context.md": (
                "# \u4f1a\u8bdd\u52a0\u901f\n"
                "\u901a\u8fc7\u63a7\u5236\u4e0a\u4e0b\u6587\u5927\u5c0f\u548c\u590d\u7528"
                "\u5df2\u9a8c\u8bc1\u6765\u6e90\uff0c\u53ef\u4ee5\u964d\u4f4e\u6210\u672c\u3002\n"
            ),
            "notes/opaqueidentifier.md": "# Inventory\nThe fixture value is retained.\n",
        }
        for relative, text in fixtures.items():
            (root / relative).write_text(text, encoding="utf-8", newline="\n")
        cases = (
            ("Copilot session performance", "notes/session.md", "keep-evidence-while-batching"),
            ("rotation policy", "notes/rotation.md", "previous slot"),
            ("\u4e0a\u4e0b\u6587 \u590d\u7528", "notes/context.md", "\u964d\u4f4e\u6210\u672c"),
            ("opaqueidentifier", "notes/opaqueidentifier.md", "fixture value"),
        )
        results = []
        with Engine(root, Path(directory) / "state") as engine:
            for query, expected, fact in cases:
                result = engine.brief(query, limit=1, max_bytes=4096)
                correct = bool(result["results"]) and (
                    result["results"][0]["path"] == expected
                    and fact in result["results"][0]["text"]
                )
                results.append({"query": query, "correct": correct})
        count = sum(item["correct"] for item in results)
        return {
            "fixture": "path-noise-and-passage-v1",
            "cases": len(cases),
            "correct_top1_with_fact": count,
            "precision": count / len(cases),
            "results": results,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-perfect", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    raise SystemExit(1 if args.require_perfect and report["precision"] != 1 else 0)
