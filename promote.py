"""Prepare and apply externally judged memory promotions."""
from __future__ import annotations
import argparse, hashlib, json, os, re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from verify_evidence import build_corpus, evidence_ok, norm

RULINGS = {"NEW", "DUPLICATE", "SUPERSEDED", "DISCARD", "CONFLICT"}
FIELDS = {"atom", "ruling", "memory_file", "atom_quote", "memory_quote", "note"}

@dataclass
class Atom:
    path: Path; name: str; description: str; kind: str; session: str; ts: str; body: str; text: str

def field(text, key):
    m = re.search(rf"(?m)^\s*{re.escape(key)}:\s*[\"']?(.*?)[\"']?\s*$", text)
    return m.group(1) if m else ""

def load(path):
    text = path.read_text(encoding="utf-8"); parts = text.split("---", 2)
    body = parts[2].strip() if len(parts) > 2 else text
    return Atom(path, field(text, "name") or path.stem, field(text, "description"), field(text, "type"), field(text, "source_session") or field(text, "originSessionId"), field(text, "source_ts"), body, text)

def house(atom):
    return f"---\nname: {atom.name}\ndescription: \"{atom.description.replace(chr(34), chr(39))}\"\nmetadata:\n  node_type: memory\n  type: {atom.kind}\n  originSessionId: {atom.session}\n---\n\n{atom.body}\n"

INDEX_LINE_MAX = 320  # the index loads into context every session — keep hooks bounded

def index_line(atom):
    title = atom.name.replace("-", " ")
    title = title[:1].upper() + title[1:]
    prefix = f"- [{title}]({atom.name}.md) — "
    budget = max(0, INDEX_LINE_MAX - len(prefix))
    desc = " ".join(atom.description.split())
    if len(desc) <= budget:
        return prefix + desc
    # cut on a word boundary and mark the elision — never strand a hook mid-word
    cut = desc[:budget].rsplit(" ", 1)[0].rstrip(" ,;:.—-")
    return prefix + (cut or desc[:budget]) + "…"

def append_index(path, atom):
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = text.rstrip("\r\n")
    line = index_line(atom)
    # write via tempfile + os.replace so a crash can never truncate the index
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(f"{text}\n{line}\n" if text else f"{line}\n", encoding="utf-8")
    os.replace(tmp, path)

def read_judgments(path, atoms, evidence, target, corpus_also):
    judged = {}
    try: lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc: raise ValueError(f"cannot read judgments file: {exc}") from exc
    for number, line in enumerate(lines, 1):
        try: value = json.loads(line)
        except json.JSONDecodeError as exc: raise ValueError(f"malformed judgment line {number}: {exc.msg}") from exc
        if not isinstance(value, dict) or not FIELDS.issubset(value): raise ValueError(f"judgment line {number} is missing required schema fields")
        if (not isinstance(value["atom"], str) or not isinstance(value["ruling"], str)
                or not isinstance(value["atom_quote"], str) or not value["atom_quote"]
                or not isinstance(value["note"], str) or not value["note"]
                or value["memory_file"] is not None and not isinstance(value["memory_file"], str)
                or value["memory_quote"] is not None and not isinstance(value["memory_quote"], str)):
            raise ValueError(f"judgment line {number} has invalid schema field types")
        slug = value["atom"]
        if slug not in atoms: raise ValueError(f"unknown atom in judgments: {slug}")
        if slug in judged: raise ValueError(f"atom has more than one ruling: {slug}")
        if value["ruling"] not in RULINGS: raise ValueError(f"invalid ruling for atom {slug}: {value['ruling']}")
        if value["ruling"] in ("NEW", "DISCARD") and (value["memory_file"] is not None or value["memory_quote"] is not None): raise ValueError(f"{value['ruling']} judgment must have null memory fields: {slug}")
        # DUPLICATE/SUPERSEDED assert a relationship to a specific memory and must cite it;
        # CONFLICT is the contract's cannot-quote fallback, so its memory fields may be null.
        if value["ruling"] in ("DUPLICATE", "SUPERSEDED") and (not value["memory_file"] or not value["memory_quote"]): raise ValueError(f"{value['ruling']} judgment requires memory evidence: {slug}")
        if value["ruling"] == "NEW" and not evidence[slug]: raise ValueError(f"unverified-evidence atom cannot be ruled NEW: {slug}")
        if norm(value["atom_quote"]) not in norm(atoms[slug].text):
            raise ValueError(f"atom_quote does not appear in staged atom: {slug}")
        if value["ruling"] in ("DUPLICATE", "SUPERSEDED"):
            candidates = list(target.glob("*.md")) + list(corpus_also)
            cited = next((p for p in candidates if p.name == value["memory_file"] or str(p) == value["memory_file"]), None)
            if cited is None or not cited.exists():
                raise ValueError(f"memory_file does not exist for {slug}: {value['memory_file']}")
            if norm(value["memory_quote"]) not in norm(cited.read_text(encoding="utf-8")):
                raise ValueError(f"memory_quote does not appear in {cited.name} for {slug}")
        judged[slug] = value
    missing = sorted(set(atoms) - set(judged))
    if missing: raise ValueError(f"missing judgment for atom(s): {', '.join(missing)}")
    return judged

