"""
Melody: ask-your-data copilot for a digital music store.

A non-technical user types a question in English; this turns it into SQL,
runs it against a read-only database, and answers back in English with the
SQL available on request for anyone who wants to audit it.
"""
import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

matplotlib.use("Agg")
load_dotenv()  # picks up GEMINI_API_KEY from a local .env file; no-op if none exists

from agent import AGENT_NAME, nl_to_sql, summarize_result
from db import SQLValidationError, get_schema_description, run_query

EXAMPLE_QUESTIONS = [
    "What are the top 5 best-selling genres by revenue?",
    "Which 5 customers have spent the most money in total?",
    "How many tracks are there in each genre?",
    "What's the average invoice total by country?",
    "Who are the top 3 artists by number of tracks sold?",
]

st.set_page_config(page_title=f"{AGENT_NAME} — Ask Your Data", page_icon="🎵", layout="centered")


@st.cache_data(show_spinner=False)
def cached_schema() -> str:
    return get_schema_description()


def render_result(df: pd.DataFrame):
    if df.empty:
        st.info("Query ran successfully but returned no rows.")
        return

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    text_cols = [c for c in df.columns if c not in numeric_cols]

    if len(df.columns) == 2 and len(numeric_cols) == 1 and len(text_cols) == 1 and len(df) > 1:
        label_col, value_col = text_cols[0], numeric_cols[0]
        fig, ax = plt.subplots(figsize=(6, 3.2))
        ax.bar(df[label_col].astype(str), df[value_col], color="#1F6F5C")
        ax.set_ylabel(value_col)
        ax.spines[["top", "right"]].set_visible(False)
        plt.xticks(rotation=30, ha="right")
        fig.tight_layout()
        st.pyplot(fig)
    else:
        st.dataframe(df, use_container_width=True)


st.title(f"🎵 {AGENT_NAME}")
st.caption(
    "Ask a question about a digital music store's sales data in plain English. "
    f"{AGENT_NAME} writes the SQL, runs it read-only, and shows its work."
)

with st.expander("Example questions"):
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, key=f"ex_{q}"):
            st.session_state["question_input"] = q

question = st.text_input(
    "Your question",
    key="question_input",
    placeholder="e.g. What are the top 5 best-selling genres by revenue?",
)
ask = st.button("Ask", type="primary")

MAX_QUESTIONS_PER_SESSION = 20  # protects the free API quota on a public deployment


def friendly_llm_error(e: Exception) -> str:
    text = str(e)
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
        return (
            f"{AGENT_NAME} has hit the free API quota for now. This demo runs on a free "
            "tier, so please try again in a few minutes (or tomorrow)."
        )
    return f"Couldn't reach the language model: {text[:200]}"


if ask and question.strip():
    st.session_state["asked"] = st.session_state.get("asked", 0) + 1
    if st.session_state["asked"] > MAX_QUESTIONS_PER_SESSION:
        st.warning(
            f"This demo is limited to {MAX_QUESTIONS_PER_SESSION} questions per session to "
            "stay within a free API quota. Refresh the page to start a new session."
        )
        st.stop()

    schema = cached_schema()

    with st.spinner(f"{AGENT_NAME} is writing the query..."):
        try:
            raw_sql = nl_to_sql(question, schema)
        except Exception as e:
            st.error(friendly_llm_error(e))
            st.stop()

    if raw_sql.upper().startswith("NO_QUERY"):
        reason = raw_sql.split(":", 1)[1].strip() if ":" in raw_sql else "This can't be answered from this data."
        st.warning(f"**{AGENT_NAME} can't answer that:** {reason}")
        st.stop()

    try:
        df, safe_sql = run_query(raw_sql)
    except SQLValidationError as e:
        st.error(f"**Blocked before it ran** — this query didn't pass the safety check: {e}")
        with st.expander("What the model generated (blocked)"):
            st.code(raw_sql, language="sql")
        st.stop()
    except Exception as e:
        st.error(f"The query failed to run against the database: {e}")
        with st.expander("Generated SQL"):
            st.code(raw_sql, language="sql")
        st.stop()

    with st.spinner("Summarizing..."):
        try:
            answer = summarize_result(question, safe_sql, df)
        except Exception:
            answer = f"Found {len(df)} row(s) — see the table below."

    st.subheader(answer.replace("$", "\\$"))  # bare $ would render as LaTeX math
    render_result(df)

    with st.expander("View generated SQL (for auditability)"):
        st.code(safe_sql, language="sql")
        st.caption(f"{len(df)} row(s) returned, capped at 200 for display.")

elif ask:
    st.warning("Type a question first.")
