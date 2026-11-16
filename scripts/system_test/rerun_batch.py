import sys
from scripts.system_test.chat_tests import ChatTester
from scripts.system_test.reporter import Reporter
from scripts.system_test.run import load_existing_results, load_env_json

tester = ChatTester()
reporter = Reporter()
results = load_existing_results()

to_rerun = sys.argv[1:] if len(sys.argv) > 1 else ['A5', 'B2', 'B3', 'B5', 'C5', 'D3']
for t_id in to_rerun:
    fn_name = 'test_' + t_id.lower()
    fn = getattr(tester, fn_name)
    print(f'Rerunning {t_id}...')
    try:
        res = fn()
        results[t_id] = res
        print(f'Result {t_id}: {res["status"]} | Cat: {res.get("category")} | Got: {str(res.get("got"))[:60]}')
    except Exception as e:
        print(f'Error rerunning {t_id}: {e}')

final_list = list(results.values())
reporter.write_results_jsonl(final_list)
env = load_env_json()
reporter.write_report_md(final_list, env)
reporter.write_summary_for_review(final_list, env)
print('Updated results.jsonl and reports.')

