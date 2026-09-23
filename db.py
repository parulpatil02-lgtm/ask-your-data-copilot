"""
Read-only access to the Chinook database, plus the guardrail that makes it
safe to point an LLM at: generated SQL is validated before it touches the
database, and the connection is opened read-only so even a validation bug
cannot cause damage.
"""
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent / "data" / "chinook.db"
MAX_ROWS = 200

# REPLACE is deliberately absent: it's a common string function, and
# "REPLACE INTO" is already rejected because it doesn't start with SELECT.
BLOCKED_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|GRANT|REINDEX)\b",
    re.IGNORECASE,
)


class SQLValidationError(Exception):
    pass


@contextmanager
def connect():
    # mode=ro makes write statements fail inside SQLite itself, independent
    # of the validator below.
    conn = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


def get_schema_description() -> str:
    """Tables, columns and foreign keys, as context for the LLM."""
    lines = []
    with connect() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            columns = [c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')]
            lines.append(f"{table}({', '.join(columns)})")
            for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
                lines.append(f"  -- {table}.{fk[3]} references {fk[2]}.{fk[4]}")
    return "\n".join(lines)


def get_reference_date() -> str:
    """Latest invoice date, used as 'today': the dataset is static, so the
    real current date would make 'last year' meaningless."""
    with connect() as conn:
        return conn.execute("SELECT date(MAX(InvoiceDate)) FROM Invoice").fetchone()[0]


def clean_sql(raw_sql: str) -> str:
    """Strips markdown fences and a trailing semicolon the model may add."""
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", raw_sql.strip(), flags=re.IGNORECASE)
    return sql.strip().rstrip(";").strip()


def _strip_literals_and_comments(sql: str) -> str:
    """Leaves only real SQL so the checks don't misfire on searched text,
    e.g. LIKE '%Create%'."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return re.sub(r'"(?:[^"]|"")*"', '""', sql)


def validate_sql(sql: str) -> str:
    """Returns the cleaned SQL, or raises SQLValidationError unless it is a
    single SELECT (or WITH ... SELECT)."""
    sql = clean_sql(sql)
    if not sql:
        raise SQLValidationError("The model didn't return a query.")
    scan = _strip_literals_and_comments(sql)
    if ";" in scan:
        raise SQLValidationError("Only a single statement is allowed (no ';' inside the query).")
    if not re.match(r"\s*(WITH|SELECT)\b", scan, re.IGNORECASE):
        raise SQLValidationError("Only SELECT (or WITH ... SELECT) statements are allowed.")
    if match := BLOCKED_KEYWORDS.search(scan):
        raise SQLValidationError(f"Query contains a blocked keyword: {match.group(0)!r}.")
    return sql


def run_query(sql: str) -> tuple[pd.DataFrame, str]:
    """Validates, then executes with a row cap. Returns (dataframe, sql)."""
    safe_sql = validate_sql(sql)
    # Newlines around the query stop a trailing '-- comment' from swallowing
    # the closing parenthesis.
    with connect() as conn:
        df = pd.read_sql_query(f"SELECT * FROM (\n{safe_sql}\n) LIMIT {MAX_ROWS}", conn)
    return df, safe_sql
