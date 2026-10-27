from __future__ import annotations

"""
Read-only production relevance inference parity audit.

Purpose
-------
Compare the implementation structure of:
  scripts/run_finbert_two_stage_finetune.py
  src/bank_intel/intelligence/relevance_engine.py

This is a READ-ONLY audit. It does not train, score the corpus, or modify files.

It checks the specific parity requirements established by the frozen trainer:
- overflow tokenization with max_length + stride
- spaced body-window sampling (beginning/middle/end), NOT first-N only
- max_body_chunks support
- token-count weighting of chunk logits
- headline/body logit weighting
- article-level logit aggregation BEFORE softmax
- label-order awareness
- local/offline checkpoint loading signals

Static inspection cannot prove numerical parity by itself. The script therefore
prints PASS / FAIL / MANUAL_REVIEW and source snippets for any suspicious item.
"""

import ast
import inspect
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRAINER = PROJECT_ROOT / "scripts" / "run_finbert_two_stage_finetune.py"
ENGINE = PROJECT_ROOT / "src" / "bank_intel" / "intelligence" / "relevance_engine.py"


def read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def snippet(text: str, pattern: str, context: int = 3) -> str:
    lines = text.splitlines()
    rx = re.compile(pattern, re.I)

    for i, line in enumerate(lines):
        if rx.search(line):
            lo = max(0, i - context)
            hi = min(len(lines), i + context + 1)
            return "\n".join(
                f"{j+1:5d}: {lines[j]}"
                for j in range(lo, hi)
            )

    return "(not found)"


def has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.I | re.S) is not None


def public_symbols(path: Path) -> list[str]:
    tree = ast.parse(read(path))
    out = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            out.append(f"function {node.name}{inspect.signature_placeholder if False else ''}")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, ast.FunctionDef)
                and not n.name.startswith("_")
            ]
            out.append(
                f"class {node.name} methods={methods}"
            )

    return out


