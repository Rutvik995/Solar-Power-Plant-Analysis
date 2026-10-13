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
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_eval")

EVAL_SET = Path(__file__).parent.parent / "solar_agent" / "eval_set.json"
NUMBER_TOL = 0.02  # 2% relative tolerance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_answer_text(text: str) -> str:
    """Remove footer lines, assumptions, warnings, dates, and entity IDs."""
    lines = []
    in_footer = False
    for line in text.splitlines():
        l_strip = line.strip()
        if (
            l_strip.startswith("Sources:")
            or l_strip.startswith("Period analysed:")
            or l_strip.startswith("## Assumptions")
            or l_strip.startswith("## Warnings")
            or l_strip.startswith("- Data available")
            or l_strip.startswith("- Undated query")
        ):
            in_footer = True
            continue
        if in_footer and l_strip.startswith("## ") and not ("Assumptions" in l_strip or "Warnings" in l_strip):
            in_footer = False
        if not in_footer:
            lines.append(line)
    cleaned = "\n".join(lines)
    # Strip ISO dates (e.g. 2026-09-01, 2026-09-26, 2026-08-28)
    cleaned = re.sub(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b", " ", cleaned)
    # Strip standalone years
    cleaned = re.sub(r"\b20[2-3]\d\b", " ", cleaned)
    # Strip entity IDs (Plant 1, Block A, INV_01, Inverter 2, etc.)
    cleaned = re.sub(r"\b(?:Plant|Block|INV|Inverter)[\s_]*[A-Za-z0-9]+\b", " ", cleaned, flags=re.IGNORECASE)
    # Strip status codes (status_code 2, code 2)
    cleaned = re.sub(r"\b(?:status[_\s]*code|code)\s*[:=]?\s*\d+\b", " ", cleaned, flags=re.IGNORECASE)
    # Strip duration strings (e.g. 30 days, 26 days)
    cleaned = re.sub(r"\b\d+\s*days?\b", " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def normalize_number_text(text: str) -> str:
    """
    Normalize number text before extraction:
    - Strip commas inside numbers (e.g. '250,610' -> '250610')
    - Strip thin spaces / non-breaking spaces between digit groupings (e.g. '250 610', '250\u202f610' -> '250610')
    - Strip remaining U+202F, U+00A0, U+2009 characters
    """
    if not text:
        return ""
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"(?<=\d)[\u202f\u00a0\u2009\s]+(?=\d{3}(?:\D|$))", "", text)
    text = re.sub(r"[\u202f\u00a0\u2009]", " ", text)
    return text


def _extract_numbers(text: str, ignore_small_ints: bool = False) -> list[float]:
    """Extract numbers, ignoring years and optionally small integer IDs."""
    text = normalize_number_text(text)
    # Strip dates so hyphens are not treated as minus signs
    text = re.sub(r"\b\d{4}[\-‑/]\d{2}[\-‑/]\d{2}\b", " ", text)
    pattern = r"-?\d+(?:\.\d+)?"
    nums = []
    for r in re.findall(pattern, text):
        try:
            val = float(r)
            if 2000 <= val <= 2100 and val == int(val):
                continue
            if ignore_small_ints and 0 <= val <= 1000 and val == int(val):
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
    full_nums = _extract_numbers(answer_text, ignore_small_ints=False)

    grounded = 0
    for n in expected:
        if any(_is_close(n, a) for a in full_nums):
            grounded += 1
        elif n == 0 and any(w in answer_text.lower() for w in ["no fault", "none", "zero", "0 "]):
            grounded += 1
        elif n == 1 and any(w in answer_text.lower() for w in [" 1 ", "one ", "1 instance", "1 fault", "1 inverter"]):
            grounded += 1
        elif n == 2 and any(w in answer_text.lower() for w in [" 2 ", "two ", "2 datapoints", "2 anomalies"]):
            grounded += 1

    grounded_frac = grounded / len(expected) if expected else 1.0

    # For unexpected numbers, clean text of dates, IDs, footers, and ignore small integer IDs
    cleaned_text = _clean_answer_text(answer_text)
    answer_nums = _extract_numbers(cleaned_text, ignore_small_ints=True)
    all_allowed = ground_values + expected

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

NON_ANALYSIS_TOOLS = {
    "fetch_inverter_daily_tool",
    "fetch_weather_daily_tool",
    "resolve_date_range",
    "resolve_date_range_tool",
    "data_fetch",
    "resolve_date",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use live LLM instead of cache.")
    parser.add_argument("--record", action="store_true", help="Run live LLM and record to cache.")
    parser.add_argument("--replay", action="store_true", help="Strict replay mode only.")
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

    api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")

    if args.replay:
        dummy = ReplayLLM(fallback_llm=None, mode="replay")
        print(f"Mode: REPLAY ({dummy.cache_size()} cached entries)")
        graph = build_graph(mode="replay")
    elif args.live:
        if not api_key:
            print("ERROR: No API key found for live mode.", file=sys.stderr)
            sys.exit(1)
        print("Mode: LIVE (uses quota)")
        graph = build_graph(mode="live")
    else:
        # Default: record mode (replays where cached, records live otherwise)
        if not api_key:
            print("ERROR: No API key found for record mode.", file=sys.stderr)
            sys.exit(1)
        dummy = ReplayLLM(fallback_llm=None, mode="record")
        print(f"Mode: RECORD/HYBRID ({dummy.cache_size()} cached entries, live fallback)")
        graph = build_graph(mode="record")

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
            calls = state.get("llm_call_count", 0)
        except CacheMissError as e:
            print(f"  SKIP (cache miss): {e}")
            rows.append({
                "id": qid, "intent": 0, "tools": 0.0, "numbers": 0.0, "unexpected": 0,
                "unexpect_score": 0.0,
                "calls": 0,
                "status": "cache_miss"
            })
            continue
        except Exception as e:
            print(f"  ERROR: {e}")
            rows.append({
                "id": qid, "intent": 0, "tools": 0.0, "numbers": 0.0, "unexpected": 0,
                "unexpect_score": 0.0,
                "calls": 0,
                "status": f"error: {e}"
            })
            continue

        answer = state.get("final_answer") or ""
        results = state.get("results") or {}
        plan = state.get("plan")

        # Intent match: check if expected_intent matches plan intent or description
        plan_intent_str = str(plan.intent.value if hasattr(plan.intent, "value") else plan.intent) if (plan and plan.intent) else ""
        plan_text = (plan_intent_str + " " + (getattr(plan, "success_criteria", "") or "") + " " + " ".join(
            t.description for t in (plan.tasks if plan else [])
        )).lower()
        intent_match = int(
            expected_intent.lower() in plan_text
            or any(k in plan_text for k in expected_intent.lower().split() if len(k) > 3)
        )

        # Tools match: compare expected tools against analysis tools only (excluding data/resolve tools)
        used_tools: set[str] = set()
        for tr in results.values():
            if tr and hasattr(tr, "tool_calls_made") and tr.tool_calls_made:
                for tc in tr.tool_calls_made:
                    used_tools.add(tc)
        if plan:
            for t in plan.tasks:
                tool_name = getattr(t, "tool", None) or getattr(t, "tool_hint", None) or ""
                if tool_name:
                    used_tools.add(tool_name)

        analysis_expected = {t for t in expected_tools if t not in NON_ANALYSIS_TOOLS and not t.startswith("fetch_")}
        analysis_used = {t for t in used_tools if t not in NON_ANALYSIS_TOOLS and not t.startswith("fetch_")}
        tools_match = (
            len(analysis_expected & analysis_used) / len(analysis_expected)
            if analysis_expected else 1.0
        )

        # Number grounding & unexpected numbers
        ground_values = _collect_ground_values(results)
        numbers_grounded, unexpected_count = _score_numbers(expected_nums, answer, ground_values)
        unexpect_score = max(0.0, 1.0 - unexpected_count * 0.1)

        row = {
            "id": qid,
            "intent": intent_match,
            "tools": round(tools_match, 2),
            "numbers": round(numbers_grounded, 2),
            "unexpected": unexpected_count,
            "unexpect_score": round(unexpect_score, 2),
            "calls": calls,
            "status": "ok"
        }
        rows.append(row)

        print(f"  intent={intent_match} tools={tools_match:.2f} "
              f"numbers={numbers_grounded:.2f} unexpected={unexpected_count} calls={calls}")
        if answer:
            print(f"  answer: {answer[:160].replace(chr(10),' ')}...")

        if idx < len(eval_set):
            time.sleep(float(os.environ.get("EVAL_SLEEP", "1.5")))

    # Summary table
    print("\n" + "=" * 76)
    print(f"{'ID':<6} {'Intent':>6} {'Tools':>6} {'Numbers':>8} {'Unexpect':>9} {'Calls':>6} {'Status'}")
    print("-" * 76)
    for r in rows:
        print(f"{r['id']:<6} {r['intent']:>6} {r['tools']:>6.2f} {r['numbers']:>8.2f}"
              f" {r['unexpected']:>9} {r.get('calls', 0):>6}   {r['status']}")

    ok_rows = [r for r in rows if r["status"] == "ok"]
    if ok_rows:
        avg_intent = sum(r["intent"] for r in ok_rows) / len(ok_rows)
        avg_tools = sum(r["tools"] for r in ok_rows) / len(ok_rows)
        avg_numbers = sum(r["numbers"] for r in ok_rows) / len(ok_rows)
        total_unexpected = sum(r["unexpected"] for r in ok_rows)
        avg_unexpect_score = sum(r["unexpect_score"] for r in ok_rows) / len(ok_rows)
        avg_calls = sum(r.get("calls", 0) for r in ok_rows) / len(ok_rows)
        print("-" * 76)
        print(f"{'AVG':<6} {avg_intent:>6.2f} {avg_tools:>6.2f} {avg_numbers:>8.2f}"
              f" {total_unexpected:>9} {avg_calls:>6.1f}   ({len(ok_rows)} questions scored)")

        overall = (avg_intent + avg_tools + avg_numbers + avg_unexpect_score) / 4
        print(f"\nOverall score: {overall:.2%} (Intent: {avg_intent:.2f}, Tools: {avg_tools:.2f}, Numbers: {avg_numbers:.2f}, Unexpected: {avg_unexpect_score:.2f})")
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
