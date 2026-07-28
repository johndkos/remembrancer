import json, os, shutil, subprocess, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from extract_transcript_text import extract
from promote import run
from verify_evidence import build_corpus, evidence_ok

SCRIPT = Path(__file__).parents[1] / "promote.py"
WORK = Path(__file__).parent / "work"

@pytest.fixture
def tmp_path(request):
    path = WORK / request.node.name
    if path.exists(): shutil.rmtree(path)
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path)

def rec(kind, text, ts="2026-01-01T00:00:00"):
    return json.dumps({"type": kind, "timestamp": ts, "message": {"content": text if kind == "user" else [{"type": "text", "text": text}]}}) + "\n"

def atom(path, name, description, body, evidence="evidence phrase long enough"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\nmetadata:\n  type: reference\n  source_session: session\n---\n\n{body}\n\n**Evidence:** \"{evidence}\"\n", encoding="utf-8")

def setup_case(tmp_path, unverified=False):
    staging, target, extracts = tmp_path / "staging", tmp_path / "memory", tmp_path / "extracts"
    staging.mkdir(); target.mkdir(); extracts.mkdir(); (target / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    atom(staging / "new.md", "unique-frobnicator", "unique durable frobnicator setting", "Unique fact")
    atom(staging / "related.md", "rolling-window", "club table rolling shots average", "Rolling fact", "missing quotation long enough" if unverified else "evidence phrase long enough")
    atom(target / "rolling-window.md", "rolling-window", "club table rolling shots average", "Same rolling fact")
    (extracts / "chunk.txt").write_text("evidence phrase long enough", encoding="utf-8")
    return staging, target, extracts

def judgment(slug, ruling, note="reviewed"):
    null_fields = ruling in ("NEW", "DISCARD", "CONFLICT")
    atom_quote = "Unique fact" if slug == "unique-frobnicator" else "Rolling fact"
    return {"atom": slug, "ruling": ruling, "memory_file": None if null_fields else "rolling-window.md", "atom_quote": atom_quote, "memory_quote": None if null_fields else "Same rolling fact", "note": note}

def dry_queue(staging, target, extracts, root, corpus_also=[]):
    queue = root / "queue.jsonl"
    run(staging, target, extracts=extracts, report=root / "dry.md", queue=queue, corpus_also=corpus_also)
    return queue

def snapshot(target): return {p.name: p.read_bytes() for p in target.iterdir()}

def test_extractor_checkpointing(tmp_path):
    project = tmp_path / "p"; project.mkdir(); transcript = project / "s.jsonl"
    transcript.write_text(rec("user", "hello durable world") + rec("assistant", "answer"), encoding="utf-8")
    out, state = tmp_path / "out", tmp_path / "state.json"
    first = extract(project, out, state, "2026-01-01"); assert len(first) == 1
    assert extract(project, out, state, "2026-01-02") == []
    with transcript.open("a", encoding="utf-8") as f: f.write(rec("user", "delta fact"))
    delta = extract(project, out, state, "2026-01-03")
    assert len(delta) == 1 and "delta fact" in delta[0].read_text() and "hello durable" not in delta[0].read_text()

def test_skill_injection_stripping(tmp_path):
    project = tmp_path / "p"; project.mkdir()
    (project / "s.jsonl").write_text(rec("user", "kept") + rec("user", "Base directory for this skill: /x\ninjected"), encoding="utf-8")
    output = extract(project, tmp_path / "out", tmp_path / "state.json", "2026-01-01")[0].read_text()
    assert "kept" in output and "Base directory" not in output and "injected" not in output

def test_dry_run_has_slim_queue_and_no_candidates(tmp_path):
    staging, target, extracts = setup_case(tmp_path); report = tmp_path / "report.md"; queue = tmp_path / "queue.jsonl"
    rows = run(staging, target, extracts=extracts, report=report, queue=queue)
    assert len(rows) == 2
    entries = list(map(json.loads, queue.read_text(encoding="utf-8").splitlines()))
    assert all(set(entry) == {"atom", "atom_text", "evidence_status"} for entry in entries)
    text = report.read_text(encoding="utf-8")
    assert "candidate" not in text.lower() and "awaiting judgment stage" in text
    assert "Relationships are ruled by the judgment stage." in text

def test_unverified_evidence_flagged_in_queue(tmp_path):
    staging, target, extracts = setup_case(tmp_path, unverified=True); queue = tmp_path / "queue.jsonl"
    run(staging, target, extracts=extracts, report=tmp_path / "report.md", queue=queue)
    entries = {x["atom"]: x for x in map(json.loads, queue.read_text().splitlines())}
    assert entries["rolling-window"]["evidence_status"] == "unverified"

def test_apply_without_judgments_errors_without_writes(tmp_path):
    staging, target, extracts = setup_case(tmp_path); before = snapshot(target)
    result = subprocess.run([sys.executable, str(SCRIPT), str(staging), str(target), "--extracts", str(extracts), "--apply"], text=True, capture_output=True)
    assert result.returncode != 0 and "judgment stage" in result.stderr and snapshot(target) == before

def test_apply_with_judgments_writes_only_new_and_indexes_once(tmp_path):
    staging, target, extracts = setup_case(tmp_path); judgments = tmp_path / "judgments.jsonl"
    judgments.write_text("\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "NEW"), judgment("rolling-window", "DUPLICATE")]) + "\n", encoding="utf-8")
    run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    run(staging, target, True, extracts, tmp_path / "apply2.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert (target / "unique-frobnicator.md").exists()
    index = (target / "MEMORY.md").read_text(encoding="utf-8")
    assert index.count("- [Unique frobnicator](unique-frobnicator.md) — unique durable frobnicator setting") == 1
    assert b"\xc3\xa2\xe2\x82\xac\xe2\x80\x9d" not in (target / "MEMORY.md").read_bytes()
    assert (target / "rolling-window.md").read_text().count("Same rolling fact") == 1

def test_index_hook_never_truncates_mid_word(tmp_path):
    from promote import INDEX_LINE_MAX, index_line, load
    long_desc = "CDGA differential CSVs and their JSON twins carry exact byte conventions " * 6
    path = tmp_path / "long.md"
    atom(path, "verbose-atom", long_desc, "Body")
    line = index_line(load(path))
    assert len(line) <= INDEX_LINE_MAX + 1                  # +1 for the ellipsis
    assert line.endswith("…"), "a shortened hook must be marked as elided"
    assert not line.rstrip("…").endswith(" "), "no dangling space before the ellipsis"
    assert line.rstrip("…").split()[-1] in long_desc.split(), "last word must be whole"
    short = tmp_path / "short.md"
    atom(short, "terse-atom", "a short durable fact", "Body")
    assert index_line(load(short)).endswith("a short durable fact")  # untouched, no ellipsis

def test_judgment_validation_aborts_without_writes(tmp_path):
    staging, target, extracts = setup_case(tmp_path, unverified=True)
    cases = [json.dumps(judgment("unique-frobnicator", "NEW")) + "\n", "{bad json\n", "\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "NEW"), judgment("rolling-window", "NEW")]) + "\n"]
    for number, content in enumerate(cases):
        judgments = tmp_path / f"bad{number}.jsonl"; judgments.write_text(content, encoding="utf-8"); before = snapshot(target)
        with pytest.raises(ValueError): run(staging, target, True, extracts, tmp_path / f"report{number}.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
        assert snapshot(target) == before

def test_escaped_inner_quote_evidence_verifies(tmp_path):
    staging, target, extracts = setup_case(tmp_path)
    atom(staging / "quoted.md", "quoted", "quoted evidence", "Fact", r'prefix \"inner words\" suffix long enough')
    (extracts / "chunk.txt").write_text('prefix "inner words" suffix long enough', encoding="utf-8")
    queue = tmp_path / "queue.jsonl"
    run(staging, target, extracts=extracts, report=tmp_path / "report.md", queue=queue)
    entries = {entry["atom"]: entry for entry in map(json.loads, queue.read_text(encoding="utf-8").splitlines())}
    assert entries["quoted"]["evidence_status"] == "verified"

@pytest.mark.parametrize("kind", ["missing", "empty"])
def test_missing_or_empty_extracts_clear_error_without_writes(tmp_path, kind):
    staging, target, _ = setup_case(tmp_path); before = snapshot(target)
    bad = tmp_path / "absent" if kind == "missing" else tmp_path / "empty"
    if kind == "empty": bad.mkdir()
    result = subprocess.run([sys.executable, str(SCRIPT), str(staging), str(target), "--extracts", str(bad), "--report", str(tmp_path / "report.md")], text=True, capture_output=True)
    assert result.returncode != 0 and str(bad) in result.stderr
    assert ("does not exist" in result.stderr if kind == "missing" else "no .txt files" in result.stderr)
    assert snapshot(target) == before and not (tmp_path / "report.md").exists()

def test_omitted_extracts_is_actionable_without_writes(tmp_path):
    staging, target, _ = setup_case(tmp_path); before = snapshot(target)
    result = subprocess.run([sys.executable, str(SCRIPT), str(staging), str(target)], text=True, capture_output=True)
    assert result.returncode != 0 and "--extracts" in result.stderr and "required" in result.stderr
    assert snapshot(target) == before

def age(path, seconds):
    """Move a file's mtime by `seconds` (negative = older). Timestamps are the guard's whole
    input, so tests set them explicitly rather than relying on write order."""
    stat = path.stat(); os.utime(path, (stat.st_atime, stat.st_mtime + seconds))

def staleness_case(tmp_path, ruling="NEW"):
    staging, target, extracts = setup_case(tmp_path)
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text("\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", ruling), judgment("rolling-window", "DUPLICATE")]) + "\n", encoding="utf-8")
    for path in target.glob("*.md"): age(path, -3600)   # corpus predates judging
    return staging, target, extracts, judgments

def test_stale_corpus_blocks_apply_without_writes(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    age(target / "rolling-window.md", 7200)             # audit rewrites it after judging
    before = snapshot(target)
    with pytest.raises(ValueError) as exc:
        run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert "stale" in str(exc.value)
    assert snapshot(target) == before and not (target / "unique-frobnicator.md").exists()

def test_stale_error_names_offending_files(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    queue = dry_queue(staging, target, extracts, tmp_path)
    age(target / "rolling-window.md", 7200)
    result = subprocess.run([sys.executable, str(SCRIPT), str(staging), str(target), "--extracts", str(extracts), "--apply", "--judgments", str(judgments), "--queue", str(queue), "--corpus-also", str(tmp_path / "absent-global.md")], text=True, capture_output=True)
    assert result.returncode != 0
    assert "rolling-window.md" in result.stderr and "Re-judge" in result.stderr
    assert result.stderr.count(":") >= 2, "both the cutoff and the file time must be shown"

def test_fresh_corpus_applies_normally(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert (target / "unique-frobnicator.md").exists()
    assert "- [Unique frobnicator]" in (target / "MEMORY.md").read_text(encoding="utf-8")

def test_memory_index_mtime_does_not_trigger_staleness(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    # MEMORY.md and the atom we just wrote are both newer than the judgments now; neither is drift
    run(staging, target, True, extracts, tmp_path / "apply2.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert (target / "MEMORY.md").read_text(encoding="utf-8").count("- [Unique frobnicator]") == 1

def test_edited_own_output_is_treated_as_corpus_drift(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    written = target / "unique-frobnicator.md"
    written.write_text(written.read_text(encoding="utf-8") + "\nAudit correction.\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        run(staging, target, True, extracts, tmp_path / "apply2.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert "unique-frobnicator.md" in str(exc.value)

def test_corpus_also_file_triggers_staleness(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    rules = tmp_path / "CLAUDE.md"; rules.write_text("# standing rules\n", encoding="utf-8")
    before = snapshot(target)
    with pytest.raises(ValueError) as exc:
        run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path, [rules]), [rules])
    assert "CLAUDE.md" in str(exc.value) and snapshot(target) == before

def test_archive_subfolder_never_triggers_staleness(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    archive = target / "_archive"; archive.mkdir()
    (archive / "memory-audit-2026-07-26.md").write_text("audit report\n", encoding="utf-8")
    run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert (target / "unique-frobnicator.md").exists()

def test_chunk_dir_earliest_mtime_is_judged_at(tmp_path):
    staging, target, extracts, judgments = staleness_case(tmp_path)
    chunks = tmp_path / "judgments"; chunks.mkdir()
    (chunks / "chunk01.judgments.jsonl").write_text("", encoding="utf-8")
    (chunks / "chunk02.judgments.jsonl").write_text("", encoding="utf-8")
    age(chunks / "chunk01.judgments.jsonl", -1800)      # first chunk judged before the merge
    age(target / "rolling-window.md", 2700)             # newer than chunk01, older than the merged file
    before = snapshot(target)
    with pytest.raises(ValueError) as exc:
        run(staging, target, True, extracts, tmp_path / "apply.md", judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert "rolling-window.md" in str(exc.value) and snapshot(target) == before

def test_discard_is_accepted_reported_and_never_written(tmp_path):
    staging, target, extracts = setup_case(tmp_path); judgments = tmp_path / "judgments.jsonl"
    judgments.write_text("\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "DISCARD"), judgment("rolling-window", "DUPLICATE")]) + "\n", encoding="utf-8")
    report = tmp_path / "apply.md"; before = snapshot(target)
    run(staging, target, True, extracts, report, judgments, dry_queue(staging, target, extracts, tmp_path), [])
    assert snapshot(target) == before and not (target / "unique-frobnicator.md").exists()
    assert "## Discarded" in report.read_text(encoding="utf-8") and "unique-frobnicator" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["edit", "delete", "add"])
def test_manifest_refuses_corpus_content_drift(tmp_path, mutation):
    staging, target, extracts = setup_case(tmp_path)
    queue = dry_queue(staging, target, extracts, tmp_path)
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text("\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "NEW"), judgment("rolling-window", "DUPLICATE")]) + "\n", encoding="utf-8")
    if mutation == "edit":
        path = target / "rolling-window.md"; old = path.stat()
        path.write_text(path.read_text(encoding="utf-8") + "changed", encoding="utf-8")
        os.utime(path, (old.st_atime, old.st_mtime))
    elif mutation == "delete":
        (target / "rolling-window.md").unlink()
    else:
        (target / "new-corpus.md").write_text("new corpus", encoding="utf-8")
    with pytest.raises(ValueError, match="memory corpus changed since judging") as exc:
        run(staging, target, True, extracts, tmp_path / "apply.md", judgments, queue, [])
    expected = "new-corpus.md" if mutation == "add" else "rolling-window.md"
    assert expected in str(exc.value) and not (target / "unique-frobnicator.md").exists()


def test_manifest_refuses_preserved_mtime_atom_edit(tmp_path):
    staging, target, extracts = setup_case(tmp_path)
    queue = dry_queue(staging, target, extracts, tmp_path)
    judgments = tmp_path / "judgments.jsonl"
    judgments.write_text("\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "NEW"), judgment("rolling-window", "DUPLICATE")]) + "\n", encoding="utf-8")
    path = staging / "new.md"; old = path.stat()
    path.write_text(path.read_text(encoding="utf-8") + "edited", encoding="utf-8")
    os.utime(path, (old.st_atime, old.st_mtime))
    with pytest.raises(ValueError, match="atom changed since judging") as exc:
        run(staging, target, True, extracts, tmp_path / "apply.md", judgments, queue, [])
    assert "unique-frobnicator" in str(exc.value)


def test_all_quoted_spans_must_verify_and_truncation_segment_may_match():
    corpus = "first genuine quotation is definitely present and second genuine quotation is also present short surviving segment matches here"
    fabricated = '**Evidence:** "first genuine quotation is definitely present" and "fabricated sibling quotation is definitely absent"'
    genuine = '**Evidence:** "first genuine quotation is definitely present" and "second genuine quotation is also present"'
    crossing = '**Evidence:** "missing beginning long enough...short surviving segment matches here"'
    assert not evidence_ok(fabricated, corpus)
    assert evidence_ok(genuine, corpus)
    assert evidence_ok(crossing, corpus)


def test_session_summaries_are_context_not_evidence(tmp_path):
    extracts = tmp_path / "extracts"; extracts.mkdir()
    quote = "summary-only durable quotation phrase"
    (extracts / "old.txt").write_text(f"[-] SESSION SUMMARY:\n{quote}\n\n[-] ASSISTANT:\nother text", encoding="utf-8")
    assert not evidence_ok(f'**Evidence:** "{quote}"', build_corpus(extracts))
    (extracts / "new.txt").write_text(f"[-] SESSION SUMMARY (context-only, not evidence):\n{quote}\n\n[-] USER:\n{quote}", encoding="utf-8")
    assert evidence_ok(f'**Evidence:** "{quote}"', build_corpus(extracts))


@pytest.mark.parametrize("bad_kind", ["unknown", "empty_quote"])
def test_merge_rejects_bad_judgment_without_output(tmp_path, bad_kind):
    queue = tmp_path / "queue.jsonl"; queue.write_text(json.dumps({"atom": "known"}) + "\n", encoding="utf-8")
    chunks = tmp_path / "chunks"; chunks.mkdir()
    value = {"atom": "unknown" if bad_kind == "unknown" else "known", "ruling": "NEW", "atom_quote": "quote", "note": "note"}
    if bad_kind == "empty_quote": value["atom_quote"] = ""
    (chunks / "chunk01.judgments.jsonl").write_text(json.dumps(value) + "\n", encoding="utf-8")
    out = tmp_path / "merged.jsonl"
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "judgment_tools.py"), "merge", str(chunks), str(queue), str(out)], text=True, capture_output=True)
    assert result.returncode == 1 and not out.exists()
    assert ("unknown atom" if bad_kind == "unknown" else "atom_quote") in result.stdout


def test_duplicate_nonexistent_memory_quote_is_clear_error(tmp_path):
    staging, target, extracts = setup_case(tmp_path)
    queue = dry_queue(staging, target, extracts, tmp_path)
    judgments = tmp_path / "judgments.jsonl"
    bad = judgment("rolling-window", "DUPLICATE"); bad["memory_quote"] = "nonexistent memory quotation"
    judgments.write_text("\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "DISCARD"), bad]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="memory_quote does not appear") as exc:
        run(staging, target, True, extracts, tmp_path / "apply.md", judgments, queue, [])
    assert "rolling-window" in str(exc.value)


def test_dry_run_writes_complete_manifest_sidecar(tmp_path):
    staging, target, extracts = setup_case(tmp_path)
    extra = tmp_path / "CLAUDE.md"; extra.write_text("global rules", encoding="utf-8")
    queue = dry_queue(staging, target, extracts, tmp_path, [extra])
    data = json.loads((tmp_path / "queue.jsonl.manifest.json").read_text(encoding="utf-8"))
    assert set(data) == {"created", "atom_sha256", "corpus_manifest"}
    assert set(data["atom_sha256"]) == {"unique-frobnicator", "rolling-window"}
    assert set(data["corpus_manifest"]) == {"MEMORY.md", "rolling-window.md", "CLAUDE.md"}
