"""Before/After 정성 평가 비교"""
import json

with open('data/eval/qualitative_raw_20260308_221644.json', encoding='utf-8') as f:
    old = json.load(f)
with open('data/eval/qualitative_raw_20260308_225646.json', encoding='utf-8') as f:
    new = json.load(f)

with open('data/eval/eval_results_20260308_143434.json', encoding='utf-8') as f:
    old_eval = json.load(f)
with open('data/eval/eval_results_20260308_225505.json', encoding='utf-8') as f:
    new_eval = json.load(f)

new_items = {r['id']: r for r in new_eval['results']}
ids = sorted(new_items.keys())

old_q = old['qual_results']
new_q = new['qual_results']

print('ID      | 이전               | 이후               | 변화')
print('-' * 70)
for i, id_ in enumerate(ids):
    ot = old_q[i]['error_type']
    nt = new_q[i]['error_type']
    os_ = old_q[i]['correctness_score']
    ns_ = new_q[i]['correctness_score']
    change = ''
    if nt == 'correct' and ot != 'correct':
        change = '개선'
    elif ot == 'correct' and nt != 'correct':
        change = '악화'
    line = f'{id_:<8}| {ot}({os_}){" "*(14-len(ot))}| {nt}({ns_}){" "*(14-len(nt))}| {change}'
    print(line)

print()
old_correct = sum(1 for r in old_q if r['error_type'] == 'correct')
new_correct = sum(1 for r in new_q if r['error_type'] == 'correct')
print(f'correct: {old_correct} -> {new_correct}')

from collections import Counter
print('이전:', Counter(r['error_type'] for r in old_q))
print('이후:', Counter(r['error_type'] for r in new_q))
