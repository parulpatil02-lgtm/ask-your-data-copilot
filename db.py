"""
Read-only access to the Chinook database, plus the guardrail that makes
this safe to point an LLM at: the generated SQL is validated before it
ever touches the database, and the connection itself is opened read-only
so even a validation bug can't cause damage.
"""
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "chinook.db"
MAX_ROWS = 200

# Anything other than a single SELECT statement is rejected outright.
BLOCKED_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|"
    r"PRAGMA|VACUUM|TRUNCATE|GRANT|REINDEX)\b",
    re.IGNORECASE,
)


def get_connection():
    # uri=True + mode=ro opens the file such that write statements fail at
    # the SQLite engine level, independent of the keyword check above.
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def get_schema_description() -> str:
    """Introspects the database and returns a compact schema description
    (tables, columns, foreign keys) to give the LLM as context."""
    conn = get_connection()
    lines = []
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    for table in tables:
        cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        col_names = [c[1] for c in cols]
        lines.append(f"{table}({', '.join(col_names)})")

        fks = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        for fk in fks:
            # fk columns: id, seq, table, from, to, ...
            lines.append(f"  -- {table}.{fk[3]} references {fk[2]}.{fk[4]}")
    conn.close()
    return "\n".join(lines)


class SQLValidationError(Exception):
    pass


def clean_sql(raw_sql: str) -> str:
    """Strips markdown code fences the model might add despite instructions."""
    sql = raw_sql.strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip().rstrip(";").strip()


def _strip_literals_and_comments(sql: str) -> str:
    """Removes comments and quoted text so the safety checks look only at
    real SQL, not at data the user is searching for (e.g. LIKE '%Create%')."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    sql = re.sub(r'"(?:[^"]|"")*"', '""', sql)
    return sql


def validate_sql(sql: str) -> str:
    """Raises SQLValidationError if the query isn't a safe, single SELECT."""
    sql = clean_sql(sql)
    if not sql:
        raise SQLValidationError("The model didn't return a query.")
    scan = _strip_literals_and_comments(sql)
    if ";" in scan:
        raise SQLValidationError("Only a single statement is allowed (no ';' inside the query).")
    if not re.match(r"^\s*(WITH|SELECT)\b", scan, re.IGNORECASE):
        raise SQLValidationError("Only SELECT (or WITH ... SELECT) statements are allowed.")
    match = BLOCKED_KEYWORDS.search(scan)
    if match:
        raise SQLValidationError(f"Query contains a blocked keyword: {match.group(0)!r}.")
    return sql


def get_reference_date() -> str:
    """The most recent invoice date, used as 'today' for relative-time
    questions -- this is a static dataset, so the real current date would
    make 'last year' meaningless."""
    conn = get_connection()
    try:
        return conn.execute("SELECT date(MAX(InvoiceDate)) FROM Invoice").fetchone()[0]
    finally:
        conn.close()


def run_query(sql: str):
    """Validates, then executes, returning (dataframe, executed_sql)."""
    import pandas as pd

    safe_sql = validate_sql(sql)
    conn = get_connection()
    try:
        df = pd.read_sql_query(f"SELECT * FROM ({safe_sql}) LIMIT {MAX_ROWS}", conn)
    finally:
        conn.close()
    return df, safe_sql