def stamp(mtime):
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

def judged_at(judgments):
    """When the rulings were formed. Prefer the EARLIEST per-chunk judge output: a chunked
    run forms rulings across a window, and the merged file is written after every read."""
    chunks = judgments.with_name(judgments.stem)
    if chunks.is_dir():
        times = [path.stat().st_mtime for path in chunks.glob("*.jsonl")]
        if times: return min(times)
    return judgments.stat().st_mtime

def moved_corpus(target, atoms, judgments, corpus_also):
    """Corpus files changed after judging. Rulings are only valid against the corpus as of
    judging time, so anything newer means the judges ruled against text that no longer exists."""
    cutoff = judged_at(judgments); moved = []
    for path in sorted(target.glob("*.md")):  # non-recursive: _archive is out of scope by contract
        if path.name == "MEMORY.md": continue  # we rewrite it ourselves; derived index, not a source of facts
        atom = atoms.get(path.stem)
        # our own prior output is not corpus drift — but only when identical to what we would
        # write now; an edited copy is a real corpus change even if the slug matches. Compare as
        # text: read_text normalizes the CRLF that write_text produces on Windows.
        if atom is not None and path.read_text(encoding="utf-8") == house(atom): continue
        if path.stat().st_mtime > cutoff: moved.append(path)
    for path in corpus_also or []:
        if path.exists() and path.stat().st_mtime > cutoff: moved.append(path)
    return cutoff, moved


def sha256_text(path):
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()

def resolved_corpus_also(corpus_also):
    if corpus_also is not None:
        return list(corpus_also)
    # Judges are told to read your global instructions file (e.g. CLAUDE.md)
    # alongside project memory; point REMEMBRANCER_GLOBAL_MD at it so the
    # corpus manifest and staleness guard cover it too.
    env = os.environ.get("REMEMBRANCER_GLOBAL_MD")
    return [Path(env).expanduser()] if env else []

def corpus_files(target, corpus_also):
    files = list(sorted(target.glob("*.md")))
    for path in corpus_also:
        if path.exists():
            files.append(path)
        else:
            print(f"WARNING: judgments assumed CLAUDE.md but corpus-also file does not exist: {path}")
    return files

def manifest_path(queue):
    return queue.with_name(queue.name + ".manifest.json")

def write_manifest(queue, atoms, target, corpus_also):
    files = corpus_files(target, corpus_also)
    names = [path.name for path in files]
    if len(names) != len(set(names)):
        raise ValueError("corpus manifest filenames must be unique")
    data = {
        "created": datetime.now().astimezone().isoformat(),
        "atom_sha256": {slug: sha256_text(atom.path) for slug, atom in atoms.items()},
        "corpus_manifest": {path.name: sha256_text(path) for path in files},
    }
    manifest_path(queue).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def validate_manifest(queue, atoms, target, corpus_also):
    sidecar = manifest_path(queue)
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read queue manifest sidecar {sidecar}: {exc}") from exc
    recorded_atoms = data.get("atom_sha256", {})
    current_atoms = {slug: sha256_text(atom.path) for slug, atom in atoms.items()}
    changed_atoms = sorted(slug for slug in set(current_atoms) | set(recorded_atoms)
                           if current_atoms.get(slug) != recorded_atoms.get(slug))
    if changed_atoms:
        raise ValueError(f"atom changed since judging; re-run dry-run + judgment: {', '.join(changed_atoms)}")
    current_files = corpus_files(target, corpus_also)
    current = {path.name: sha256_text(path) for path in current_files}
    recorded = data.get("corpus_manifest", {})
    changed_files = sorted(name for name in set(current) | set(recorded) if current.get(name) != recorded.get(name))
    if changed_files:
        raise ValueError(f"memory corpus changed since judging: {', '.join(changed_files)}")

