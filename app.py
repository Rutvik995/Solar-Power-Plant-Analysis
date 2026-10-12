"""
app.py – Streamlit web application for Solar Power Plant Analysis.

Features:
  - Calls build_graph() directly
  - Query box with example question buttons
  - Answer text and warnings display
  - Tables rendered as interactive DataFrames
  - Plotly chart visualizations
  - Collapsible plan view (tasks and dependencies)
  - LLM call budget counter: 'LLM calls used: n/12'
  - Mode toggle: replay (default) or live
  - Clear, graceful HTTP 429 rate limit error messaging
"""

import json
import uuid
import pandas as pd
import plotly.io as pio
import streamlit as st
from langgraph.checkpoint.memory import MemorySaver

from solar_agent.config.settings import settings
from solar_agent.graph.data_store import DataStore
from solar_agent.graph.graph import build_graph, initial_state

st.set_page_config(page_title="Solar Plant Analyzer", layout="wide", page_icon="☀️")

# Title and header
st.title("☀️ Solar Power Plant Analysis Agent")
st.markdown("Multi-agent solar telemetry analysis powered by LangGraph.")

# Sidebar controls
with st.sidebar:
    st.header("Settings")
    mode = st.radio("Execution Mode", ["replay", "live"], index=0, help="Replay uses cached LLM responses. Live uses the configured LLM API.")
    st.divider()
    if st.button("New Conversation (Reset Context)"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.checkpointer = MemorySaver()
        st.session_state.history = []
        st.rerun()

# Initialize session state
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "checkpointer" not in st.session_state:
    st.session_state.checkpointer = MemorySaver()
if "query_text" not in st.session_state:
    st.session_state.query_text = ""

# Example questions
st.markdown("**Example Questions:**")
col1, col2, col3 = st.columns(3)
if col1.button("Plant 1 PR in Sept 2026"):
    st.session_state.query_text = "What was the PR of Plant 1 in September 2026?"
if col2.button("Find faulty inverters"):
    st.session_state.query_text = "Find faulty inverters in Plant 1 and explain why."
if col3.button("Compare soiling loss"):
    st.session_state.query_text = "Compare soiling loss between plants."

# Query input
query = st.text_input("Enter your solar plant analysis query:", value=st.session_state.query_text, key="user_query_input")
run_query = st.button("Run Analysis", type="primary")

if run_query and query.strip():
    st.divider()
    with st.spinner("Analyzing solar plant telemetry..."):
        try:
            graph = build_graph(mode=mode, checkpointer=st.session_state.checkpointer)
            config = {"configurable": {"thread_id": st.session_state.thread_id}}

            state_input = initial_state(query.strip())
            result = graph.invoke(state_input, config=config)

            # LLM calls budget display
            calls_used = result.get("llm_call_count", 0)
            max_budget = settings.max_llm_calls_per_query
            st.metric("LLM calls used", f"{calls_used}/{max_budget}")

            # Answer text
            final_ans = result.get("final_answer") or "No answer generated."
            st.subheader("Answer")
            st.markdown(final_ans)

            # Collapsible plan view
            plan = result.get("plan")
            if plan and hasattr(plan, "tasks"):
                with st.expander("Execution Plan (Tasks & Dependencies)", expanded=False):
                    plan_data = []
                    results_dict = result.get("results") or {}
                    for t in plan.tasks:
                        tr = results_dict.get(t.id)
                        status_str = tr.status.value if tr else "pending"
                        plan_data.append({
                            "ID": t.id,
                            "Agent": t.agent,
                            "Description": t.description,
                            "Dependencies": ", ".join(t.depends_on) if t.depends_on else "None",
                            "Status": status_str,
                        })
                    st.dataframe(pd.DataFrame(plan_data), use_container_width=True)

            # Interactive DataFrames
            results_dict = result.get("results") or {}
            tables_found = False
            for tid, tr in results_dict.items():
                if tr and tr.data_ref:
                    df = DataStore.get(tr.data_ref)
                    if df is not None and not df.empty:
                        if not tables_found:
                            st.subheader("Data Tables")
                            tables_found = True
                        with st.expander(f"Data from Task {tid} ({df.shape[0]} rows, {df.shape[1]} columns)"):
                            st.dataframe(df.head(100), use_container_width=True)

            # Charts
            charts_found = False
            for tid, tr in results_dict.items():
                if tr and tr.metrics and "chart_json" in tr.metrics:
                    if not charts_found:
                        st.subheader("Visualizations")
                        charts_found = True
                    chart_json = tr.metrics["chart_json"]
                    fig = pio.from_json(chart_json)
                    st.plotly_chart(fig, use_container_width=True)

            # Warnings
            warnings = result.get("warnings") or []
            if warnings:
                st.subheader("Warnings")
                for w in warnings:
                    st.warning(w)

        except Exception as exc:
            err_msg = str(exc)
            if "429" in err_msg or "rate limit" in err_msg.lower():
                st.error("Rate limit exceeded (HTTP 429): Provider quota was exceeded. Please wait a moment and retry.")
            else:
                st.error(f"Error during query execution: {exc}")
