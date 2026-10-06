"""
scripts/rerecord.py – Re-populate the LLM cache for eval runs.

Usage:
    python scripts/rerecord.py [--questions q01,q05]  # subset (default: all)
    python scripts/rerecord.py --dry-run              # show what would be recorded

This script runs a subset (or all) eval questions through the LIVE Gemini API and
saves every LLM call to solar_agent/tests/llm_cache/llm_cache.json.
Subsequent eval runs can use mode='replay' with no API quota consumed.

WARNING: Each question may use several LLM calls (orchestrator + agents + synthesizer).
The free Gemini tier allows ~20 calls/day. Run only what you need.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("rerecord")

from dotenv import load_dotenv
load_dotenv()

from langchain_google_genai import ChatGoogleGenerativeAI
from solar_agent.graph.llm_recorder import ReplayLLM
from solar_agent.graph.graph import build_graph, initial_state
from solar_agent.graph.data_store import DataStore

EVAL_SET = Path(__file__).parent.parent / "solar_agent" / "eval_set.json"
CACHE_FILE = Path(__file__).parent.parent / "solar_agent" / "tests" / "llm_cache" / "llm_cache.json"


def main():
    parser = argparse.ArgumentParser(description="Re-record LLM responses for eval questions.")
    parser.add_argument(
        "--questions", default=None,
        help="Comma-separated question IDs to record, e.g. q01,q05. Default: all."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print questions without running.")
    args = parser.parse_args()

    with open(EVAL_SET) as f:
        eval_set = json.load(f)

    selected_ids = set(args.questions.split(",")) if args.questions else None
    if selected_ids:
        eval_set = [q for q in eval_set if q["id"] in selected_ids]
        logger.info("Filtered to %d questions: %s", len(eval_set), selected_ids)

    if args.dry_run:
        for q in eval_set:
            print(f"  [{q['id']}] {q['question']}")
        return

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("Set GOOGLE_API_KEY or GEMINI_API_KEY in your .env file.")
        sys.exit(1)

    graph = build_graph(mode="record")

    logger.info("Starting re-record run: %d questions", len(eval_set))

    for idx, q in enumerate(eval_set, 1):
        DataStore.clear()
        logger.info("[%d/%d] %s: %s", idx, len(eval_set), q["id"], q["question"])
        try:
            result = graph.invoke(initial_state(q["question"]))
            logger.info("  → %s", (result.get("final_answer") or "")[:120].replace("\n", " "))
        except Exception as exc:
            logger.error("  FAILED: %s", exc)

    dummy_recorder = ReplayLLM(fallback_llm=None, mode="replay")
    logger.info(
        "Done. Cache now has %d entries → %s", dummy_recorder.cache_size(), CACHE_FILE
    )


if __name__ == "__main__":
    main()
