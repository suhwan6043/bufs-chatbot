# -*- coding: utf-8 -*-
"""개선 사항 검증 스크립트 - q019/q020/q024/q011/q037 관련 그래프 출력 확인"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from app.graphdb import AcademicGraph

g = AcademicGraph()

# ─── 개선 1: 야간수업 교시표 (q019, q020 해결) ─────────────────────────────
print("=" * 60)
print("[개선 1] 야간수업 교시표 — q019(10교시=18:00), q020(14교시=22:05)")
print("=" * 60)
results = g._query_schedule()
for r in results:
    if "야간수업교시표" in r.text:
        print(r.text)
print()

# ─── 개선 1: OCU 개강 시간 (q024 해결) ─────────────────────────────────────
print("=" * 60)
print("[개선 1] OCU 개강 시간 — q024(2025-09-01 10:00)")
print("=" * 60)
for r in results:
    if "OCU개강" in r.text or "10:00" in r.text:
        # 해당 줄만 출력
        for line in r.text.splitlines():
            if "OCU" in line or "10:00" in line:
                print(line)
print()

# ─── 개선 2: 2019학번 재수강 학점 제한 (q011 해결) ─────────────────────────
print("=" * 60)
print("[개선 2] 2019학번 재수강 제한 — q011(학기 6학점/졸업 24학점)")
print("=" * 60)
rule = g.get_registration_rule("2019")
fmt  = g._fmt_registration_rule("2019", rule)
print(fmt)
print()

# ─── 개선 2: 장바구니 최대 학점 (q037 참고) ─────────────────────────────────
print("=" * 60)
print("[개선 2] 장바구니 최대 학점 — q037(30학점)")
print("=" * 60)
for reg_group in ("2023이후", "2022이전"):
    node = g.G.nodes.get(f"reg_{reg_group}", {})
    print(f"  reg_{reg_group} → 장바구니최대학점: {node.get('장바구니최대학점', '없음')}")
print()

# ─── 개선 3: evaluate.py 중복 제거 확인 ─────────────────────────────────────
print("=" * 60)
print("[개선 3] evaluate.py 중복 print 제거 확인")
print("=" * 60)
with open("scripts/evaluate.py", encoding="utf-8") as f:
    content = f.read()
judge_count   = content.count("LLM Judge    :")
reranker_count = content.count("Reranker     :")
print(f"  'LLM Judge    :' 출력 횟수: {judge_count}  (기대: 1)")
print(f"  'Reranker     :' 출력 횟수: {reranker_count}  (기대: 1)")

# Reranker 워밍업 실패 처리 확인
if "warmed_reranker = False  # False → 라우터가 재시도하지 않음" in content:
    print("  워밍업 실패 처리: OK (False로 설정)")
else:
    print("  워밍업 실패 처리: [확인 필요]")
