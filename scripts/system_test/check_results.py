import json

results = []
with open('test_output/results.jsonl') as f:
    for line in f:
        results.append(json.loads(line))

groups = {}
for r in results:
    g = r.get('group', '?')
    if g not in groups:
        groups[g] = {'PASS': 0, 'FAIL': 0, 'REVIEW': 0, 'NOT_RUN_QUOTA': 0}
    groups[g][r['status']] = groups[g].get(r['status'], 0) + 1

total_pass = sum(1 for r in results if r['status'] == 'PASS')
total_fail = sum(1 for r in results if r['status'] == 'FAIL')
print(f'Total tests: {len(results)}, PASS: {total_pass}, FAIL: {total_fail}')
print('By group:', groups)

fails = [r for r in results if r['status'] == 'FAIL']
print('\nFAILURES:')
for r in fails:
    det = str(r.get('details', ''))[:80]
    print(f"  {r['id']} [{r.get('category')}]: {det}")

