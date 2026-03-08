# -*- coding: utf-8 -*-
import pickle, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

with open("data/graphs/academic_graph.pkl", "rb") as f:
    G = pickle.load(f)

print("=== 기말고사 관련 노드 ===")
for nid, d in G.nodes(data=True):
    if "기말" in nid or "기말" in str(d.get("이벤트명", "")):
        start = d.get("시작일", "-")
        end   = d.get("종료일", "-")
        print(f"  {nid}  ({start} ~ {end})")

print()
print("=== 전체 학사일정 (시작일 있는 것만) ===")
for nid, d in G.nodes(data=True):
    if d.get("type") == "학사일정" and d.get("시작일"):
        print(f"  {d.get('이벤트명')}: {d.get('시작일')}~{d.get('종료일')}")
