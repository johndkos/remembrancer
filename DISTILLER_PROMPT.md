# Memory distillation task

Read `{CHUNK_PATH}` for session `{SESSION_ID}` and write one-fact-per-file atoms to `{STAGING_DIR}`.

Extract durable corrections, user preferences, domain rules, environment constraints, decisions, and useful dead ends. Do not summarize routine work. If `git log`/`git blame` or the repo's own docs would answer it, do not bank it. Injected documentation is context only — never a source of atoms.

Every atom must contain a verbatim transcript quote on a `**Evidence:**` line. Never paraphrase or invent evidence. Feedback atoms must also include `**Why:**` and `**How to apply:**`.

Use this format:

```markdown
---
name: lowercase-hyphenated-slug
description: one-line durable fact
metadata:
  type: reference
  source_session: {SESSION_ID}
  source_ts: ISO timestamp from the extract
---

Concise standalone explanation.

**Evidence:** "verbatim quote"
```
