"""Verify each staged atom's **Evidence:** quote appears in the transcript extracts.

An atom whose quote can't be found (after whitespace normalization) is flagged —
either the distiller paraphrased too loosely or invented the fact.

Usage: python verify_evidence.py <atoms_dir> <extracts_dir>
"""
import re
import sys
from pathlib import Path

MIN_SEGMENT = 20  # ignore quote fragments shorter than this


def norm(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = s.replace("*", "")  # markdown emphasis differs between quote and corpus
    return re.sub(r"\s+", " ", s).strip().lower()


def main(atoms_dir: Path, extracts_dir: Path) -> None:
    corpus = norm(" ".join(p.read_text(encoding="utf-8")
                           for p in extracts_dir.glob("*.txt")))
    ok, miss, no_evidence = [], [], []
    for atom in sorted(atoms_dir.rglob("*.md")):
        text = atom.read_text(encoding="utf-8")
        m = re.search(r"\*\*Evidence:\*\*\s*(.+?)(?:\n\*\*|\n---|\Z)", text, re.DOTALL)
        if not m:
            no_evidence.append(atom)
            continue
        evidence = m.group(1).replace("“", '"').replace("”", '"')
        # evidence may be several quoted spans joined by prose ("..." and "...");
        # extract each span, split spans on ellipses, verify the longest segment
        spans = re.findall(r'"([^"]+)"', evidence) or [evidence.strip().strip('"')]
        segments = [seg for span in spans
                    for seg in re.split(r"\.\.\.|…", span)
                    if len(norm(seg)) >= MIN_SEGMENT]
        target = max(segments, key=lambda s: len(norm(s)), default=evidence)
        (ok if norm(target) in corpus else miss).append(atom)

    rel = lambda p: f"{p.parent.name}/{p.name}"
    print(f"atoms checked: {len(ok) + len(miss) + len(no_evidence)}")
    print(f"  quote found in corpus : {len(ok)}")
    print(f"  quote NOT found       : {len(miss)}")
    print(f"  no evidence line      : {len(no_evidence)}")
    for p in miss:
        print(f"  MISS: {rel(p)}")
    for p in no_evidence:
        print(f"  NO-EVIDENCE: {rel(p)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
