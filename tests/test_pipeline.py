import json, shutil, subprocess, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from extract_transcript_text import extract
from promote import run

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
    atom(staging / "related.md", "rolling-window", "dashboard rolling window average", "Rolling fact", "missing quotation long enough" if unverified else "evidence phrase long enough")
    atom(target / "rolling-window.md", "rolling-window", "dashboard rolling window average", "Same rolling fact")
    (extracts / "chunk.txt").write_text("evidence phrase long enough", encoding="utf-8")
    return staging, target, extracts

def judgment(slug, ruling, note="reviewed"):
    null_fields = ruling in ("NEW", "DISCARD", "CONFLICT")
    return {"atom": slug, "ruling": ruling, "memory_file": None if null_fields else "rolling-window.md", "atom_quote": "quote", "memory_quote": None if null_fields else "quote", "note": note}

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
    run(staging, target, True, extracts, tmp_path / "apply.md", judgments)
    run(staging, target, True, extracts, tmp_path / "apply2.md", judgments)
    assert (target / "unique-frobnicator.md").exists()
    index = (target / "MEMORY.md").read_text(encoding="utf-8")
    assert index.count("- [Unique frobnicator](unique-frobnicator.md) — unique durable frobnicator setting") == 1
    assert b"\xc3\xa2\xe2\x82\xac\xe2\x80\x9d" not in (target / "MEMORY.md").read_bytes()
    assert (target / "rolling-window.md").read_text().count("Same rolling fact") == 1

def test_judgment_validation_aborts_without_writes(tmp_path):
    staging, target, extracts = setup_case(tmp_path, unverified=True)
    cases = [json.dumps(judgment("unique-frobnicator", "NEW")) + "\n", "{bad json\n", "\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "NEW"), judgment("rolling-window", "NEW")]) + "\n"]
    for number, content in enumerate(cases):
        judgments = tmp_path / f"bad{number}.jsonl"; judgments.write_text(content, encoding="utf-8"); before = snapshot(target)
        with pytest.raises(ValueError): run(staging, target, True, extracts, tmp_path / f"report{number}.md", judgments)
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

def test_discard_is_accepted_reported_and_never_written(tmp_path):
    staging, target, extracts = setup_case(tmp_path); judgments = tmp_path / "judgments.jsonl"
    judgments.write_text("\n".join(json.dumps(x) for x in [judgment("unique-frobnicator", "DISCARD"), judgment("rolling-window", "DUPLICATE")]) + "\n", encoding="utf-8")
    report = tmp_path / "apply.md"; before = snapshot(target)
    run(staging, target, True, extracts, report, judgments)
    assert snapshot(target) == before and not (target / "unique-frobnicator.md").exists()
    assert "## Discarded" in report.read_text(encoding="utf-8") and "unique-frobnicator" in report.read_text(encoding="utf-8")
