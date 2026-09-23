"""
The two LLM calls that make up the agent: one turns a plain-English
question into SQL, the other turns a query result back into a plain-English
answer. Kept as two small, separately-testable calls rather than one
do-everything prompt.
"""
import os
from functools import lru_cache

from google import genai

from db import get_reference_date

MODEL = "gemini-flash-lite-latest"

AGENT_NAME = "Melody"


def _get_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        import streamlit as st

        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return None


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "No GEMINI_API_KEY found. Set it in a local .env file (see .env.example) "
            "or, when deployed, in Streamlit's Secrets."
        )
    return genai.Client(api_key=api_key)


def build_system_prompt(schema: str) -> str:
    reference_date = get_reference_date()
    return f"""You are {AGENT_NAME}, a SQL-writing assistant for a digital music
store's SQLite database (the Chinook sample database).

Database schema (table(columns), with foreign keys noted as comments):
{schema}

Rules:
- Write exactly ONE SQLite SELECT statement (or WITH ... SELECT) that answers
  the user's question. Nothing else -- no explanation, no markdown fences,
  no trailing semicolon.
- Only use tables and columns that appear in the schema above.
- Prefer explicit JOINs over implicit ones, and use the foreign-key
  relationships noted in the schema.
- Always include the measure being asked about (a total, count, average)
  as a column next to its label, with a readable alias, and ORDER BY it
  descending whenever the question implies ranking ("top", "most",
  "best", "highest") or asks for a count/total per group.
- Revenue is SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity); a
  customer's total spend is SUM(Invoice.Total).
- This is a static dataset. Treat {reference_date} (the most recent invoice
  date) as "today" for relative time expressions such as "last year" or
  "this month". Never use date('now').
- For counts or averages "per" some entity (per playlist, per customer, per
  artist), start from that entity's own table and LEFT JOIN the related
  rows, so entities with zero related rows count as zero instead of
  disappearing from the average. This applies to per-entity counts and
  averages only.
- For "never", "no", or "without" questions (e.g. artists with no sales),
  use NOT EXISTS or NOT IN with a subquery. Do NOT chain LEFT JOINs and
  filter on NULL -- that returns wrong, duplicated rows.
- If the question is ambiguous, make the most reasonable interpretation for
  a business user rather than asking for clarification.
- If the question genuinely cannot be answered from this schema (e.g. it
  asks about data that doesn't exist here), respond with exactly:
  NO_QUERY: <one short sentence explaining why>
"""


def nl_to_sql(question: str, schema: str) -> str:
    client = get_client()
    system_prompt = build_system_prompt(schema)
    response = client.models.generate_content(
        model=MODEL,
        contents=f"{system_prompt}\n\nQuestion: {question}",
    )
    return (response.text or "").strip()


def summarize_result(question: str, sql: str, df) -> str:
    client = get_client()
    rows = df.to_string(index=False) if not df.empty else "(no rows returned)"
    prompt = f"""You are {AGENT_NAME}. A user asked: "{question}"

This SQL was run:
{sql}

It returned {len(df)} row(s) (results are capped at 200). Full result:
{rows}

In ONE short, plain-English sentence (no greeting or preamble, include the
key figures), answer the user's original question using ONLY the rows above. Any claim about a maximum, minimum, ranking or
total must be checked against every row shown, not just the first few. No
SQL, no column names verbatim, just the answer a business person would want
to hear."""
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return (response.text or "").strip()
