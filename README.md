# remembrancer

**Evidence-grounded long-term memory for coding agents.**

Remembrancer distills your coding-agent session transcripts (Claude Code
JSONL) into one-fact-per-file Markdown memory — and refuses to remember
anything it can't prove. Every candidate fact must carry a **verbatim quote**
from the transcript it came from; a model judge must rule it genuinely new
against your entire existing memory; and nothing is ever written without an
explicit human-approved judgment file. Memory with receipts.

*The name is a nod to the King's Remembrancer — the officer whose duty was
reminding the court of what it was bound to know.*

## Why evidence quotes

Most agent-memory systems store beliefs. The failure mode is silent: a
paraphrase drifts, a summary invents, and six weeks later your agent
confidently "remembers" something nobody ever said. Remembrancer's core rule
is that a fact without a verbatim transcript quote is inadmissible — the
quote is checked mechanically against the extracts, and unverifiable facts
can never be promoted. When memory and new evidence disagree, the conflict
is routed to a human instead of being resolved by whichever text a model
read last.

## Pipeline

```
transcripts (JSONL)
   │  archive-transcripts.ps1        nightly, additive — defeats ~30-day pruning
   ▼
extract_transcript_text.py           dialogue only; checkpointed/incremental
   │
   ▼
DISTILLER_PROMPT.md  ──(your agent)→ staged atoms, verbatim Evidence per fact
   │
   ▼
promote.py (dry run)                 evidence verification + judgment queue
   │
   ▼
JUDGMENT_PROMPT.md   ──(your agent)→ rulings: NEW / DUPLICATE / SUPERSEDED /
   │                                 DISCARD / CONFLICT, quotes both ways
   ▼
judgment_tools.py merge              validate rulings against the queue
   │
   ▼
promote.py --apply --judgments …     writes ONLY judge-ruled-NEW, verified
                                     atoms; dry-run is the default; there is
                                     no bypass flag
```

The Python is **stdlib-only and makes zero LLM/API calls**. The two
distillation and judgment stages are *prompt contracts* executed by whatever
agent runtime you already use (e.g. Claude Code subagents): you hand the
prompt file plus the input paths to a cheap model, it writes files, the
pipeline validates them. Structure over model firepower.

## Quickstart

```bash
# 0. (optional, Windows) start archiving transcripts so history stops evaporating
powershell archive-transcripts.ps1 -Destination D:\transcript-archive

# 1. extract new dialogue from a project's transcripts
python extract_transcript_text.py --project <project-slug> --out extracts

# 2. distill: run DISTILLER_PROMPT.md over each new extract chunk with your
#    agent, writing atoms to staging/<project>/

# 3. analyze: evidence check + judgment queue (writes nothing to memory)
python promote.py staging/<project> <memory-dir> --extracts extracts \
    --report reports/report.md --queue reports/queue.jsonl

# 4. judge: run JUDGMENT_PROMPT.md over the queue with your agent
#    (judgment_tools.py split for big queues), then validate:
python judgment_tools.py merge reports/judgments reports/queue.jsonl \
    reports/judgments.jsonl --review reports/review.md

# 5. review reports/review.md yourself, then — and only then — apply:
python promote.py staging/<project> <memory-dir> --apply \
    --judgments reports/judgments.jsonl --extracts extracts \n    --queue reports/queue.jsonl
```

`--apply` requires the queue: the dry run writes a `<queue>.manifest.json`
sidecar hashing every staged atom and every corpus file, and apply refuses
if ANY content changed since judging — an edited atom, a modified or deleted
memory file, a sync clobber. Judgments are bound to exactly what was judged;
timestamps are never the authority. Set `REMEMBRANCER_GLOBAL_MD` to your
global instructions file (e.g. CLAUDE.md) so the manifest covers it too —
the judgment contract has judges read it.

Keep reports OUT of the staging directory — every `.md` under staging is
treated as a staged atom.

## Design principles (learned the hard way)

1. **Code for recall, models for judgment.** An early version used string
   matching to pick "candidate" related memories for the judge. It confidently
   starved judges of the real corpus. Retired: judges read the entire memory
   store (it's small; that's fine), and code sticks to what it can actually
   know.
2. **Dedup against live memory before promoting — newer evidence wins.** A
   staged fact can be true when spoken and refuted by the time you promote it.
3. **Unverifiable evidence caps the ruling.** No quote in the corpus → the
   atom can never be ruled NEW, no matter how plausible.
4. **Every delegated build gets independent verification.** Each stage of this
   pipeline was built against a spec with an acceptance checklist and verified
   by a second party. Every single build had a catch.
5. **The human is the only writer of record.** Nothing autonomous ever runs
   `--apply`. The scheduled runs end at a report.

## Requirements

- Python 3.13+ (stdlib only)
- A coding-agent runtime that can execute the two prompt contracts against
  local files (any Claude Code-style subagent works)
- Claude Code-format session transcripts (`~/.claude/projects/<slug>/*.jsonl`);
  set `REMEMBRANCER_ARCHIVE` to read from a durable archive instead

## Status

v0.1 — extracted from a working personal system (a 23-project estate runs on
it weekly). Interfaces may move; the invariants above won't.

## License

MIT
