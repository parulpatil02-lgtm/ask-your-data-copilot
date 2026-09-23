"""
Golden-answer regression suite. Each question is paired with a
hand-written SQL query whose result is the ground truth; the agent's answer
must match it. Run this after ANY change to the prompts -- fixing one
question can silently break another (this suite exists because that
happened).

Run:  python test_golden.py              (needs GEMINI_API_KEY)
      python test_golden.py --baseline   same questions, but with a bare schema-only
                                         prompt, to measure what the prompt engineering added
"""
import statistics
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from agent import _generate, nl_to_sql, summarize_result
from db import connect, get_schema_description, run_query

BASELINE_PROMPT = (
    "You are a SQL assistant for a SQLite database.\nSchema:\n{schema}\n"
    "Return one SQLite SELECT query, nothing else."
)


def truth(sql):
    with connect() as conn:
        return conn.execute(sql).fetchall()


def col0_list(df):
    return [str(v) for v in df.iloc[:, 0].tolist()]


def scalar(df):
    return float(df.iloc[0, 0])


CASES = [
    dict(
        q="What are the top 5 best-selling genres by revenue?",
        expected=lambda: [r[0] for r in truth(
            "SELECT g.Name FROM Genre g JOIN Track t ON t.GenreId=g.GenreId "
            "JOIN InvoiceLine il ON il.TrackId=t.TrackId GROUP BY g.GenreId "
            "ORDER BY SUM(il.UnitPrice*il.Quantity) DESC LIMIT 5")],
        actual=col0_list,
        same=lambda a, e: a == e,
    ),
    dict(
        q="Which 5 customers have spent the most money in total?",
        expected=lambda: [f"{r[0]} {r[1]}" for r in truth(
            "SELECT c.FirstName, c.LastName FROM Customer c JOIN Invoice i "
            "ON i.CustomerId=c.CustomerId GROUP BY c.CustomerId "
            "ORDER BY SUM(i.Total) DESC LIMIT 5")],
        actual=lambda df: [f"{a} {b}" for a, b in zip(df.iloc[:, 0], df.iloc[:, 1])],
        same=lambda a, e: a == e,
    ),
    dict(
        q="Which country has the highest average invoice total?",
        expected=lambda: truth(
            "SELECT BillingCountry FROM Invoice GROUP BY BillingCountry "
            "ORDER BY AVG(Total) DESC LIMIT 1")[0][0],
        actual=lambda df: str(df.iloc[0, 0]),
        same=lambda a, e: a == e,
    ),
    dict(
        q="Which artists have never sold a single track?",
        expected=lambda: {r[0] for r in truth(
            "SELECT Name FROM Artist WHERE ArtistId NOT IN (SELECT a.ArtistId FROM Album a "
            "JOIN Track t ON t.AlbumId=a.AlbumId JOIN InvoiceLine il ON il.TrackId=t.TrackId)")},
        actual=lambda df: set(col0_list(df)),
        same=lambda a, e: a == e,
    ),
    dict(
        q="What is the average number of tracks per playlist?",
        expected=lambda: 1.0 * truth("SELECT COUNT(*) FROM PlaylistTrack")[0][0]
        / truth("SELECT COUNT(*) FROM Playlist")[0][0],
        actual=scalar,
        same=lambda a, e: abs(a - e) < 0.01,
    ),
    dict(
        q="What percentage of our revenue comes from the top 3 genres?",
        expected=lambda: truth(
            "WITH r AS (SELECT SUM(il.UnitPrice*il.Quantity) v FROM Genre g "
            "JOIN Track t ON t.GenreId=g.GenreId JOIN InvoiceLine il ON il.TrackId=t.TrackId "
            "GROUP BY g.GenreId) "
            "SELECT 100.0*(SELECT SUM(v) FROM (SELECT v FROM r ORDER BY v DESC LIMIT 3))"
            "/(SELECT SUM(v) FROM r)")[0][0],
        actual=scalar,
        same=lambda a, e: abs(a - e) < 0.05,
    ),
    dict(
        q="How many customers have not made a purchase in the last year?",
        expected=lambda: truth(
            "SELECT COUNT(*) FROM Customer WHERE CustomerId NOT IN (SELECT CustomerId FROM Invoice "
            "WHERE InvoiceDate >= date((SELECT MAX(InvoiceDate) FROM Invoice), '-1 year'))")[0][0],
        actual=scalar,
        same=lambda a, e: a == e,
    ),
    dict(
        q="Which sales rep (support rep) generated the most revenue?",
        expected=lambda: truth(
            "SELECT e.LastName FROM Employee e JOIN Customer c ON c.SupportRepId=e.EmployeeId "
            "JOIN Invoice i ON i.CustomerId=c.CustomerId GROUP BY e.EmployeeId "
            "ORDER BY SUM(i.Total) DESC LIMIT 1")[0][0],
        actual=lambda df: " ".join(str(v) for v in df.iloc[0].tolist() if isinstance(v, str)),
        same=lambda a, e: e in a,
    ),
    dict(
        q="How many tracks have the word Create in the title?",
        expected=lambda: truth("SELECT COUNT(*) FROM Track WHERE Name LIKE '%Create%'")[0][0],
        actual=scalar,
        same=lambda a, e: a == e,
    ),
]


def main():
    baseline = "--baseline" in sys.argv
    schema = get_schema_description()
    failures, timings = 0, []
    for case in CASES:
        q = case["q"]
        try:
            start = time.perf_counter()
            if baseline:
                raw = _generate(f"Question: {q}", system=BASELINE_PROMPT.format(schema=schema))
            else:
                raw = nl_to_sql(q, schema)
            df, sql = run_query(raw)
            got = case["actual"](df)
            if not baseline:  # full user-facing pipeline includes the written answer
                summarize_result(q, sql, df)
                timings.append(time.perf_counter() - start)
            want = case["expected"]()
            ok = case["same"](got, want)
        except Exception as e:
            ok, got, want = False, f"{type(e).__name__}: {str(e)[:120]}", "-"
        if ok:
            print(f"PASS  {q}")
        else:
            failures += 1
            preview = str(got)[:110]
            print(f"FAIL  {q}\n      got:      {preview}\n      expected: {str(want)[:110]}")
        time.sleep(4)

    mode = "BASELINE (schema-only prompt)" if baseline else "FINAL prompt"
    print(f"\n[{mode}] {len(CASES) - failures}/{len(CASES)} passed")
    if timings:
        print(f"end-to-end per question (SQL + run + written answer): "
              f"median {statistics.median(timings):.1f}s, max {max(timings):.1f}s")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
