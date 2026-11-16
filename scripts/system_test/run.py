"""
run.py — CLI Test Runner for Solar Plant AI System Test Suite.

Usage:
    python -m scripts.system_test.run [--groups A,B,C] [--resume] [--dashboard-only] [--ui] [--list] [--rerun ID]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from scripts.system_test.chat_tests import ChatTester, QuotaStopException
from scripts.system_test.dashboard_tests import DashboardTester
from scripts.system_test.ground_truth import GroundTruth
from scripts.system_test.reporter import Reporter
from scripts.system_test.ui_tests import UITester


def load_env_json() -> dict[str, Any]:
    env_path = "test_output/env.json"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_existing_results(results_path: str = "test_output/results.jsonl") -> dict[str, dict[str, Any]]:
    existing = {}
    if os.path.exists(results_path):
        with open(results_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        existing[record["id"]] = record
                    except Exception:
                        pass
    return existing


def main():
    parser = argparse.ArgumentParser(description="Solar Plant AI System Test Suite Runner")
    parser.add_argument("--groups", type=str, help="Comma-separated groups to run (e.g. A,B,C,DB,UI)")
    parser.add_argument("--resume", action="store_true", help="Skip tests already present in results.jsonl")
    parser.add_argument("--dashboard-only", action="store_true", help="Run only dashboard API tests (DB1-DB17)")
    parser.add_argument("--ui", action="store_true", help="Run only UI tests (UI1-UI11)")
    parser.add_argument("--list", action="store_true", help="List all available tests")
    parser.add_argument("--rerun", type=str, help="Specific test ID to rerun (e.g. B1)")

    args = parser.parse_args()

    gt = GroundTruth()
    # Step 1: Ground truth self-check
    print("=" * 80)
    print("STEP 1: Checking Ground Truth Self-Check...")
    sc = gt.self_check()
    if not sc["passed"]:
        print("FATAL: Ground truth self-check failed:", sc["errors"])
        sys.exit(1)
    print("Ground truth self-check PASSED successfully.")
    print("=" * 80)

    # Initialize testers
    db_tester = DashboardTester()
    chat_tester = ChatTester()
    ui_tester = UITester()
    reporter = Reporter()

    # Map of all tests
    all_tests: Dict[str, tuple[str, Any]] = {}

    # Register DB tests
    for i in range(1, 18):
        t_id = f"DB{i}"
        method_name = f"test_db{i}"
        if hasattr(db_tester, method_name):
            all_tests[t_id] = ("DB", getattr(db_tester, method_name))

    # Register Chat tests
    chat_map = {
        "A": [f"test_a{i}" for i in range(1, 6)],
        "B": [f"test_b{i}" for i in range(1, 9)],
        "C": [f"test_c{i}" for i in range(1, 6)],
        "D": [f"test_d{i}" for i in range(1, 6)],
        "E": [f"test_e{i}" for i in range(1, 5)],
        "F": [f"test_f{i}" for i in range(1, 14)],
        "G": [f"test_g{i}" for i in range(1, 5)],
        "H": [f"test_h{i}" for i in range(1, 3)],
    }
    for grp, m_list in chat_map.items():
        for m_name in m_list:
            t_id = m_name.replace("test_", "").upper()
            if hasattr(chat_tester, m_name):
                all_tests[t_id] = (grp, getattr(chat_tester, m_name))

    # Register UI tests
    for i in range(1, 12):
        t_id = f"UI{i}"
        all_tests[t_id] = ("UI", None)  # UI tester runs in batch or individually

    if args.list:
        print(f"Total registered tests: {len(all_tests)}")
        for t_id, (grp, _) in sorted(all_tests.items()):
            print(f"  [{grp}] {t_id}")
        return

    # Filter tests to run
    existing_results = load_existing_results()
    selected_tests = []

    if args.rerun:
        if args.rerun.upper() in all_tests:
            selected_tests = [args.rerun.upper()]
        else:
            print(f"Error: test ID '{args.rerun}' not recognized.")
            sys.exit(1)
    elif args.dashboard_only:
        selected_tests = [t for t, (g, _) in all_tests.items() if g == "DB"]
    elif args.ui:
        selected_tests = [t for t, (g, _) in all_tests.items() if g == "UI"]
    elif args.groups:
        target_groups = [g.strip().upper() for g in args.groups.split(",")]
        selected_tests = [t for t, (g, _) in all_tests.items() if g in target_groups]
    else:
        # Full run order: DB, A, B, C, D, E, F, G, H, UI
        order = ["DB", "A", "B", "C", "D", "E", "F", "G", "H", "UI"]
        for g_target in order:
            for t_id, (g, _) in all_tests.items():
                if g == g_target:
                    selected_tests.append(t_id)

    print(f"Execution plan: {len(selected_tests)} tests to evaluate.")

    results_dict = dict(existing_results) if (args.resume or not args.rerun) else {}
    quota_stopped = False

    # Execute tests
    for t_id in selected_tests:
        grp, fn = all_tests[t_id]

        if args.resume and t_id in results_dict and t_id != args.rerun:
            print(f">> Skipping {t_id} (already present in results.jsonl: {results_dict[t_id]['status']})")
            continue

        if quota_stopped and grp not in ("DB", "UI"):
            print(f">> Quota limit reached previously. Marking {t_id} NOT_RUN_QUOTA.")
            results_dict[t_id] = {
                "id": t_id,
                "group": grp,
                "status": "NOT_RUN_QUOTA",
                "category": None,
                "expected": "Not executed due to quota limit",
                "got": "NOT_RUN_QUOTA",
                "latency_ms": 0.0,
            }
            continue

        print(f">> Running [{grp}] {t_id}...")
        try:
            if grp == "UI":
                # Will run UI batch after or single test
                continue
            elif fn:
                res = fn()
                results_dict[t_id] = res
                print(f"   Result: {res['status']} | Expected: {str(res.get('expected'))[:40]} | Got: {str(res.get('got'))[:40]}")
        except QuotaStopException as qe:
            print(f"   [QUOTA LIMIT] {qe}")
            quota_stopped = True
            results_dict[t_id] = {
                "id": t_id,
                "group": grp,
                "status": "NOT_RUN_QUOTA",
                "category": None,
                "expected": "Quota stop triggered",
                "got": str(qe),
                "latency_ms": 0.0,
            }
        except Exception as e:
            print(f"   [ERROR] Exception executing {t_id}: {e}")
            results_dict[t_id] = {
                "id": t_id,
                "group": grp,
                "status": "FAIL",
                "category": "crash_or_error",
                "expected": "Clean test execution",
                "got": f"Exception: {e}",
                "latency_ms": 0.0,
            }

        # Small delay between tests to keep logs clear and respect rate limit
        time.sleep(1.0)

    # If UI tests are included in selected_tests
    ui_ids_to_run = [t for t in selected_tests if all_tests[t][0] == "UI"]
    if ui_ids_to_run:
        print("\n" + "=" * 80)
        print("STEP: Running UI Smoke Tests (Playwright Chromium)...")
        print("=" * 80)
        try:
            ui_results = ui_tester.run_all()
            for r in ui_results:
                results_dict[r["id"]] = r
                print(f">> UI Test {r['id']}: {r['status']} | Got: {r.get('got')}")
        except Exception as e:
            print(f"Error running UI tests: {e}")
            for u_id in ui_ids_to_run:
                results_dict[u_id] = {
                    "id": u_id,
                    "group": "UI",
                    "status": "FAIL",
                    "category": "ui_defect",
                    "expected": "UI test suite completes",
                    "got": f"Exception: {e}",
                    "latency_ms": 0.0,
                }

    # Order results according to all_tests order
    final_results = []
    for t_id in all_tests:
        if t_id in results_dict:
            final_results.append(results_dict[t_id])

    # Generate Reports
    env_info = load_env_json()
    print("\n" + "=" * 80)
    print("STEP: Generating Output Artifacts...")
    print("=" * 80)
    jsonl_path = reporter.write_results_jsonl(final_results)
    report_path = reporter.write_report_md(final_results, env_info)
    summary_path = reporter.write_summary_for_review(final_results, env_info)

    print(f"Results saved to: {jsonl_path}")
    print(f"Report saved to: {report_path}")
    print(f"Summary saved to: {summary_path}")

    # Summary Stats
    total = len(final_results)
    passed = sum(1 for r in final_results if r["status"] == "PASS")
    failed = sum(1 for r in final_results if r["status"] == "FAIL")
    review = sum(1 for r in final_results if r["status"] == "REVIEW")
    not_run = sum(1 for r in final_results if r["status"] == "NOT_RUN_QUOTA")
    print(f"\nCompleted: {total} tests. PASS: {passed}, FAIL: {failed}, REVIEW: {review}, NOT_RUN: {not_run}")


if __name__ == "__main__":
    main()

