"""
scripts/run_eval.py – Score the 25-question eval set.

Scoring dimensions per question:
  - intent_match    (1/0): agent state intent == eval set intent (substring match)
  - tools_match     (0-1): fraction of expected tools that appeared in planned tasks
  - numbers_grounded (0-1): fraction of key_numbers found in final answer (±2%)
  - unexpected_nums  (int): count of numbers in answer NOT in expected OR tool outputs

Summary table is printed to stdout. Exits with code 1 if average score < 0.7.

Usage:
    python scripts/run_eval.py               # replay mode (requires cache)
    python scripts/run_eval.py --live        # live mode (uses quota)
    python scripts/run_eval.py --questions q01,q05
"""
import argparse
import json
import logging
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_eval")

EVAL_SET = Path(__file__).parent.parent / "solar_agent" / "eval_set.json"
NUMBER_TOL = 0.02  # 2% relative tolerance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_numbers(text: str) -> list[float]:
    """Extract numbers, ignoring years and small integer IDs."""
    pattern = r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?"
    nums = []
    for r in re.findall(pattern, text):
        try:
            val = float(r.replace(",", ""))
            if 2000 <= val <= 2100 and val == int(val):
                continue
            if 0 <= val <= 1000 and val == int(val):
                continue
            nums.append(val)
        except ValueError:
            pass
    return nums


def _is_close(a: float, b: float, tol: float = NUMBER_TOL) -> bool:
    if b == 0:
        return abs(a) < 1e-6
    # also accept percent conversion
    return abs((a - b) / b) <= tol or abs((a - b * 100) / (b * 100)) <= tol


def _score_numbers(expected: list[float], answer_text: str,
                   ground_values: list[float]) -> tuple[float, int]:
    """Return (grounded_fraction, unexpected_count)."""
    answer_nums = _extract_numbers(answer_text)
    all_allowed = ground_values + expected

    grounded = sum(
        1 for n in expected if any(_is_close(n, a) for a in answer_nums)
    )
    grounded_frac = grounded / len(expected) if expected else 1.0

    unexpected = sum(
        1 for n in answer_nums if not any(_is_close(n, a) for a in all_allowed)
    )
    return grounded_frac, unexpected


def _collect_ground_values(results: dict) -> list[float]:
    from solar_agent.graph.data_store import DataStore
    import numpy as np
    nums = []
    for tr in (results or {}).values():
        if tr and tr.metrics:
            for v in tr.metrics.values():
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    pass
        if tr and tr.data_ref:
            df = DataStore.get(tr.data_ref)
            if df is not None and not df.empty:
                for col in df.select_dtypes(include=[np.number]).columns:
                    for val in df[col].dropna():
                        try:
                            nums.append(float(val))
                        except (TypeError, ValueError):
                            pass
    return nums


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use live LLM instead of cache.")
    parser.add_argument("--questions", default=None, help="Comma-separated question IDs.")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from solar_agent.graph.llm_recorder import ReplayLLM, CacheMissError
    from solar_agent.graph.graph import build_graph, initial_state
    from solar_agent.graph.data_store import DataStore

    with open(EVAL_SET) as f:
        eval_set = json.load(f)

    selected_ids = set(args.questions.split(",")) if args.questions else None
    if selected_ids:
        eval_set = [q for q in eval_set if q["id"] in selected_ids]

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")

    if args.live:
        if not api_key:
            print("ERROR: No API key found for live mode.", file=sys.stderr)
            sys.exit(1)
        print("Mode: LIVE (uses quota)")
        graph = build_graph(mode="live")
    else:
        dummy = ReplayLLM(fallback_llm=None, mode="replay")
        print(f"Mode: REPLAY ({dummy.cache_size()} cached entries)")
        graph = build_graph(mode="replay")

    rows = []

    for idx, q in enumerate(eval_set, 1):
        DataStore.clear()
        qid = q["id"]
        expected_intent = q["intent"]
        expected_tools = set(q.get("tools", []))
        expected_nums = q.get("key_numbers", [])

        print(f"\n[{idx}/{len(eval_set)}] {qid}: {q['question'][:80]}")

        try:
            state = graph.invoke(initial_state(q["question"]))
        except CacheMissError as e:
            print(f"  SKIP (cache miss): {e}")
            rows.append({
                "id": qid, "intent": 0, "tools": 0.0, "numbers": 0.0, "unexpected": 0,
                "status": "cache_miss"
            })
            continue
        except Exception as e:
            print(f"  ERROR: {e}")
            rows.append({
                "id": qid, "intent": 0, "tools": 0.0, "numbers": 0.0, "unexpected": 0,
                "status": f"error: {e}"
            })
            continue

        answer = state.get("final_answer") or ""
        results = state.get("results") or {}
        plan = state.get("plan")

        # Intent match: check if expected_intent substring in plan description
        plan_text = (plan.goal if plan else "") + " " + " ".join(
            t.description for t in (plan.tasks if plan else [])
        )
        intent_match = int(expected_intent.lower() in plan_text.lower())

        # Tools match: fraction of expected tools seen in any task's tool field
        used_tools: set[str] = set()
        if plan:
            for t in plan.tasks:
                tool_name = getattr(t, "tool", None) or ""
                if tool_name:
                    used_tools.add(tool_name)
        tools_match = (
            len(expected_tools & used_tools) / len(expected_tools)
            if expected_tools else 1.0
        )

        # Number grounding
        ground_values = _collect_ground_values(results)
        numbers_grounded, unexpected_count = _score_numbers(expected_nums, answer, ground_values)

        row = {
            "id": qid,
            "intent": intent_match,
            "tools": round(tools_match, 2),
            "numbers": round(numbers_grounded, 2),
            "unexpected": unexpected_count,
            "status": "ok"
        }
        rows.append(row)

        print(f"  intent={intent_match} tools={tools_match:.2f} "
              f"numbers={numbers_grounded:.2f} unexpected={unexpected_count}")
        if answer:
            print(f"  answer: {answer[:160].replace(chr(10),' ')}…")

    # Summary table
    print("\n" + "=" * 70)
    print(f"{'ID':<6} {'Intent':>6} {'Tools':>6} {'Numbers':>8} {'Unexpect':>9} {'Status'}")
    print("-" * 70)
    for r in rows:
        print(f"{r['id']:<6} {r['intent']:>6} {r['tools']:>6.2f} {r['numbers']:>8.2f}"
              f" {r['unexpected']:>9}   {r['status']}")

    ok_rows = [r for r in rows if r["status"] == "ok"]
    if ok_rows:
        avg_intent = sum(r["intent"] for r in ok_rows) / len(ok_rows)
        avg_tools = sum(r["tools"] for r in ok_rows) / len(ok_rows)
        avg_numbers = sum(r["numbers"] for r in ok_rows) / len(ok_rows)
        total_unexpected = sum(r["unexpected"] for r in ok_rows)
        print("-" * 70)
        print(f"{'AVG':<6} {avg_intent:>6.2f} {avg_tools:>6.2f} {avg_numbers:>8.2f}"
              f" {total_unexpected:>9}   ({len(ok_rows)} questions scored)")

        overall = (avg_intent + avg_tools + avg_numbers) / 3
        print(f"\nOverall score: {overall:.2%}")
        if overall < 0.70:
            print("FAIL: score below 70% threshold.")
            sys.exit(1)
        else:
            print("PASS")
    else:
        print("\nNo questions could be scored (all cache misses / errors).")
        sys.exit(1)


if __name__ == "__main__":
    main()
