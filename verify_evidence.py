"""Canonical evidence check: does an atom's **Evidence:** quote appear in the extracts?

An atom whose quote can't be found (after whitespace normalization) is flagged —
either the distiller paraphrased too loosely or invented the fact. promote.py
imports evidence_ok/build_corpus from here; this file is the single
implementation.

Usage: python verify_evidence.py <atoms_dir> <extracts_dir>
"""
import html
import re
import sys
from pathlib import Path

MIN_SEGMENT = 20  # ignore quote fragments shorter than this

EVIDENCE_RE = re.compile(r"\*\*Evidence:\*\*\s*(.+?)(?:\n\*\*|\n---|\Z)", re.DOTALL)


def norm(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = s.replace("*", "")  # markdown emphasis differs between quote and corpus
    return re.sub(r"\s+", " ", s).strip().lower()


def build_corpus(extracts_dir: Path) -> str:
    return norm(html.unescape(" ".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in extracts_dir.rglob("*.txt"))))


def evidence_ok(atom_text: str, corpus: str) -> bool:
    m = EVIDENCE_RE.search(atom_text)
    if not m:
        return False
    evidence = html.unescape(m.group(1))
    # Evidence may be several quoted spans joined by prose ("..." and "...").
    # Spans are delimited by UNESCAPED quotes; a \" inside a span is a literal
    # quote belonging to the quoted text, not a span boundary.
    spans = [s.replace(r'\"', '"').replace(r'\n', ' ')
             for s in re.findall(r'"((?:[^"\\]|\\.)*)"', evidence)]
    if not spans:
        spans = [evidence.replace(r'\"', '"').replace(r'\n', ' ').strip().strip('"')]
    segs = [s for span in spans for s in re.split(r"\.\.\.|…|â€¦", span) if len(norm(s)) >= MIN_SEGMENT]
    # ANY sufficiently-long segment matching grounds the atom — quotes that cross
    # the extractor's truncation boundary would fail a longest-segment-only rule.
    return any(norm(s) in corpus for s in segs) if segs else norm(m.group(1)) in corpus


def main(atoms_dir: Path, extracts_dir: Path) -> None:
    corpus = build_corpus(extracts_dir)
    ok, miss, no_evidence = [], [], []
    for atom in sorted(atoms_dir.rglob("*.md")):
        text = atom.read_text(encoding="utf-8")
        if not EVIDENCE_RE.search(text):
            no_evidence.append(atom)
        elif evidence_ok(text, corpus):
            ok.append(atom)
        else:
            miss.append(atom)

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