def run(staging, target, apply=False, extracts=None, report=None, judgments=None, queue=None, corpus_also=None):
    if extracts is None: raise ValueError("--extracts is required; provide a directory containing transcript extract .txt files")
    if not extracts.exists(): raise ValueError(f"extracts path does not exist: {extracts}")
    if not extracts.is_dir() or not any(extracts.rglob("*.txt")): raise ValueError(f"extracts path contains no .txt files: {extracts}")
    paths = sorted(staging.rglob("*.md")); atoms = {atom.name: atom for atom in map(load, paths)}
    if len(atoms) != len(paths): raise ValueError("staged atom slugs must be unique")
    corpus = build_corpus(extracts)
    evidence = {slug: evidence_ok(atom.text, corpus) for slug, atom in atoms.items()}
    corpus_also = resolved_corpus_also(corpus_also)
    if apply:
        if judgments is None: raise ValueError("--apply requires --judgments from the external judgment stage; there is no bypass")
        if queue is None: raise ValueError("--apply requires --queue so judged content can be verified")
        validate_manifest(queue, atoms, target, corpus_also)
        ruled = read_judgments(judgments, atoms, evidence, target, corpus_also)
        cutoff, moved = moved_corpus(target, atoms, judgments, corpus_also)
        if moved:
            detail = "\n".join(f"  {path.name}  {stamp(path.stat().st_mtime)}" for path in moved)
            raise ValueError(
                f"judgments are stale: {len(moved)} corpus file(s) changed after judging ({stamp(cutoff)})\n{detail}\n"
                "Rulings are only valid against the corpus as of judging time. Re-judge this\n"
                "project against the current corpus, then apply the new judgments.")
        rows = [(atom, ruled[slug]["ruling"], evidence[slug], ruled[slug]["note"], "write" if ruled[slug]["ruling"] == "NEW" else "review") for slug, atom in atoms.items()]
        applied = []
        try:
            for atom, ruling, _, _, _ in rows:
                if ruling == "NEW" and not (target / f"{atom.name}.md").exists():
                    (target / f"{atom.name}.md").write_text(house(atom), encoding="utf-8")
                    append_index(target / "MEMORY.md", atom)
                    applied.append(atom.name)
        except Exception as exc:
            failed = atom.name
            remaining = [a.name for a, r, *_ in rows if r == "NEW" and a.name not in applied and a.name != failed]
            text = ("# Judgment apply failure report\n\n"
                    f"Applied (file + index both done): {', '.join(applied) or 'none'}\n\n"
                    f"Failed: {failed}\n\nRemaining: {', '.join(remaining) or 'none'}\n\nError: {exc}\n")
            if report:
                report.parent.mkdir(parents=True, exist_ok=True)
                try: report.write_text(text, encoding="utf-8")
                except OSError as report_exc: print(f"WARNING: could not write failure report: {report_exc}")
            else:
                print(text, end="")
            raise ValueError(f"partial apply failed; applied: {', '.join(applied) or 'none'}; failed: {failed}; remaining: {', '.join(remaining) or 'none'}") from exc
        text = "# Judgment apply report\n\n| Atom | Ruling | Evidence | Judge note | Action |\n|---|---|---|---|---|\n" + "".join(f"| {a.name} | {r} | {'verified' if v else 'unverified'} | {n} | {x} |\n" for a, r, v, n, x in rows)
        discarded = [a.name for a, ruling, *_ in rows if ruling == "DISCARD"]
        text += "\n## Discarded\n\n" + ("\n".join(f"- {slug}" for slug in discarded) if discarded else "None") + "\n"
    else:
        rows = []; queue_path = queue or (report.parent / "judgment_queue.jsonl" if report else Path("judgment_queue.jsonl")); queue_path.parent.mkdir(parents=True, exist_ok=True)
        with queue_path.open("w", encoding="utf-8") as output:
            for slug, atom in atoms.items():
                rows.append((atom, evidence[slug], "awaiting judgment stage"))
                output.write(json.dumps({"atom": slug, "atom_text": atom.text, "evidence_status": "verified" if evidence[slug] else "unverified"}) + "\n")
        write_manifest(queue_path, atoms, target, corpus_also)
        text = "# Promotion judgment report\n\nRelationships are ruled by the judgment stage.\n\n| Atom | Evidence | Action |\n|---|---|---|\n" + "".join(f"| {a.name} | {'verified' if v else 'unverified'} | {action} |\n" for a, v, action in rows)
    if report: report.parent.mkdir(parents=True, exist_ok=True); report.write_text(text, encoding="utf-8")
    else: print(text, end="")
    return rows

def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("staging_dir", type=Path); parser.add_argument("target_memory_dir", type=Path); parser.add_argument("--apply", action="store_true"); parser.add_argument("--judgments", type=Path); parser.add_argument("--extracts", type=Path, required=True, help="directory containing transcript extract .txt files"); parser.add_argument("--report", type=Path); parser.add_argument("--queue", type=Path); parser.add_argument("--corpus-also", type=Path, action="append", help="additional file the judges were required to read (e.g. the global standing-rules CLAUDE.md); repeatable"); args = parser.parse_args(argv)
    if args.apply and args.judgments is None: parser.error("--apply requires --judgments from the external judgment stage; there is no bypass")
    if args.apply and args.queue is None: parser.error("--apply requires --queue so judged content can be verified")
    try: run(args.staging_dir, args.target_memory_dir, args.apply, args.extracts, args.report, args.judgments, args.queue, args.corpus_also)
    except ValueError as exc: parser.error(str(exc))

if __name__ == "__main__": main()
