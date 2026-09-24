# Melody - Ask-Your-Data Copilot

**Business users ask a question in plain English and get a checked answer in
seconds - without joining the analyst queue, and without an AI quietly guessing.**

**Try it live: [melody-ask-your-data.streamlit.app](https://melody-ask-your-data.streamlit.app/)**
It runs on a free API tier, so it can be slow to wake up, and briefly unavailable
when Google's free model is overloaded (it says so rather than hanging).

## The business problem

Every analytics team has the same bottleneck: business users need one-off numbers,
only analysts can write the SQL, so requests pile up and people wait. The obvious
fix - let an LLM write the SQL - creates two new problems: it can be confidently
*wrong*, and it can run something destructive. Melody is an attempt to remove the
queue without either risk.

## The data

The public Chinook database, a digital music store: 11 tables, **59 customers, 412
invoices, 2,240 invoice lines, 3,503 tracks and 275 artists**, with invoices from
2021-01-01 to 2025-12-22 and $2,328.60 in revenue (dates as shipped with the
sample).

## What it surfaced

Questions a stakeholder would ask, answered in one sentence with a chart, each
checked against hand-written SQL:

- **Rock alone is 35.5% of revenue**, and the top three genres are 63.1%.
- **One sales rep generated 35.8% of revenue** (Jane Peacock, $833.04).
- **110 of 275 artists (40%) have never sold a track.**
- **13 of 59 customers (22%) made no purchase in the 12 months to 2025-12-22.**

The more important finding was about the agent itself: **an LLM SQL agent fails
silently in specific, predictable ways.** "Average tracks per playlist" came back
**622.5**; the true answer is **484.2** (it dropped 4 empty playlists from the
denominator). It read as a perfectly reasonable answer, which is what makes it
dangerous. The [failure log](#failure-log-what-went-wrong-while-building-this) below
covers eight problems found while building and deploying it.

## What changed

Measured with `test_golden.py`: 9 business questions, each with a hand-written SQL
query as ground truth.

| | Naive baseline (schema-only prompt) | Melody |
|---|---|---|
| Correct answers | **7 / 9** in both runs - but *different* questions failed each run | **9 / 9** on every run (4 runs) |
| Example of a wrong answer | Avg tracks per playlist: 622.5 (true: 484.2); a percentage query that errored | Both fixed and covered by tests |
| Time to a written answer | - | **median 1.6 s, max 3.4 s** (SQL + run + summary) |
| Destructive or out-of-scope requests | - | Refused or blocked; database opened read-only; 23 safety checks pass |

Two honest notes. The baseline's failures *moving around* between runs is itself
the point: without ground-truth tests you can't tell which answers are wrong. And
the baseline is my own naive prompt, not a published system, on 9 questions and one
database; the timing excludes any queue time, which I didn't measure.

## How it works

```
question ──► Gemini writes one SELECT ──► validator ──► read-only SQLite ──► rows (capped at 200)
                                                                                  │
                          chart + one-sentence answer + "View generated SQL" ◄────┘
```

1. **Text-to-SQL.** The model receives the live database schema (tables, columns,
   foreign keys) plus business rules (how revenue is defined, what "today" means)
   as a system instruction, kept separate from the user's text.
2. **Validation.** Only a single `SELECT` / `WITH … SELECT` passes. Comments and
   quoted text are stripped before scanning, so searching for a track called
   "Create" isn't mistaken for a `CREATE` statement.
3. **Read-only execution.** The database is opened in SQLite's `mode=ro`, so even
   if validation had a bug the engine itself refuses writes.
4. **Answer from the full result.** The summary is written from every returned row
   (not a preview), then shown with an auto-chosen chart. The SQL is always one
   click away, so anyone can audit it.

## Safety, and how it's tested

Two independent layers: the validator, and an engine-level read-only connection.
`test_guardrails.py` runs 23 checks - DROP/DELETE/UPDATE/INSERT/REPLACE INTO/
ATTACH/PRAGMA, multi-statement injection, comment-hidden statements,
`WITH … DELETE` - plus legitimate queries that must still work (searching for
"Create", using `REPLACE()`, ending in a comment) and a direct attempt to write
through the connection. The model itself also refused both destructive requests I
tried ("delete every customer", "drop the Invoice table"), but the design doesn't
rely on that.

## Failure log: what went wrong while building this

Rows 1-5 were found by deliberately trying to break it and checking against the
raw data; rows 6-7 came from a final line-by-line code review; row 8 came from the
first test of the live deployment.

| # | What happened | Root cause | Fix |
|---|---|---|---|
| 1 | "Average tracks per playlist" returned **622.5**; correct is **484.2** | Counted from the link table, silently dropping the 4 empty playlists from the denominator | Prompt rule: per-entity averages start from the entity's own table with a LEFT JOIN |
| 2 | After fix #1, "artists who never sold a track" broke: returned AC/DC, Aerosmith, Led Zeppelin (who have sold plenty) | The new LEFT JOIN rule was over-applied; chained joins + `IS NULL` produced duplicated wrong rows, and the summary reported them confidently | Scoped the rule to per-entity averages; "never/without" questions use `NOT EXISTS`/`NOT IN`. Added the golden suite so this can't recur silently |
| 3 | A legitimate search (`LIKE '%Create%'`) was **blocked** | Keyword scan matched inside a quoted string | Validator strips string literals and comments before scanning |
| 4 | "Customers inactive in the last year" was refused | Static dataset has no "now"; the model correctly declined to guess | Inject the latest invoice date (2025-12-22) as "today" |
| 5 | The answer to "which country has the highest average invoice" was right **by luck** | The summarizer only saw the first 10 rows but made claims about all 24 | Summarizer now receives every row and is told to check any max/min/ranking claim against all of them |
| 6 | Any query ending in a `-- comment` crashed | The row-cap wrapper put its closing `)` on the same line, so the comment swallowed it | Query is wrapped on its own lines; covered by a test |
| 7 | `REPLACE()`, a normal string function, was blocked | It sat in the keyword blocklist but added no safety: `REPLACE INTO` is already rejected (not a SELECT) and the connection is read-only | Removed from the blocklist; covered by a test |
| 8 | The live app sat spinning for minutes with no message | Google's free model returned 503 "high demand"; the SDK silently retried instead of failing | 15 s timeout, no silent retries, one fallback model, and a plain "model is overloaded, try again in a minute" message |

## Known limitations (not fixed)

- **Ambiguous terms are resolved silently.** "Most popular genre" is interpreted as
  tracks sold, returned with `LIMIT 1`, so the runner-up is hidden. The SQL toggle
  makes the interpretation visible, but the answer doesn't volunteer it.
- **Small evaluation set** (9 golden questions, one database). Multi-step and
  multi-part questions are largely untested.
- **Not fully deterministic.** Temperature is 0, yet the baseline's failures varied
  between runs, so re-run `test_golden.py` after any prompt or model change.
- **Model alias and fallback.** It uses `gemini-flash-lite-latest`, falling back to
  `gemini-flash-latest` if that is unavailable. The golden suite has only been run
  on the primary, and when Google moves an alias, behaviour can change.
- **Provider outages.** When Google's free models are overloaded, the app cannot
  answer; it now says so within about 15 seconds rather than hanging.
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
Restart the server after editing `agent.py` or `db.py` - I saw stale behaviour from
a long-running server until I did.

```bash
.venv\Scripts\python test_guardrails.py          # no API key needed
.venv\Scripts\python test_golden.py              # needs a key
.venv\Scripts\python test_golden.py --baseline   # the same questions with a bare prompt
```

## Stack

Python · Streamlit · Google Gemini (`google-genai`) · SQLite · pandas · matplotlib

Data: the public [Chinook](https://github.com/lerocha/chinook-database) sample
database.
