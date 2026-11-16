"""
reporter.py — Aggregates test results, outputs results.jsonl, raw payloads,
report.md, and summary_for_review.txt (<= 200 lines).
"""

from __future__ import annotations

import json
import os
from typing import Any, List


class Reporter:
    def __init__(self, output_dir: str = "test_output"):
        self.output_dir = output_dir
        self.raw_dir = os.path.join(output_dir, "raw")
        os.makedirs(self.raw_dir, exist_ok=True)

    def save_raw(self, test_id: str, payload: Any):
        raw_path = os.path.join(self.raw_dir, f"{test_id}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        return raw_path

    def write_results_jsonl(self, results: List[dict[str, Any]]) -> str:
        out_path = os.path.join(self.output_dir, "results.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for r in results:
                # Save raw payload to raw/<id>.json if present
                raw_path = None
                if "raw_payload" in r:
                    raw_path = self.save_raw(r["id"], r.pop("raw_payload"))
                elif "details" in r:
                    raw_path = self.save_raw(r["id"], {"details": r.get("details"), "got": r.get("got")})
                r_record = dict(r)
                r_record["raw_path"] = raw_path
                f.write(json.dumps(r_record, default=str) + "\n")
        return out_path

    def write_report_md(self, results: List[dict[str, Any]], env_info: dict[str, Any]) -> str:
        out_path = os.path.join(self.output_dir, "report.md")

        # Group counts
        groups = {}
        for r in results:
            grp = r.get("group", "OTHER")
            if grp not in groups:
                groups[grp] = {"PASS": 0, "FAIL": 0, "REVIEW": 0, "NOT_RUN_QUOTA": 0, "total": 0, "latencies": [], "calls": []}
            st = r.get("status", "FAIL")
            groups[grp][st] = groups[grp].get(st, 0) + 1
            groups[grp]["total"] += 1
            if r.get("latency_ms"):
                groups[grp]["latencies"].append(r["latency_ms"])
            calls_val = r.get("llm_calls")
            if isinstance(calls_val, dict):
                c = calls_val.get("total", 0)
                groups[grp]["calls"].append(c)
            elif isinstance(calls_val, (int, float)):
                groups[grp]["calls"].append(calls_val)

        all_lats = [r["latency_ms"] for r in results if r.get("latency_ms")]
        all_calls = []
        for r in results:
            c = r.get("llm_calls")
            if isinstance(c, dict):
                all_calls.append(c.get("total", 0))
            elif isinstance(c, (int, float)):
                all_calls.append(c)

        total_pass = sum(1 for r in results if r["status"] == "PASS")
        total_fail = sum(1 for r in results if r["status"] == "FAIL")
        total_review = sum(1 for r in results if r["status"] == "REVIEW")
        total_quota = sum(1 for r in results if r["status"] == "NOT_RUN_QUOTA")

        avg_lat = sum(all_lats) / len(all_lats) if all_lats else 0
        avg_calls = sum(all_calls) / len(all_calls) if all_calls else 0

        lines = [
            "# System Test Execution Report",
            "",
            "## Environment Summary",
            f"- **Git Commit:** `{env_info.get('git_commit', 'unknown')}`",
            f"- **Provider:** `{env_info.get('llm_provider', 'groq')}`",
            f"- **Models:** Orchestrator: `{env_info.get('models', {}).get('orchestrator')}`, Worker: `{env_info.get('models', {}).get('worker')}`",
            f"- **Data Range:** `{env_info.get('data_min_date')}` to `{env_info.get('data_max_date')}`",
            f"- **Table Counts:** Plants: {env_info.get('table_counts', {}).get('plants')}, Blocks: {env_info.get('table_counts', {}).get('blocks')}, Inverters: {env_info.get('table_counts', {}).get('inverters')}, Telemetry: {env_info.get('table_counts', {}).get('telemetry')}",
            "",
            "## Overall Results",
            f"- **Total Tests:** {len(results)}",
            f"- **PASS:** {total_pass} | **FAIL:** {total_fail} | **REVIEW:** {total_review} | **NOT_RUN_QUOTA:** {total_quota}",
            f"- **Average Latency:** {avg_lat/1000.0:.2f} s",
            f"- **Average LLM Calls:** {avg_calls:.1f}",
            "",
            "## Summary by Group",
            "| Group | Total | PASS | FAIL | REVIEW | NOT_RUN | Avg Latency (s) | Avg Calls |",
            "|---|---|---|---|---|---|---|---|",
        ]

        for grp, st in sorted(groups.items()):
            g_lat = (sum(st["latencies"]) / len(st["latencies"]) / 1000.0) if st["latencies"] else 0.0
            g_calls = (sum(st["calls"]) / len(st["calls"])) if st["calls"] else 0.0
            lines.append(
                f"| {grp} | {st['total']} | {st.get('PASS', 0)} | {st.get('FAIL', 0)} | {st.get('REVIEW', 0)} | {st.get('NOT_RUN_QUOTA', 0)} | {g_lat:.2f}s | {g_calls:.1f} |"
            )

        lines.extend([
            "",
            "## Detailed Failures & Reviews",
            "",
        ])

        fails_and_reviews = [r for r in results if r["status"] in ("FAIL", "REVIEW")]
        if not fails_and_reviews:
            lines.append("No failures or reviews recorded.")
        else:
            for r in fails_and_reviews:
                lines.append(f"### [{r['status']}] {r['id']} (Group {r.get('group')})")
                if "question" in r:
                    lines.append(f"- **Question:** {r['question']}")
                lines.append(f"- **Category:** `{r.get('category')}`")
                lines.append(f"- **Expected:** {r.get('expected')}")
                lines.append(f"- **Got:** {r.get('got')}")
                if r.get("details"):
                    lines.append(f"- **Details:** {r.get('details')}")
                lines.append("")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return out_path

    def write_summary_for_review(self, results: List[dict[str, Any]], env_info: dict[str, Any]) -> str:
        """
        Creates test_output/summary_for_review.txt strictly <= 200 lines.
        """
        out_path = os.path.join(self.output_dir, "summary_for_review.txt")

        total_pass = sum(1 for r in results if r["status"] == "PASS")
        total_fail = sum(1 for r in results if r["status"] == "FAIL")
        total_review = sum(1 for r in results if r["status"] == "REVIEW")
        total_quota = sum(1 for r in results if r["status"] == "NOT_RUN_QUOTA")

        lines = [
            f"SYSTEM TEST SUMMARY — Commit: {env_info.get('git_commit', 'unknown')} | Provider: {env_info.get('llm_provider', 'groq')}",
            f"Range: {env_info.get('data_min_date')} to {env_info.get('data_max_date')} | Total: {len(results)} | PASS: {total_pass} | FAIL: {total_fail} | REVIEW: {total_review} | NOT_RUN: {total_quota}",
            "=" * 90,
            "ID   | STATUS | EXPECTED vs GOT (summary)                                                | SEC  | CALLS",
            "-" * 90,
        ]

        for r in results:
            t_id = r["id"].ljust(4)
            st = r["status"].ljust(6)
            exp_got = f"exp: {str(r.get('expected'))[:40]} | got: {str(r.get('got'))[:40]}"
            exp_got = exp_got[:70].ljust(72)
            sec = f"{((r.get('latency_ms') or 0)/1000.0):.1f}s".rjust(5)
            calls_val = r.get("llm_calls")
            if isinstance(calls_val, dict):
                c_str = str(calls_val.get("total", 0)).rjust(5)
            else:
                c_str = str(calls_val or 0).rjust(5)
            lines.append(f"{t_id} | {st} | {exp_got} | {sec} | {c_str}")

        lines.extend([
            "-" * 90,
            "FAILURES & REVIEWS DETAILS:",
            "-" * 90,
        ])

        fails = [r for r in results if r["status"] in ("FAIL", "REVIEW")]
        for r in fails:
            block = [
                f">> [{r['status']}] {r['id']} (Cat: {r.get('category')}): {r.get('question', r.get('expected'))}",
                f"   Got: {str(r.get('got'))[:120]}",
                f"   Details: {str(r.get('details'))[:150]}",
            ]
            # Keep under 200 lines total
            if len(lines) + len(block) >= 195:
                lines.append(f"... and {len(fails) - fails.index(r)} more (see report.md)")
                break
            lines.extend(block)

        # Truncate strictly to <= 200 lines if exceeded
        final_lines = lines[:200]

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(final_lines) + "\n")
        return out_path

