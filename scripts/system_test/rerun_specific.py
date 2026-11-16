"""
Rerun specific chat tests with updated harness fixes.
Usage: python -m scripts.system_test.rerun_specific A1 A2 A3 A4 A5 B5 C2 C3 C4
"""
import sys
from scripts.system_test.chat_tests import ChatTester, QuotaStopException
from scripts.system_test.reporter import Reporter
from scripts.system_test.run import load_existing_results, load_env_json
import time

tester = ChatTester()
reporter = Reporter()
results = load_existing_results()

to_rerun = sys.argv[1:] if len(sys.argv) > 1 else []

for t_id in to_rerun:
    fn_name = 'test_' + t_id.lower()
    fn = getattr(tester, fn_name)
    print(f'Rerunning {t_id}...', flush=True)
    try:
        res = fn()
        results[t_id] = res
        print(f'Result {t_id}: {res["status"]} | Cat: {res.get("category")} | Got: {str(res.get("got"))[:70]}', flush=True)
    except QuotaStopException as qe:
        print(f'[QUOTA] {t_id}: {qe}', flush=True)
        results[t_id] = {"id": t_id, "group": t_id[0], "status": "NOT_RUN_QUOTA", "category": None, "expected": "Quota", "got": str(qe), "latency_ms": 0.0}
    except Exception as e:
        print(f'[ERROR] {t_id}: {e}', flush=True)
        results[t_id] = {"id": t_id, "group": t_id[0], "status": "FAIL", "category": "crash_or_error", "expected": "No exception", "got": str(e), "latency_ms": 0.0}
    time.sleep(1.0)

final_list = list(results.values())
reporter.write_results_jsonl(final_list)
env = load_env_json()
reporter.write_report_md(final_list, env)
reporter.write_summary_for_review(final_list, env)
print(f'\nUpdated {len(final_list)} results.')

