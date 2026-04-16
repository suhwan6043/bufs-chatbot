"""Slicing ON / OFF / 완화판 응답 속도 비교."""
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
        times = [r["elapsed_s"] for r in d["results"] if not r["prediction"].startswith("[ERROR]")]
        by_ds[dname] = times
    return by_ds


on = load("reports/eval_contains_f1/*_slicing_on_*.json")
off = load("reports/eval_contains_f1/*_slicing_off_*.json")
mit = load("reports/eval_contains_f1/*_mitigated_*.json")


def stats(times):
    if not times:
        return (0, 0, 0, 0, 0)
    return (
        len(times),
        statistics.mean(times),
        statistics.median(times),
        statistics.quantiles(times, n=20)[18] if len(times) >= 20 else max(times),  # P95
        max(times),
    )


print(f"{'dataset':<30} {'mode':<10} {'n':>4} {'mean':>7} {'median':>7} {'p95':>7} {'max':>7}")
print("-" * 80)
for dname in on.keys():
    for label, data in [("ON", on), ("OFF", off), ("MIT", mit)]:
        n, mean, med, p95, mx = stats(data.get(dname, []))
        print(f"{dname:<30} {label:<10} {n:>4} {mean:>7.2f} {med:>7.2f} {p95:>7.2f} {mx:>7.2f}")
    print()

# 전체 합산
print("=" * 80)
print("전체 합산 (3개 데이터셋 통합)")
print("-" * 80)
for label, data in [("ON", on), ("OFF", off), ("MIT", mit)]:
    all_times = []
    for times in data.values():
        all_times.extend(times)
    n, mean, med, p95, mx = stats(all_times)
    print(f"{'[ALL]':<30} {label:<10} {n:>4} {mean:>7.2f} {med:>7.2f} {p95:>7.2f} {mx:>7.2f}")

# ON vs OFF 차이
print()
print("=" * 80)
print("ON → OFF 속도 차이")
print("-" * 80)
on_all = [t for ts in on.values() for t in ts]
off_all = [t for ts in off.values() for t in ts]
delta_mean = statistics.mean(off_all) - statistics.mean(on_all)
delta_pct = delta_mean / statistics.mean(on_all) * 100 if statistics.mean(on_all) else 0
print(f"평균 {statistics.mean(on_all):.2f}s → {statistics.mean(off_all):.2f}s  ({delta_mean:+.2f}s, {delta_pct:+.1f}%)")
