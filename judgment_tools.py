"""Split judgment queues for workers and merge/validate their outputs.

split: divide a judgment_queue.jsonl into worker-sized chunk files.
merge: combine chunk*.judgments.jsonl files, validate against the queue,
       normalize absent memory fields to null, and write a merged judgments
       file promote.py --apply can consume, plus a human review file of
       NEW/CONFLICT rulings.

Usage:
  python judgment_tools.py split <queue.jsonl> <chunks_dir> [--size 8]
  python judgment_tools.py merge <judgments_dir> <queue.jsonl> <merged_out.jsonl> [--review <review.md>]
"""
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from promote import RULINGS  # single definition; promote.py is the contract's gate


def split(queue: Path, chunks_dir: Path, size: int) -> None:
    lines = [l for l in queue.read_text(encoding="utf-8").splitlines() if l.strip()]
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for i in range(math.ceil(len(lines) / size)):
        part = lines[i * size:(i + 1) * size]
        (chunks_dir / f"chunk{i + 1:02d}.jsonl").write_text("\n".join(part) + "\n", encoding="utf-8")
    print(f"{len(lines)} entries -> {math.ceil(len(lines) / size)} chunk(s) in {chunks_dir}")


def merge(judgments_dir: Path, queue: Path, out: Path, review: Path | None) -> int:
    merged, bad = [], []
    try:
        queue_atoms = [json.loads(l)["atom"] for l in queue.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"invalid queue: {exc}")
        return 1
    queue_set = set(queue_atoms)
    for f in sorted(judgments_dir.glob("chunk*.judgments.jsonl")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                assert isinstance(o.get("atom"), str) and o["atom"], "missing atom"
                assert o["atom"] in queue_set, f"unknown atom {o['atom']!r}"
                assert o.get("ruling") in RULINGS, f"bad ruling {o.get('ruling')!r}"
                # judges sometimes omit memory keys on NEW/DISCARD; absent means null
                o.setdefault("memory_file", None)
                o.setdefault("memory_quote", None)
                assert isinstance(o.get("atom_quote"), str) and o["atom_quote"].strip(), "missing or empty atom_quote"
                assert isinstance(o.get("note"), str) and o["note"].strip(), "missing or empty note"
                merged.append(o)
            except (AssertionError, json.JSONDecodeError) as e:
                bad.append(f"{f.name}:{i}: {e}")

    judged = Counter(o["atom"] for o in merged)
    missing = [a for a in queue_atoms if a not in judged]
    dupes = [a for a, n in judged.items() if n > 1]

    counts = Counter(o["ruling"] for o in merged)
    print(f"merged: {len(merged)} rulings | {dict(counts)}")
    print(f"malformed: {bad or 'none'} | missing: {missing or 'none'} | multiple rulings: {dupes or 'none'}")

    if bad or missing or dupes:
        return 1

    out.write_text("\n".join(json.dumps(o) for o in merged) + "\n", encoding="utf-8")
    if review is not None:
        lines = []
        for o in merged:
            if o["ruling"] in ("NEW", "CONFLICT"):
                versus = f" (vs {o['memory_file']})" if o.get("memory_file") else ""
                lines += [f"## {o['atom']} — {o['ruling']}{versus}",
                          f"note: {o.get('note', '')}",
                          f"atom_quote: {str(o.get('atom_quote', ''))[:200]}",
                          f"memory_quote: {str(o.get('memory_quote', ''))[:200]}", ""]
        review.write_text("\n".join(lines), encoding="utf-8")
        print(f"review file: {review} ({sum(1 for o in merged if o['ruling'] in ('NEW', 'CONFLICT'))} entries)")

    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("queue", type=Path); s.add_argument("chunks_dir", type=Path)
    s.add_argument("--size", type=int, default=8)
    m = sub.add_parser("merge")
    m.add_argument("judgments_dir", type=Path); m.add_argument("queue", type=Path)
    m.add_argument("out", type=Path); m.add_argument("--review", type=Path)
    a = p.parse_args()
    if a.cmd == "split":
        split(a.queue, a.chunks_dir, a.size)
        return 0
    return merge(a.judgments_dir, a.queue, a.out, a.review)


if __name__ == "__main__":
    sys.exit(main())