def main() -> None:
    trainer = read(TRAINER)
    engine = read(ENGINE)

    checks = []

    # 1. Overflow tokenization.
    trainer_overflow = (
        has(trainer, r"return_overflowing_tokens\s*=\s*True")
        and has(trainer, r"\bstride\s*=")
        and has(trainer, r"\bmax_length\s*=")
    )
    engine_overflow = (
        has(engine, r"return_overflowing_tokens\s*=\s*True")
        and has(engine, r"\bstride\s*=")
        and has(engine, r"\bmax_length\s*=")
    )
    checks.append(
        (
            "Overflow tokenization + stride",
            "PASS" if trainer_overflow and engine_overflow else "FAIL",
            engine_overflow,
        )
    )

    # 2. Spaced indices.
    trainer_spaced = has(trainer, r"def\s+spaced_indices\b") or has(
        trainer, r"\bspaced_indices\s*\("
    )
    engine_spaced = has(engine, r"def\s+spaced_indices\b") or has(
        engine, r"\bspaced_indices\s*\("
    )

    suspicious_first_n = (
        has(engine, r"\[\s*:\s*max_body_chunks\s*\]")
        or has(engine, r"\[\s*0\s*:\s*max_body_chunks\s*\]")
        or has(engine, r"chunks\s*\[\s*:\s*[A-Za-z_]*max")
    )

    if trainer_spaced and engine_spaced and not suspicious_first_n:
        spaced_status = "PASS"
    elif suspicious_first_n or (trainer_spaced and not engine_spaced):
        spaced_status = "FAIL"
    else:
        spaced_status = "MANUAL_REVIEW"

    checks.append(
        (
            "Spaced body-window selection",
            spaced_status,
            engine_spaced,
        )
    )

    # 3. max_body_chunks.
    checks.append(
        (
            "max_body_chunks implemented",
            "PASS" if has(engine, r"\bmax_body_chunks\b") else "FAIL",
            has(engine, r"\bmax_body_chunks\b"),
        )
    )

    # 4. Token-count weights.
    trainer_token_weight = (
        has(trainer, r"attention_mask")
        and has(trainer, r"\bsum\s*\(")
    )
    engine_token_weight = (
        has(engine, r"attention_mask")
        and has(engine, r"\bsum\s*\(")
    )
    checks.append(
        (
            "Token-count weighting of chunk logits",
            "PASS" if trainer_token_weight and engine_token_weight else "MANUAL_REVIEW",
            engine_token_weight,
        )
    )

    # 5. Headline/body weighting.
    engine_side_weights = (
        has(engine, r"headline_weight")
        and has(engine, r"body_weight")
    )
    checks.append(
        (
            "Headline/body weighted article logits",
            "PASS" if engine_side_weights else "FAIL",
            engine_side_weights,
        )
    )

    # 6. Softmax placement. Static heuristic:
    # We expect chunk/article logits to be aggregated first, then softmax.
    softmax_positions = [
        m.start()
        for m in re.finditer(r"softmax\s*\(", engine, flags=re.I)
    ]

    aggregation_positions = [
        m.start()
        for m in re.finditer(
            r"(weighted|aggregate|article_logits|combined_logits|pooled_logits)",
            engine,
            flags=re.I,
        )
    ]

    if not softmax_positions:
        softmax_status = "MANUAL_REVIEW"
    elif aggregation_positions and min(softmax_positions) > min(aggregation_positions):
        softmax_status = "PASS"
    else:
        softmax_status = "MANUAL_REVIEW"

    checks.append(
        (
            "Softmax after article-level logit aggregation",
            softmax_status,
            bool(softmax_positions),
        )
    )

    # 7. Label order.
    label_order_ok = (
        has(engine, r"label_order")
        or has(engine, r"\[\s*[\"']WATCH[\"']\s*,\s*[\"']RELEVANT[\"']\s*\]")
        or has(engine, r"\[\s*[\"']NOISE[\"']\s*,\s*[\"']KEEP[\"']\s*\]")
    )
    checks.append(
        (
            "Explicit label-order handling",
            "PASS" if label_order_ok else "MANUAL_REVIEW",
            label_order_ok,
        )
    )

    # 8. Offline/local loading.
    local_ok = has(engine, r"local_files_only\s*=\s*True")
    checks.append(
        (
            "Local/offline checkpoint loading",
            "PASS" if local_ok else "MANUAL_REVIEW",
            local_ok,
        )
    )

    print("=" * 100)
    print("PRODUCTION RELEVANCE INFERENCE PARITY AUDIT")
    print("=" * 100)
    print(f"Trainer : {TRAINER}")
    print(f"Engine  : {ENGINE}")
    print()

    fail_count = 0
    manual_count = 0

    for name, status, _ in checks:
        print(f"{status:<14} {name}")
        if status == "FAIL":
            fail_count += 1
        elif status == "MANUAL_REVIEW":
            manual_count += 1

    print()
    print("CRITICAL DETAILS")
    print("-" * 100)

    print("\n[spaced_indices / body selection]")
    print(snippet(engine, r"spaced_indices|max_body_chunks|\[\s*:\s*max_body_chunks", 5))

    print("\n[token weighting]")
    print(snippet(engine, r"attention_mask|token_weight|weights", 5))

    print("\n[headline/body aggregation]")
    print(snippet(engine, r"headline_weight|body_weight|combined_logits|article_logits", 6))

    print("\n[softmax]")
    print(snippet(engine, r"softmax", 6))

    print("\n[public engine symbols]")
    try:
        for item in public_symbols(ENGINE):
            print(" ", item)
    except Exception as exc:
        print("  Unable to parse public symbols:", exc)

    print()
    print("SUMMARY")
    print("-" * 100)

    if fail_count:
        print(
            f"PARITY STATUS : FAIL ({fail_count} critical/static failure(s), "
            f"{manual_count} manual-review item(s))"
        )
        print(
            "Do NOT claim production/trainer parity yet. Patch relevance_engine.py "
            "before production scoring."
        )
    elif manual_count:
        print(
            f"PARITY STATUS : MANUAL REVIEW REQUIRED "
            f"({manual_count} unresolved item(s))"
        )
        print(
            "No obvious critical mismatch was proven, but numerical parity is not "
            "yet established."
        )
    else:
        print("PARITY STATUS : STATIC CHECK PASS")
        print(
            "Static structure matches the trainer requirements. A numerical parity "
            "test is still required before final production sign-off."
        )


if __name__ == "__main__":
    main()
