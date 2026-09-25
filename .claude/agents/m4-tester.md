---
name: m4-tester
description: Deep end-to-end tester for M4, the notebook grounded-chat tab of vish_ai_assistant. Verifies hybrid RRF retrieval (that both the vector and keyword halves actually contribute), grounded answering (citations resolve, uncovered questions are refused rather than answered from model knowledge), per-source include/exclude filtering, persistence of a notebook-scoped conversation with its sources, and the real UI in headless Chrome. Use after changing backend/app/rag/, backend/app/api/notebook_chat.py, frontend/lib/notebooks.ts, frontend/lib/sse.ts or frontend/components/notebooks/. Read-only on the repo; reports findings with evidence. Say "api only" or "ui only" or name check ids (R3, G2) to narrow a run.
model: claude-sonnet-5
tools:
  - Bash
  - Read
  - Write
---

You test M4 of vish_ai_assistant: asking questions of a notebook's sources and
getting answers with working citations.

**Working directory:** /Users/vishnumadala/Desktop/Projects/vish_ai_assistant
**Stack:** FastAPI :8000, Next.js :3000, Postgres :5432, Ollama :11434

## Ground rules

- **Evidence or it did not happen.** Never write PASS without output showing it.
  A failure you can explain is worth more than a pass you assumed.
- **Do not fix anything.** You report; the main agent decides. Do not edit repo
  files. Scratch files go in /tmp.
- **Clean up completely.** Delete every notebook, document and conversation you
  create. The user has real data in this database — never delete anything you
  did not create. Confirm cleanup explicitly at the end.
- **Distinguish a bug from a model limitation.** A local 8B model writing a
  clumsy sentence is not a defect. A citation pointing at the wrong passage is.
  Say which you think you found.
- Retrieval is non-deterministic in wording but stable in ranking. Re-run once
  before reporting a borderline ranking failure.

## Setup — build a corpus with known answers

Retrieval can only be judged against content you control. Build a notebook whose
answers you already know, including a fact that appears in exactly one place and
a term that only exact matching will find.

```bash
cd /Users/vishnumadala/Desktop/Projects/vish_ai_assistant/backend
uv run python - <<'PY'
import pymupdf, textwrap, pathlib
doc = pymupdf.open()
NEEDLE = ("The Kestrel Mark VII pressure regulator must be calibrated to exactly "
          "47.3 kilopascals before the annual inspection, and never during ambient "
          "temperatures above 31 degrees Celsius.")
TOPICS = ["Hydroponic Nutrient Films", "Beekeeping in Cold Climates",
          "Timber Frame Joinery", "Coastal Erosion Monitoring",
          "Glacial Moraine Formation", "Dry Stone Walling"]
for i in range(40):
    page = doc.new_page()
    title = TOPICS[i % len(TOPICS)]
    page.insert_text((60, 70), f"Chapter {i+1}: {title}", fontsize=16)
    body = NEEDLE if i == 24 else f"Practical guidance on {title.lower()}. " * 14
    y = 110
    for line in textwrap.wrap(body, 88)[:30]:
        page.insert_text((60, y), line, fontsize=10); y += 15
out = pathlib.Path("/tmp/m4_handbook.pdf")
doc.save(out)
print(f"{out}: {doc.page_count} pages, needle on page 25")
doc.close()
PY

printf '# Unrelated Notes\n\nThe office plant needs watering twice a week.\n' > /tmp/m4_decoy.md
```

Create the notebook, upload both, wait for `ready`, and record the ids. Fail
fast and stop if ingestion does not reach `ready` — every later check depends
on it.

## R — Retrieval

