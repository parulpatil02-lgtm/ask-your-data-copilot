"""
Tests for the safety layer. No API key needed: these run the validator and
the read-only connection directly.

Run:  python test_guardrails.py
"""
import sqlite3

from db import SQLValidationError, get_connection, validate_sql

MUST_BLOCK = [
    "DROP TABLE Customer",
    "DELETE FROM Invoice WHERE 1=1",
    "UPDATE Customer SET Email='x'",
    "INSERT INTO Artist(Name) VALUES ('x')",
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
    "SELECT 'a;b' AS semicolon_in_text",
    "WITH t AS (SELECT * FROM Track) SELECT COUNT(*) FROM t",
    "SELECT Name FROM Artist -- just a comment\nLIMIT 2",
    "```sql\nSELECT COUNT(*) FROM Customer\n```",
]


def main():
    failures = 0

    for sql in MUST_BLOCK:
        try:
            validate_sql(sql)
            print(f"FAIL  (should block): {sql!r}")
            failures += 1
        except SQLValidationError:
            print(f"ok    blocked: {sql!r}")

    for sql in MUST_ALLOW:
        try:
            validate_sql(sql)
            print(f"ok    allowed: {sql!r}")
        except SQLValidationError as e:
            print(f"FAIL  (should allow): {sql!r} -> {e}")
            failures += 1

    # Second layer: even if validation were bypassed, the database itself
    # is opened read-only and must refuse writes.
    conn = get_connection()
    try:
        conn.execute("INSERT INTO Artist(Name) VALUES ('should not work')")
        print("FAIL  read-only connection accepted a write")
        failures += 1
    except sqlite3.OperationalError as e:
        print(f"ok    engine-level read-only enforced: {e}")
    finally:
        conn.close()

    print(f"\n{'ALL PASSED' if failures == 0 else str(failures) + ' FAILURE(S)'}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
