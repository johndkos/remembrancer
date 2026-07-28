"""Extract human-readable dialogue from Claude Code session transcripts (JSONL).

Keeps: real user messages, assistant text (truncated), compaction summaries.
Drops: tool calls/results, sidechains (subagent chatter), hooks, queue ops,
       system-reminder blocks embedded in user content.

Output: one .txt extract per session, split into ~MAX_PART_BYTES parts at
message boundaries so each part fits comfortably in a distiller context.

Source resolution: the REMEMBRANCER_ARCHIVE environment variable (a durable
transcript archive you maintain — see archive-transcripts.ps1), falling back
to the live ~/.claude/projects directory. Note the live directory is pruned
by Claude Code after ~30 days; run the archiver if you want history to keep.

Usage: python extract_transcript_text.py <project_dir> <out_dir>
       python extract_transcript_text.py --project <slug> [--out DIR] [--source DIR] [--state FILE]
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

ASSISTANT_TRUNC = 400      # chars kept per assistant text block
USER_CAP = 4000            # chars kept per user message (huge pastes trimmed)
MAX_PART_BYTES = 140_000   # split threshold per output part

SYS_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
DEFAULT_STATE = Path(__file__).resolve().parent / "state" / "extract_state.json"


def clean_user_text(text: str) -> str | None:
    text = SYS_REMINDER.sub("", text)
    if text.strip().startswith("Base directory for this skill:"):
        return None
    m = COMMAND_NAME.search(text)
    if m:  # slash-command invocation — keep as a one-line note
        return f"[ran slash command: {m.group(1).strip()}]"
    if "<local-command-stdout>" in text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > USER_CAP:
        text = text[:USER_CAP] + f" ...[trimmed, {len(text)} chars total]"
    return text


def iter_messages(path: Path):
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtype = rec.get("type")
            if rtype == "summary" and rec.get("summary"):
                yield ("SESSION SUMMARY (context-only, not evidence)", rec["summary"], None)
                continue
            if rec.get("isSidechain"):
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            ts = (rec.get("timestamp") or "")[:16]
            content = msg.get("content")
            if rtype == "user":
                if isinstance(content, str):
                    t = clean_user_text(content)
                    if t:
                        yield ("USER", t, ts)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            t = clean_user_text(item.get("text", ""))
                            if t:
                                yield ("USER", t, ts)
            elif rtype == "assistant" and isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        t = item.get("text", "").strip()
                        if not t:
                            continue
                        if len(t) > ASSISTANT_TRUNC:
                            t = t[:ASSISTANT_TRUNC] + " ...[trimmed]"
                        yield ("ASSISTANT", t, ts)


def extract(project_dir: Path, out_dir: Path, state_path: Path = DEFAULT_STATE, run_date: str | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    try: state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError): state = {"files": {}}
    files, created = state.setdefault("files", {}), []
    for path in sorted(project_dir.glob("*.jsonl")):
        session = path.stem
        key = str(path.resolve()); messages = list(iter_messages(path)); prior = files.get(key, {})
        done = min(int(prior.get("messages", 0)), len(messages))
        part, part_no, written = [], int(prior.get("next_part", 1)), 0

        def flush():
            nonlocal part, part_no, written
            if not part:
                return
            stamp = run_date or date.today().isoformat()
            name = f"{session}.part{part_no:02d}.{stamp}.txt"
            while (out_dir / name).exists():
                part_no += 1; name = f"{session}.part{part_no:02d}.{stamp}.txt"
            body = f"=== session {session} — part {part_no} ===\n\n" + "\n\n".join(part)
            (out_dir / name).write_text(body, encoding="utf-8")
            created.append(out_dir / name)
            written += len(body)
            part, part_no = [], part_no + 1

        size = 0
        for role, text, ts in messages[done:]:
            entry = f"[{ts or '-'}] {role}:\n{text}"
            if size + len(entry) > MAX_PART_BYTES and part:
                flush()
                size = 0
            part.append(entry)
            size += len(entry)
        flush()
        files[key] = {"messages": len(messages), "next_part": part_no}
        # Parts precede state deliberately: a crash re-emits duplicates (which
        # judges/dedup catch) rather than losing content.
        atomic_write_state(state_path, state)
        print(f"{session}: {len(messages)-done} new message(s), {written/1e3:.0f} KB")
    atomic_write_state(state_path, state)
    return created

def atomic_write_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=state_path.name + ".", suffix=".tmp", dir=state_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(name, state_path)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise

def source_root() -> Path:
    archive = os.environ.get("REMEMBRANCER_ARCHIVE")
    if archive and Path(archive).expanduser().exists():
        return Path(archive).expanduser()
    return Path.home() / ".claude" / "projects"

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and not argv[0].startswith("-"):
        extract(Path(argv[0]), Path(argv[1])); return
    parser = argparse.ArgumentParser(); group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project"); group.add_argument("--all", action="store_true")
    parser.add_argument("--source", type=Path); parser.add_argument("--out", type=Path, default=Path(__file__).parent / "extracts")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE); args = parser.parse_args(argv)
    root = args.source or source_root(); projects = [root / args.project] if args.project else sorted(p for p in root.iterdir() if p.is_dir())
    for project in projects:
        if project.is_dir(): extract(project, args.out / project.name, args.state)


if __name__ == "__main__":
    main()
