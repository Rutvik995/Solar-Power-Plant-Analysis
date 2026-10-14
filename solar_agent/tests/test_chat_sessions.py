"""
solar_agent/tests/test_chat_sessions.py — Tests for Chat Storage, Session Endpoints & Cancellation.

Requirements:
  1. Persistence across a new app instance
  2. Delete cascade
  3. Edit removes later messages
  4. Stop mid-run saves a stopped payload and makes no further LLM calls
  5. Stale running rows are cleaned at startup
  6. Delete message pair (question + answer)
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.main import app
from solar_agent.config.settings import settings
import solar_agent.db.chat_storage as cs
from solar_agent.graph.budgeted_llm import BudgetedLLM
from solar_agent.state import (
    Cancelled,
    IntentType,
    Plan,
    Scope,
    Task,
    TaskResult,
    TaskStatus,
    get_cancel_token,
    register_cancel_token,
    unregister_cancel_token,
)


# ---------------------------------------------------------------------------
# Test 1: Persistence across a new app instance
# ---------------------------------------------------------------------------
def test_persistence_across_new_app_instance(tmp_path, monkeypatch):
    db_file = tmp_path / "persistence.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "chat_database_url", db_url)

    cs._CHAT_ENGINE = None
    cs.init_chat_db()

    # App instance 1
    client1 = TestClient(app)
    res_create = client1.post("/api/sessions", json={"title": "Persistent Session"})
    assert res_create.status_code == 200
    session_id = res_create.json()["id"]

    # Add user message and assistant message
    cs.create_message(session_id=session_id, role="user", content="Question 1")
    cs.create_message(
        session_id=session_id,
        role="assistant",
        content="Answer 1",
        payload={"short_answer": "Answer 1", "tables": []},
        status="complete",
    )

    # Dispose engine to ensure all DB locks and caches are cleared
    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None

    # App instance 2: new client with fresh engine connection
    client2 = TestClient(app)
    res_sessions = client2.get("/api/sessions")
    assert res_sessions.status_code == 200
    session_titles = [s["title"] for s in res_sessions.json()]
    assert "Persistent Session" in session_titles

    res_msgs = client2.get(f"/api/sessions/{session_id}/messages")
    assert res_msgs.status_code == 200
    msgs = res_msgs.json()
    assert len(msgs) == 2
    assert msgs[0]["content"] == "Question 1"
    assert msgs[1]["content"] == "Answer 1"
    assert msgs[1]["status"] == "complete"
    assert msgs[1]["payload"]["short_answer"] == "Answer 1"

    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None


# ---------------------------------------------------------------------------
# Test 2: Delete cascade
# ---------------------------------------------------------------------------
def test_delete_cascade(tmp_path, monkeypatch):
    db_file = tmp_path / "cascade.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "chat_database_url", db_url)

    cs._CHAT_ENGINE = None
    cs.init_chat_db()
    client = TestClient(app)

    res_sess = client.post("/api/sessions", json={"title": "Session to Cascade Delete"})
    assert res_sess.status_code == 200
    session_id = res_sess.json()["id"]

    # Insert messages into this session
    cs.create_message(session_id=session_id, role="user", content="Will be deleted 1")
    cs.create_message(session_id=session_id, role="assistant", content="Will be deleted 2")

    res_before = client.get(f"/api/sessions/{session_id}/messages")
    assert len(res_before.json()) == 2

    # Delete the session
    res_del = client.delete(f"/api/sessions/{session_id}")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] is True

    # Verify session messages return 404
    res_after = client.get(f"/api/sessions/{session_id}/messages")
    assert res_after.status_code == 404

    # Direct DB check: chat_messages table has 0 rows for this session_id
    with cs.get_chat_engine().connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM chat_messages WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar()
        assert count == 0

    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None


# ---------------------------------------------------------------------------
# Test 3: Edit removes later messages
# ---------------------------------------------------------------------------
def test_edit_removes_later_messages(tmp_path, monkeypatch):
    db_file = tmp_path / "edit.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "chat_database_url", db_url)

    cs._CHAT_ENGINE = None
    cs.init_chat_db()
    client = TestClient(app)

    res_sess = client.post("/api/sessions", json={"title": "Session with edits"})
    session_id = res_sess.json()["id"]

    # Message 1: user "Original Q1"
    m1 = cs.create_message(session_id=session_id, role="user", content="Original Q1")
    # Message 2: assistant "Answer 1"
    m2 = cs.create_message(
        session_id=session_id,
        role="assistant",
        content="Answer 1",
        status="complete",
        payload={"short_answer": "Answer 1"},
    )
    # Message 3: user "Q2"
    m3 = cs.create_message(session_id=session_id, role="user", content="Q2")
    # Message 4: assistant "Answer 2"
    m4 = cs.create_message(
        session_id=session_id,
        role="assistant",
        content="Answer 2",
        status="complete",
        payload={"short_answer": "Answer 2"},
    )

    all_before = client.get(f"/api/sessions/{session_id}/messages").json()
    assert len(all_before) == 4

    # Mock graph for the edited run
    mock_graph = MagicMock()
    mock_plan = Plan(
        intent=IntentType.SUMMARY,
        scope=Scope(start_date=date(2026, 9, 1), end_date=date(2026, 9, 5), plant_ids=[1]),
        tasks=[Task(id="t1", agent="data", description="Fetch data")],
        success_criteria="Criteria satisfied",
    )
    mock_graph.stream.return_value = [
        {"orchestrator": {"plan": mock_plan, "llm_calls": {"total": 1}}},
        {"executor": {"results": {"t1": TaskResult(task_id="t1", status=TaskStatus.DONE, summary="Data fetched")}, "llm_calls": {"total": 2}}},
        {"synthesizer": {"final_answer": "Updated Answer 1", "short_answer": "Updated Answer 1", "details_markdown": "Details", "llm_calls": {"total": 3}}},
    ]

    with patch("api.main.build_graph", return_value=mock_graph):
        res_edit = client.post(f"/api/messages/{m1['id']}/edit", json={"query": "Edited Q1"})
        assert res_edit.status_code == 200
        run_data = res_edit.json()
        run_id = run_data["run_id"]

        from api.main import _RUN_THREADS
        if run_id in _RUN_THREADS:
            _RUN_THREADS[run_id].join(timeout=3)

    all_after = client.get(f"/api/sessions/{session_id}/messages").json()
    msg_ids_after = [m["id"] for m in all_after]

    # Later messages (m2, m3, m4) are removed
    assert m2["id"] not in msg_ids_after
    assert m3["id"] not in msg_ids_after
    assert m4["id"] not in msg_ids_after

    # m1 content is updated
    assert all_after[0]["id"] == m1["id"]
    assert all_after[0]["content"] == "Edited Q1"

    # New assistant message exists
    assert len(all_after) == 2
    assert all_after[1]["role"] == "assistant"
    assert all_after[1]["content"] == "Updated Answer 1"

    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None


# ---------------------------------------------------------------------------
# Test 4: Stop mid-run saves a stopped payload and makes no further LLM calls
# ---------------------------------------------------------------------------
def test_stop_mid_run_saves_stopped_payload_and_no_further_calls(tmp_path, monkeypatch):
    db_file = tmp_path / "stop.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "chat_database_url", db_url)

    cs._CHAT_ENGINE = None
    cs.init_chat_db()
    client = TestClient(app)

    res_sess = client.post("/api/sessions", json={"title": "Stop Run Test"})
    session_id = res_sess.json()["id"]

    mock_plan = Plan(
        intent=IntentType.SUMMARY,
        scope=Scope(start_date=date(2026, 9, 1), end_date=date(2026, 9, 5), plant_ids=[1]),
        tasks=[
            Task(id="t1", agent="data", description="Task 1 - fetch inverter data"),
            Task(id="t2", agent="performance", description="Task 2 - compute performance ratio", depends_on=["t1"]),
        ],
        success_criteria="Criteria satisfied",
    )

    llm_calls_made = 0
    subsequent_call_attempted = False

    def mock_stream(state_input, config=None, stream_mode="updates"):
        nonlocal llm_calls_made, subsequent_call_attempted
        llm_calls_made += 1
        # Step 1: Yield orchestrator plan and partial result
        yield {
            "orchestrator": {
                "plan": mock_plan,
                "results": {"t1": TaskResult(task_id="t1", status=TaskStatus.DONE, summary="T1 completed")},
                "llm_calls": {"total": 1},
            }
        }

        # Wait briefly for client to send stop request
        run_id = state_input.get("run_id")
        token = get_cancel_token(run_id)
        import time
        for _ in range(100):
            if token and token.is_cancelled():
                break
            time.sleep(0.01)

        if token:
            token.check(partial_results={"t1": TaskResult(task_id="t1", status=TaskStatus.DONE, summary="T1 completed")})

        subsequent_call_attempted = True
        llm_calls_made += 1
        yield {"synthesizer": {"final_answer": "Should never reach here"}}

    mock_graph = MagicMock()
    mock_graph.stream.side_effect = mock_stream

    with patch("api.main.build_graph", return_value=mock_graph):
        res_chat = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"query": "Run that will be stopped"},
        )
        assert res_chat.status_code == 200
        run_data = res_chat.json()
        run_id = run_data["run_id"]
        asst_msg_id = run_data["assistant_message_id"]

        # Call stop immediately
        res_stop = client.post(f"/api/runs/{run_id}/stop")
        assert res_stop.status_code == 200

        from api.main import _RUN_THREADS
        if run_id in _RUN_THREADS:
            _RUN_THREADS[run_id].join(timeout=3)

    assert subsequent_call_attempted is False
    assert llm_calls_made == 1

    # Check run status via endpoint
    res_run = client.get(f"/api/runs/{run_id}")
    assert res_run.status_code == 200
    run_info = res_run.json()
    assert run_info["status"] == "stopped"
    assert run_info["payload"] is not None

    # Check assistant message in DB
    asst_msg = cs.get_message(asst_msg_id)
    assert asst_msg is not None
    assert asst_msg["status"] == "stopped"
    assert asst_msg["payload"] is not None
    assert "t1" in str(asst_msg["payload"])

    # Unit check: BudgetedLLM checks CancelToken before making call
    inner_llm = MagicMock()
    inner_llm.invoke.return_value = "Normal response"
    b_llm = BudgetedLLM(inner_llm=inner_llm, budget=5)
    test_token = register_cancel_token("unit_cancel_test")
    test_token.cancel()

    with pytest.raises(Cancelled):
        b_llm.invoke("Hello", run_id="unit_cancel_test")
    assert inner_llm.invoke.call_count == 0

    unregister_cancel_token("unit_cancel_test")
    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None


# ---------------------------------------------------------------------------
# Test 5: Stale running rows are cleaned at startup
# ---------------------------------------------------------------------------
def test_stale_running_rows_cleaned_at_startup(tmp_path, monkeypatch):
    db_file = tmp_path / "stale.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "chat_database_url", db_url)

    cs._CHAT_ENGINE = None
    cs.init_chat_db()

    sess = cs.create_session(title="Stale Session")
    m_running = cs.create_message(session_id=sess["id"], role="assistant", content="In flight...", status="running")
    m_complete = cs.create_message(session_id=sess["id"], role="assistant", content="Done", status="complete")

    assert cs.get_message(m_running["id"])["status"] == "running"
    assert cs.get_message(m_complete["id"])["status"] == "complete"

    # Simulate restart by calling init_chat_db()
    cs.init_chat_db()

    after_running = cs.get_message(m_running["id"])
    after_complete = cs.get_message(m_complete["id"])

    assert after_running["status"] == "stopped"
    assert after_complete["status"] == "complete"

    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None


# ---------------------------------------------------------------------------
# Test 6: Delete message pair (question + answer)
# ---------------------------------------------------------------------------
def test_delete_message_pair(tmp_path, monkeypatch):
    db_file = tmp_path / "pair.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "chat_database_url", db_url)

    cs._CHAT_ENGINE = None
    cs.init_chat_db()
    client = TestClient(app)

    sess = client.post("/api/sessions", json={"title": "Pair Test"}).json()
    session_id = sess["id"]

    m_user = cs.create_message(session_id=session_id, role="user", content="Question to delete")
    m_asst = cs.create_message(session_id=session_id, role="assistant", content="Answer to delete")

    # Deleting user message deletes the pair
    res_del = client.delete(f"/api/messages/{m_user['id']}")
    assert res_del.status_code == 200

    remaining = client.get(f"/api/sessions/{session_id}/messages").json()
    assert len(remaining) == 0

    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None


# ---------------------------------------------------------------------------
# Test 7: Follow-up context is built from DB assistant payload
# ---------------------------------------------------------------------------
def test_followup_context_from_db_payload(tmp_path, monkeypatch):
    db_file = tmp_path / "followup.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "chat_database_url", db_url)

    cs._CHAT_ENGINE = None
    cs.init_chat_db()
    client = TestClient(app)

    sess = client.post("/api/sessions", json={"title": "Follow-up Test"}).json()
    session_id = sess["id"]

    # Preceding user question and completed assistant payload
    cs.create_message(
        session_id=session_id,
        role="user",
        content="What was the PR of Plant 1 in September 2026?",
    )
    cs.create_message(
        session_id=session_id,
        role="assistant",
        content="The PR was 0.85.",
        status="complete",
        payload={
            "short_answer": "The PR was 0.85.",
            "period": {"start": "2026-09-01", "end": "2026-09-30"},
            "plan": [{"id": "t1", "agent": "performance", "tool": "compute_kpis_tool"}],
        },
    )

    prev_query, prev_tasks, prev_scope = cs.get_last_completed_assistant_context(session_id)
    assert prev_query == "What was the PR of Plant 1 in September 2026?"
    assert prev_tasks == [{"agent": "performance", "tool": "compute_kpis_tool"}]
    assert prev_scope == {"start": "2026-09-01", "end": "2026-09-30"}

    # Mock graph to verify state receives previous context
    passed_state = {}

    def mock_stream(state_input, config=None, stream_mode="updates"):
        nonlocal passed_state
        passed_state = dict(state_input)
        yield {
            "synthesizer": {
                "final_answer": "Plant 2 PR was 0.87.",
                "short_answer": "Plant 2 PR was 0.87.",
                "details_markdown": "",
                "llm_calls": {"total": 1},
            }
        }

    mock_graph = MagicMock()
    mock_graph.stream.side_effect = mock_stream

    with patch("api.main.build_graph", return_value=mock_graph):
        res = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"query": "The same for Plant 2"},
        )
        assert res.status_code == 200
        run_id = res.json()["run_id"]

        from api.main import _RUN_THREADS
        if run_id in _RUN_THREADS:
            _RUN_THREADS[run_id].join(timeout=3)

    assert passed_state.get("previous_query") == "What was the PR of Plant 1 in September 2026?"
    assert passed_state.get("previous_tasks") == [{"agent": "performance", "tool": "compute_kpis_tool"}]
    assert passed_state.get("previous_scope") == {"start": "2026-09-01", "end": "2026-09-30"}

    cs.get_chat_engine().dispose()
    cs._CHAT_ENGINE = None

