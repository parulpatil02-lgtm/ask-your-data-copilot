"""
Tests for the safety layer. No API key needed.

Run:  python test_guardrails.py
"""
import sqlite3

from db import SQLValidationError, connect, run_query, validate_sql

MUST_BLOCK = [
    "DROP TABLE Customer",
    "DELETE FROM Invoice WHERE 1=1",
    "UPDATE Customer SET Email='x'",
    "INSERT INTO Artist(Name) VALUES ('x')",
    "REPLACE INTO Artist(ArtistId, Name) VALUES (1, 'x')",
    "PRAGMA table_info(Customer)",
    "ATTACH DATABASE 'other.db' AS o",
    "SELECT * FROM Customer; DROP TABLE Customer",
    "SELECT 1 /* hidden */; DROP TABLE Customer",
    "-- innocent comment\nDROP TABLE Customer",
    "WITH x AS (SELECT 1) DELETE FROM Customer",
    "",
]

MUST_ALLOW = [
    "SELECT Name FROM Artist LIMIT 3",
    "SELECT Name FROM Track WHERE Name LIKE '%Create%'",
    "SELECT Name FROM Track WHERE Name LIKE '%Drop%' OR Name LIKE '%Update%'",
    "SELECT REPLACE(Name, ' ', '_') FROM Artist LIMIT 2",
    "SELECT 'a;b' AS semicolon_in_text",
    "WITH t AS (SELECT * FROM Track) SELECT COUNT(*) FROM t",
    "SELECT Name FROM Artist -- just a comment\nLIMIT 2",
    "```sql\nSELECT COUNT(*) FROM Customer\n```",
]

MUST_RUN = [  # must also execute, not just validate
    "SELECT Name FROM Artist LIMIT 2 -- ends in a comment",
    "SELECT REPLACE(Name, ' ', '_') AS n FROM Artist LIMIT 2",
]


def main():
    failures = 0

    def check(ok, label):
        nonlocal failures
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  {label}")

    for sql in MUST_BLOCK:
        try:
            validate_sql(sql)
            check(False, f"should block: {sql!r}")
        except SQLValidationError:
            check(True, f"blocked: {sql!r}")

    for sql in MUST_ALLOW:
        try:
            validate_sql(sql)
            check(True, f"allowed: {sql!r}")
        except SQLValidationError as e:
            check(False, f"should allow: {sql!r} -> {e}")

    for sql in MUST_RUN:
        try:
            df, _ = run_query(sql)
            check(len(df) == 2, f"executes: {sql!r}")
        except Exception as e:
            check(False, f"should execute: {sql!r} -> {e}")

    # Second layer: the database itself must refuse writes.
    with connect() as conn:
        try:
            conn.execute("INSERT INTO Artist(Name) VALUES ('should not work')")
            check(False, "read-only connection accepted a write")
        except sqlite3.OperationalError as e:
            check(True, f"engine-level read-only enforced: {e}")

    print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILURE(S)'}")
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
