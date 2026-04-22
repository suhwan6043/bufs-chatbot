"""
실패 원인 진단 (read-only): 검색 vs 생성 vs 파이프라인

입력: 최신 eval 리포트 per-dataset JSON (balanced/rag/user)
출력: stdout 표 + reports/diagnosis/latest.md

분류 규칙:
  recall=0 && !contains_gt  → R (검색 실패)
  recall>0 && !contains_gt  → G (생성 실패)
  recall=0 &&  contains_gt  → P (파이프라인 우회, graph direct/FAQ)
  recall>0 &&  contains_gt  → 정답

G 세분화: G1=refusal / G2=부분(token_f1>0) / G3=엉뚱(token_f1=0)
R 세분화: R1=결과0개(results_count=0) / R2=결과있으나 top-5 miss

사용:
  python -X utf8 scripts/diagnose_failures.py [--tag s3_cut_065_20260420_003923]
"""
from __future__ import annotations

import argparse
import json
import sys
import io
import re
from pathlib import Path
from collections import defaultdict, Counter

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_f1_score import is_refusal as _raw_is_refusal  # noqa: E402


def _strip_footer(text: str) -> str:
    """답변 말미의 고정 푸터(`📞 학사 문의: 학사지원팀 ...`)와
    `*검증 경고:*` 블록을 제거. is_refusal 패턴이 푸터에 걸려
    모든 답변이 거절로 오분류되는 문제 회피."""
    if not text:
        return text
    t = text
    # 푸터: '---' 뒤 '학사 문의' 또는 '📞' 라인부터 끝까지
    for sep in ("\n---\n", "\n📞", "\n*검증 경고"):
        idx = t.find(sep)
        if idx >= 0:
            t = t[:idx]
    return t.strip()


def is_refusal(prediction: str) -> bool:
    return _raw_is_refusal(_strip_footer(prediction or ""))

REPORT_DIR = ROOT / "reports" / "eval_contains_f1"
OUT_DIR = ROOT / "reports" / "diagnosis"
DATASETS = ("balanced_test_set", "rag_eval_dataset_2026_1", "user_eval_dataset_50")


def _find_latest_tag() -> str:
    # combined_<tag>_<timestamp>.json 중 최신
    files = sorted(REPORT_DIR.glob("combined_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("combined_*.json 리포트 없음")
    m = re.match(r"combined_(.+)\.json$", files[0].name)
    return m.group(1) if m else files[0].stem.replace("combined_", "")


def _load(tag: str, ds: str) -> dict:
    path = REPORT_DIR / f"{ds}_{tag}.json"
    if not path.exists():
        raise SystemExit(f"없음: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def classify(row: dict) -> tuple[str, str]:
    """(main, sub) 분류. main ∈ {OK, R, G, P}, sub 세분화."""
    retrieval = row.get("retrieval") or {}
    recall = float(retrieval.get("recall") or 0.0)
    hit = retrieval.get("hit_rank")
    contains = bool(row.get("contains_gt"))
    token_f1 = float(row.get("token_f1") or 0.0)
    pred = row.get("prediction") or ""
    results_count = int(row.get("results_count") or 0)
    answerable = bool(row.get("answerable", True))

    # unanswerable(문서 밖) 문항은 retrieval 자체가 N/A → 별도 처리
    if not answerable:
        if contains:
            return ("OK", "unanswerable_ok")
        # 답변 거절 기대인데 답한 경우
        return ("G", "G4_unanswerable_missed")

    hit_ok = (hit is not None) and (hit >= 1) and (hit <= 5)

    if hit_ok and contains:
        return ("OK", "ok")
    if hit_ok and not contains:
        if is_refusal(pred):
            return ("G", "G1_refusal")
        if token_f1 > 0.1:
            return ("G", "G2_partial")
        return ("G", "G3_offtopic")
    if (not hit_ok) and contains:
        # recall metric이 놓친 경로 (graph direct / FAQ / alias)
        return ("P", "P_bypass")
    # !hit_ok and !contains
    if results_count == 0:
        return ("R", "R1_no_results")
    return ("R", "R2_miss_top5")


def summarize(tag: str) -> dict:
    out = {"tag": tag, "datasets": {}, "cases": defaultdict(list)}
    for ds in DATASETS:
        data = _load(tag, ds)
        rows = data.get("results", [])
        counters = Counter()
        sub_counters = Counter()
        for r in rows:
            main, sub = classify(r)
            counters[main] += 1
            sub_counters[sub] += 1
            if main in ("R", "G", "P"):
                out["cases"][sub].append({
                    "ds": ds,
                    "id": r.get("id"),
                    "q": r.get("question", "")[:80],
                    "gt": (r.get("ground_truth") or "")[:80],
                    "pred": (r.get("prediction") or "")[:80],
                    "recall": (r.get("retrieval") or {}).get("recall"),
                    "hit_rank": (r.get("retrieval") or {}).get("hit_rank"),
                    "token_f1": r.get("token_f1"),
                    "results_count": r.get("results_count"),
                })
        out["datasets"][ds] = {
            "total": len(rows),
            "main": dict(counters),
            "sub": dict(sub_counters),
        }
    return out


def render(out: dict) -> str:
    lines = [f"# 실패 진단 (tag: {out['tag']})\n"]
    lines.append("## 데이터셋별 분류\n")
    lines.append("| 데이터셋 | 전체 | OK | R(검색) | G(생성) | P(우회) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    totals = Counter()
    for ds, d in out["datasets"].items():
        m = d["main"]
        totals["total"] += d["total"]
        for k in ("OK", "R", "G", "P"):
            totals[k] += m.get(k, 0)
        lines.append(f"| {ds} | {d['total']} | {m.get('OK',0)} | {m.get('R',0)} | {m.get('G',0)} | {m.get('P',0)} |")
    lines.append(f"| **합계** | **{totals['total']}** | **{totals['OK']}** | **{totals['R']}** | **{totals['G']}** | **{totals['P']}** |")
    lines.append("")
    lines.append("## 세분화 합계\n")
    sub_total = Counter()
    for d in out["datasets"].values():
        for k, v in d["sub"].items():
            sub_total[k] += v
    for k in sorted(sub_total):
        lines.append(f"- `{k}`: {sub_total[k]}")
    lines.append("")
    lines.append("## 대표 케이스 (각 분류당 최대 10건)\n")
    for sub, items in sorted(out["cases"].items()):
        lines.append(f"### {sub} ({len(items)}건)\n")
        for c in items[:10]:
            lines.append(
                f"- [{c['ds'][:8]}/{c['id']}] recall={c['recall']} rc={c['results_count']} f1={c['token_f1']}\n"
                f"  - Q: {c['q']}\n"
                f"  - GT: {c['gt']}\n"
                f"  - Pred: {c['pred']}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=None, help="리포트 태그 (기본: 최신 combined)")
    args = ap.parse_args()
    tag = args.tag or _find_latest_tag()
    out = summarize(tag)
    md = render(out)
    print(md)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "latest.md").write_text(md, encoding="utf-8")
    (OUT_DIR / f"{tag}.md").write_text(md, encoding="utf-8")
    print(f"\n[saved] {OUT_DIR / 'latest.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
