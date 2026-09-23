# Melody — Ask-Your-Data Copilot

**Non-technical stakeholders type a question in plain English; Melody writes the
SQL, runs it read-only, and answers in one sentence with a chart — and the SQL is
always one click away, so anyone can audit it.**

Built to remove a familiar bottleneck: business users waiting days in an analyst's
queue for one-off SQL pulls.

## How it works

```
question ──► Gemini writes one SELECT ──► validator ──► read-only SQLite ──► rows (capped at 200)
                                                                                  │
                          chart + one-sentence answer + "View generated SQL" ◄────┘
```

1. **Text-to-SQL.** The model receives the live database schema (tables, columns,
   foreign keys) plus business rules (how revenue is defined, what "today" means).
2. **Validation.** Only a single `SELECT` / `WITH … SELECT` passes. Comments and
   quoted text are stripped before scanning, so searching for a track called
   "Create" isn't mistaken for a `CREATE` statement.
3. **Read-only execution.** The database is opened in SQLite's `mode=ro`, so even
   if validation had a bug the engine itself refuses writes.
4. **Answer from the full result.** The summary is written from every returned row
   (not a preview), then shown with an auto-chosen chart.

## Safety, and how it's tested

Two independent layers: the validator, and an engine-level read-only connection.
`test_guardrails.py` runs 19 checks — DROP/DELETE/UPDATE/INSERT/ATTACH/PRAGMA,
multi-statement injection, comment-hidden statements, `WITH … DELETE` — plus a
direct attempt to write through the connection. The model itself also refused
both destructive requests I tried ("delete every customer", "drop the Invoice
table"), but the design doesn't rely on that.

## Accuracy, and how it's tested

`test_golden.py` pairs 9 business questions with hand-written SQL as ground truth
and compares results (not wording). **27/27 across three consecutive runs.**
Honest scope: 9 questions on one database is a small sample, and LLM output is
non-deterministic, so this catches regressions — it doesn't prove correctness in
general.

## Failure log: what went wrong while building this

Found by deliberately trying to break it, then verifying against the raw data.

| # | What happened | Root cause | Fix |
|---|---|---|---|
| 1 | "Average tracks per playlist" returned **622.5**; correct is **484.2** | Counted from the link table, silently dropping the 4 empty playlists from the denominator | Prompt rule: per-entity averages start from the entity's own table with a LEFT JOIN |
| 2 | After fix #1, "artists who never sold a track" broke: returned AC/DC, Aerosmith, Led Zeppelin (who have sold plenty) | The new LEFT JOIN rule was over-applied; chained joins + `IS NULL` produced duplicated wrong rows, and the summary reported them confidently | Scoped the rule to per-entity averages; "never/without" questions use `NOT EXISTS`/`NOT IN`. Added the golden suite so this can't recur silently |
| 3 | A legitimate search (`LIKE '%Create%'`) was **blocked** | Keyword scan matched inside a quoted string | Validator strips string literals and comments before scanning |
| 4 | "Customers inactive in the last year" was refused | Static dataset has no "now"; the model correctly declined to guess | Inject the latest invoice date (2025-12-22) as "today" |
| 5 | The answer to "which country has the highest average invoice" was right **by luck** | The summarizer only saw the first 10 rows but made claims about all 24 | Summarizer now receives every row and is told to check any max/min/ranking claim against all of them |

## Known limitations (not fixed)

- **Ambiguous terms are resolved silently.** "Most popular genre" is interpreted as
  tracks sold, returned with `LIMIT 1`, so the runner-up is hidden. The SQL toggle
  makes the interpretation visible, but the answer doesn't volunteer it.
- **Small evaluation set** (9 golden questions, one database). Multi-step and
  multi-part questions are largely untested.
- **Model alias.** It uses `gemini-flash-lite-latest`; when Google moves the alias,
  behaviour can change. Re-run `test_golden.py` after any such change.
- **Free-tier quota.** The public demo runs on a free API tier, so it can be
  rate-limited; sessions are capped at 20 questions and the app says so plainly.
- **Results capped at 200 rows.**
- **The keyword validator is a heuristic.** The read-only connection is the real
  protection.

## Run it locally

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # then put your free Gemini key in .env
.venv\Scripts\python -m streamlit run app.py
```

Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
Restart the server after editing `agent.py` or `db.py` — Streamlit doesn't reliably
reload helper modules.

```bash
.venv\Scripts\python test_guardrails.py   # no API key needed
.venv\Scripts\python test_golden.py       # needs a key
```

## Stack

Python · Streamlit · Google Gemini (`google-genai`) · SQLite · pandas · matplotlib

Data: the public [Chinook](https://github.com/lerocha/chinook-database) sample
database (a digital music store) — dates are as shipped with it.
