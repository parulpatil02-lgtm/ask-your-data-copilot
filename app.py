"""
Melody: ask-your-data copilot for a digital music store.

A non-technical user types a question in English; Melody turns it into SQL,
runs it against a read-only database, and answers in English, with the SQL
available on request for anyone who wants to audit it.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agent import AGENT_NAME, nl_to_sql, summarize_result
from db import MAX_ROWS, SQLValidationError, get_schema_description, run_query

load_dotenv()  # local GEMINI_API_KEY from .env; a no-op when deployed

MAX_QUESTIONS_PER_SESSION = 20  # protects the free API quota on a public deployment
ACCENT = "#1F6F5C"
EXAMPLE_QUESTIONS = [
    "What are the top 5 best-selling genres by revenue?",
    "Which 5 customers have spent the most money in total?",
    "How many tracks are there in each genre?",
    "What's the average invoice total by country?",
    "Who are the top 3 artists by number of tracks sold?",
]

st.set_page_config(page_title=f"{AGENT_NAME} - Ask Your Data", page_icon="🎵", layout="centered")


@st.cache_data(show_spinner=False)
def cached_schema() -> str:
    return get_schema_description()


def friendly_llm_error(e: Exception) -> str:
    text = str(e)
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
        return (
            f"{AGENT_NAME} has hit the free API quota for now. This demo runs on a free "
            "tier, so please try again in a few minutes (or tomorrow)."
        )
    return f"Couldn't reach the language model: {text[:200]}"


def render_result(df: pd.DataFrame):
    if df.empty:
        st.info("The query ran successfully but returned no rows.")
        return

    numeric = df.select_dtypes(include="number").columns.tolist()
    labels = [c for c in df.columns if c not in numeric]

    if len(df) > 1 and len(numeric) == 1 and len(labels) == 1:
        fig, ax = plt.subplots(figsize=(6, 3.2))
        ax.bar(df[labels[0]].astype(str), df[numeric[0]], color=ACCENT)
        ax.set_ylabel(numeric[0])
        ax.spines[["top", "right"]].set_visible(False)
        plt.xticks(rotation=30, ha="right")
        fig.tight_layout()
        st.pyplot(fig)
        plt.close(fig)
    else:
        st.dataframe(df, width="stretch")


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

if st.button("Ask", type="primary"):
    if not question.strip():
        st.warning("Type a question first.")
        st.stop()

    st.session_state["asked"] = st.session_state.get("asked", 0) + 1
    if st.session_state["asked"] > MAX_QUESTIONS_PER_SESSION:
        st.warning(
            f"This demo is limited to {MAX_QUESTIONS_PER_SESSION} questions per session to "
            "stay within a free API quota. Refresh the page to start a new session."
        )
        st.stop()

    with st.spinner(f"{AGENT_NAME} is writing the query..."):
        try:
            raw_sql = nl_to_sql(question, cached_schema())
        except Exception as e:
            st.error(friendly_llm_error(e))
            st.stop()

    if raw_sql.upper().startswith("NO_QUERY"):
        reason = raw_sql.partition(":")[2].strip() or "This can't be answered from this data."
        st.warning(f"**{AGENT_NAME} can't answer that:** {reason}")
        st.stop()

    try:
        df, safe_sql = run_query(raw_sql)
    except SQLValidationError as e:
        st.error(f"**Not run** - the generated query failed the safety check: {e}")
        with st.expander("What the model generated"):
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
            answer = f"Found {len(df)} row(s) - see below."

    st.subheader(answer.replace("$", "\\$"))  # a bare $ would render as LaTeX math
    render_result(df)

    with st.expander("View generated SQL (for auditability)"):
        st.code(safe_sql, language="sql")
        st.caption(f"{len(df)} row(s) returned (display capped at {MAX_ROWS}).")
