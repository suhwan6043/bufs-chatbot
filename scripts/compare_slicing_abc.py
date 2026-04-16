"""Phase A/B/C 결과 항목별 비교 — 0건 퇴행 검증 및 회복 항목 목록."""
import json
import glob
from pathlib import Path


def main():
    on_files = sorted(glob.glob("reports/eval_contains_f1/*_slicing_on_*.json"))
    off_files = sorted(glob.glob("reports/eval_contains_f1/*_slicing_off_*.json"))
    mit_files = sorted(glob.glob("reports/eval_contains_f1/*_mitigated_*.json"))
    on_files = [f for f in on_files if "combined" not in f]
    off_files = [f for f in off_files if "combined" not in f]
    mit_files = [f for f in mit_files if "combined" not in f]

    for on_f, off_f, mit_f in zip(on_files, off_files, mit_files):
        on_d = json.load(open(on_f, encoding="utf-8"))
        off_d = json.load(open(off_f, encoding="utf-8"))
        mit_d = json.load(open(mit_f, encoding="utf-8"))
        on_map = {r["id"]: r["contains_gt"] for r in on_d["results"]}
        off_map = {r["id"]: r["contains_gt"] for r in off_d["results"]}
        mit_map = {r["id"]: r["contains_gt"] for r in mit_d["results"]}
        name = Path(on_f).stem.rsplit("_slicing_on_", 1)[0]
        reg_on_off = sorted([i for i in on_map if on_map[i] and not off_map.get(i, False)])
        imp_on_off = sorted([i for i in on_map if not on_map[i] and off_map.get(i, False)])
        reg_on_mit = sorted([i for i in on_map if on_map[i] and not mit_map.get(i, False)])
        imp_on_mit = sorted([i for i in on_map if not on_map[i] and mit_map.get(i, False)])
        print(f"=== {name} ===")
        print(f"  ON → OFF : 퇴행 {len(reg_on_off)}건 {reg_on_off}")
        print(f"             회복 {len(imp_on_off)}건 {imp_on_off}")
        print(f"  ON → MIT : 퇴행 {len(reg_on_mit)}건 {reg_on_mit}")
        print(f"             회복 {len(imp_on_mit)}건 {imp_on_mit}")
        print()


if __name__ == "__main__":
    main()