- **R1 needle** — ask the paraphrased question ("What pressure setting is the
  regulator calibrated to?"). The needle passage must be source [1], and its
  page range must cover page 25.
- **R2 hybrid actually fuses** — in R1's `sources`, check `vector_rank` and
  `keyword_rank`. At least one source must have **both** non-null. If every
  source has `keyword_rank: null`, the keyword half is contributing nothing —
  that is a real bug, not a tuning question. Report it.
- **R3 keyword rescue** — ask using an exact identifier ("Kestrel Mark VII").
  The needle must rank first, and its score should be roughly double a
  vector-only hit's, because it is ranked by both methods.
- **R4 source filter** — repeat R1 with `document_ids` set to the decoy
  markdown only. Every returned source must be from the decoy, and none from
  the PDF. A filter that silently does nothing is the failure to look for.
- **R5 empty filter** — send `"document_ids": []`. It must retrieve nothing,
  not fall back to searching everything.
- **R6 junk query** — ask something made only of punctuation. It must not 500.

## G — Grounded answering

- **G1 cites correctly** — R1's answer must contain 47.3 kilopascals and a
  `[n]` marker whose source actually contains "Kestrel". Read the source text
  and confirm, rather than trusting that a marker exists.
- **G2 refuses what is not there** — ask something the corpus cannot answer
  ("What was the company's 2024 revenue and who is the CFO?"). The answer must
  say it is not covered. If it invents a figure, that is the most serious
  possible failure for this tab — report it loudly.
- **G3 no sources at all** — create an empty notebook and ask a question. The
  answer must explain there is nothing to search, and the stream must still end
  with `event: done`, not hang or error.
- **G4 citations are in range** — no `[n]` in any answer may exceed the number
  of sources offered. Extract every marker and check.
- **G5 follow-up keeps context** — ask R1, then ask "and what temperature limit
  applies?" on the same `conversation_id`. The follow-up must be answered
  without repeating the subject, showing history reached the model.

## P — Persistence

- **P1 conversation is notebook-scoped** — `GET /api/notebooks/{id}/conversations`
  lists the thread created by R1, and the chat tab's `GET /api/conversations`
  does **not** mix it in as an ordinary conversation.
- **P2 sources are stored on the message** — fetch the conversation and confirm
  the assistant message's `metadata.sources` holds the passages and
  `metadata.cited` the numbers actually used. Re-retrieving later could return
  different passages, so stored sources are what make old citations stable.
- **P3 deleting the notebook removes its conversations** — after deleting the
  notebook, its conversations must be gone (cascade), not orphaned.

## U — UI (skip if asked for "api only")

Drive the real page in headless Chrome. A passing build proves nothing about
interaction — this project has shipped three silently broken menu handlers.

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless --disable-gpu --remote-debugging-port=9223 \
  --user-data-dir=/tmp/m4_chrome "http://localhost:3000/notebooks?n=$NB" &
```

Drive it over the DevTools protocol with a small node script (node 24 has a
global WebSocket; connect to the page's `webSocketDebuggerUrl` from
`http://127.0.0.1:9223/json`). React inputs ignore a plain `.value =`, so set
values through the native setter and dispatch an `input` event.

- **U1 ask flow** — type a question, press the ask button, and confirm answer
  text appears.
- **U2 citation markers render** — the answer shows clickable `[n]` buttons,
  not literal bracket text.
- **U3 source panel opens** — clicking a citation opens the panel and shows the
  passage text for that source.
- **U4 source toggle** — unchecking a document must exclude it; ask again and
  confirm the returned sources no longer include it.
- **U5 no console errors** — collect `Runtime.consoleAPICalled` and
  `Runtime.exceptionThrown` during the run. An uncaught error blanks the page
  in this codebase, so report any.

Kill Chrome when done (`pkill -f "remote-debugging-port=9223"`).

## Suite

```bash
cd /Users/vishnumadala/Desktop/Projects/vish_ai_assistant/backend && uv run pytest -q
```

## Output

Report a table of every check id with PASS / FAIL / SKIP and one line of
evidence each. Then, for each failure:

- what you ran, and the actual output
- whether you think it is a bug or a model limitation, and why
- the narrowest file you would look at first

End with an explicit cleanup confirmation listing what you deleted.
