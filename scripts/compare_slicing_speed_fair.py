"""Slicing ON/OFF/완화판 응답 속도 공정 비교.
- 첫 3문항(warmup) 제외
- 외치(outlier) 제외 후 중간값·백분위 비교
"""
import json
import glob
import statistics
from pathlib import Path


def load(pattern):
    files = [f for f in sorted(glob.glob(pattern)) if "combined" not in f]
    by_ds = {}
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        dname = Path(f).stem.split("_slicing_")[0].split("_mitigated")[0]
        results = d["results"]
        # warmup 제외: 처음 3문항 버림
        warmed = results[3:]
        times = [r["elapsed_s"] for r in warmed if not r["prediction"].startswith("[ERROR]")]
        by_ds[dname] = times
    return by_ds


on = load("reports/eval_contains_f1/*_slicing_on_*.json")
off = load("reports/eval_contains_f1/*_slicing_off_*.json")
mit = load("reports/eval_contains_f1/*_mitigated_*.json")


def stats(times):
    if not times:
        return (0, 0, 0, 0, 0, 0)
    n = len(times)
    mean = statistics.mean(times)
    med = statistics.median(times)
    p75 = statistics.quantiles(times, n=4)[2] if n >= 4 else max(times)
    p95 = statistics.quantiles(times, n=20)[18] if n >= 20 else max(times)
    mx = max(times)
    return (n, mean, med, p75, p95, mx)


print(f"warmup(첫 3문항) 제외 후 비교")
print()
print(f"{'dataset':<30} {'mode':<5} {'n':>4} {'mean':>7} {'median':>7} {'p75':>7} {'p95':>7} {'max':>7}")
print("-" * 80)
for dname in on.keys():
    for label, data in [("ON", on), ("OFF", off), ("MIT", mit)]:
        n, mean, med, p75, p95, mx = stats(data.get(dname, []))
        print(f"{dname:<30} {label:<5} {n:>4} {mean:>7.2f} {med:>7.2f} {p75:>7.2f} {p95:>7.2f} {mx:>7.2f}")
    print()

# 중간값 기반 비교 (outlier 영향 최소화)
print("=" * 80)
print("전체 통합 — 중간값 기준 (이상치에 강함)")
print("-" * 80)
for label, data in [("ON", on), ("OFF", off), ("MIT", mit)]:
    all_times = [t for ts in data.values() for t in ts]
    n, mean, med, p75, p95, mx = stats(all_times)
    print(f"{'[ALL]':<30} {label:<5} {n:>4} {mean:>7.2f} {med:>7.2f} {p75:>7.2f} {p95:>7.2f} {mx:>7.2f}")

# 동일 질문에 대한 직접 비교 (가장 공정)
print()
print("=" * 80)
print("동일 질문 per-item 비교 (가장 공정한 비교)")
print("-" * 80)


def per_item(pattern):
    files = [f for f in sorted(glob.glob(pattern)) if "combined" not in f]
    m = {}
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        for r in d["results"]:
            if not r["prediction"].startswith("[ERROR]"):
                m[r["id"]] = r["elapsed_s"]
    return m


on_item = per_item("reports/eval_contains_f1/*_slicing_on_*.json")
off_item = per_item("reports/eval_contains_f1/*_slicing_off_*.json")
mit_item = per_item("reports/eval_contains_f1/*_mitigated_*.json")

common = set(on_item) & set(off_item) & set(mit_item)
diffs_on_off = [on_item[i] - off_item[i] for i in common]
diffs_on_mit = [on_item[i] - mit_item[i] for i in common]

print(f"공통 {len(common)}문항에서")
print(f"  ON - OFF 평균 차이:  {statistics.mean(diffs_on_off):+.2f}s  (양수 = ON이 더 느림)")
print(f"  ON - OFF 중간값 차이:{statistics.median(diffs_on_off):+.2f}s")
print(f"  ON - MIT 평균 차이:  {statistics.mean(diffs_on_mit):+.2f}s")
print(f"  ON - MIT 중간값 차이:{statistics.median(diffs_on_mit):+.2f}s")

# 승패 카운트
on_wins = sum(1 for i in common if on_item[i] < off_item[i])
off_wins = sum(1 for i in common if off_item[i] < on_item[i])
ties = sum(1 for i in common if abs(on_item[i] - off_item[i]) < 0.1)
print(f"\n  per-item 승패 (ON vs OFF):")
print(f"    ON이 빠른 문항:  {on_wins}건")
print(f"    OFF가 빠른 문항: {off_wins}건")
print(f"    동률(<0.1s):    {ties}건")
