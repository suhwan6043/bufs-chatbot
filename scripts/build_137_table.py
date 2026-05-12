"""scored_137_h100.jsonl 을 137행 markdown 표 + 통계 보고서로 출력.

표 컬럼: idx | Q | yesterday_verdict | h100_verdict | change | 근거
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent.parent
DIR = BASE / "reports" / "eval_5_7"


def main() -> int:
    rows = [json.loads(l) for l in (DIR / "scored_137_h100.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows.sort(key=lambda r: r["idx"])

    # 직접 검토 verdict 보정 (review 20건)
    manual_path = DIR / "review_manual_verdicts.json"
    if manual_path.exists():
        manual = json.loads(manual_path.read_text(encoding="utf-8"))
        for r in rows:
            mv = manual["verdicts"].get(str(r["idx"]))
            if mv:
                r["h100_verdict"] = mv["verdict"]
                r["manual_reason"] = mv["reason"]

    # 변화 분류
    SCORE = {"correct": 3, "partial": 2, "refusal_acceptable": 1, "wrong": 0, "refusal_unacceptable": 0, "refusal": 1, "review": -1}
    def change_label(y: str, h: str) -> str:
        if h == "review":
            return "review"
        ys, hs = SCORE.get(y, 0), SCORE.get(h, 0)
        if hs > ys: return "improved"
        if hs < ys: return "regressed"
        return "unchanged"

    for r in rows:
        r["change"] = change_label(r.get("yesterday_verdict", ""), r.get("h100_verdict", ""))

    # 통계
    y_dist = Counter(r.get("yesterday_verdict") for r in rows)
    h_dist = Counter(r.get("h100_verdict") for r in rows)
    chg_dist = Counter(r["change"] for r in rows)

    lines: list[str] = []
    lines.append("# 137문 정답률 직접 채점 결과")
    lines.append("")
    lines.append("**대상**: 5/7 chat_2026-05-07.jsonl 137 unique 질문")
    lines.append("**환경**: H100 + gemma4:26b 답변 / gemma3:4b 분류 (SSH 터널)")
    lines.append("**비교**: 어제 graded.jsonl (로컬 CPU + gemma4:e4b)")
    lines.append("")
    lines.append("## 채점 방법론")
    lines.append("")
    lines.append("**자동 1차 채점** (`scripts/score_137_h100.py`):")
    lines.append("- 답변에 \"관련 정보를 찾을 수 없습니다\" + 본문 <180자 → `refusal`")
    lines.append("- GT 핵심 키워드 (면제·불가·가능·휴업일·부서명) 모순 → `wrong`")
    lines.append("- GT 토큰(숫자·연락처·부서·날짜·URL) 매칭률 + GT-답변 단어 overlap:")
    lines.append("  - 핵심 키워드 일치 + overlap≥0.55 → `correct`")
    lines.append("  - 토큰 매칭률 ≥0.85 + overlap≥0.4 → `correct`")
    lines.append("  - 매칭률 ≥0.5 or overlap≥0.45 → `partial`")
    lines.append("  - 매칭률 ≤0.25 or overlap<0.15 → `wrong`")
    lines.append("  - 그 외 + 어제 답변과 70%+ 유사 → 어제 verdict 상속")
    lines.append("  - 그래도 결정 못 함 → `review`")
    lines.append("- 어제 GT는 `graded.jsonl` (어제 Sonnet 직접 fact-check 결과)")
    lines.append("")
    lines.append(f"**⚠ 한계**: 자동 휴리스틱은 형식 차이(list vs 문장)에 약하고 답변이 길어 GT를 포함하지만 다른 정보도 섞인 경우 `partial`로 떨어트림. 따라서 정답률은 직접 검토 기준 30 사례 결과(`LLM_PERF_VERIFICATION.md`)와 함께 해석.")
    lines.append("")

    # 통계 표
    lines.append("## 1. 정답률 비교 (137문 전체)")
    lines.append("")
    lines.append("| verdict | 어제 (graded) | H100 (자동) | 변화 |")
    lines.append("|---|---:|---:|---:|")
    all_verdicts = sorted(set(list(y_dist.keys()) + list(h_dist.keys())))
    for v in all_verdicts:
        if not v:
            continue
        yc, hc = y_dist.get(v, 0), h_dist.get(v, 0)
        lines.append(f"| {v} | {yc} | {hc} | {hc-yc:+d} |")
    lines.append(f"| **합계** | **{sum(y_dist.values())}** | **{sum(h_dist.values())}** | — |")
    lines.append("")
    # 점수 평균
    avg_y = sum(SCORE.get(r.get("yesterday_verdict",""), 0) for r in rows) / len(rows)
    avg_h = sum(SCORE.get(r.get("h100_verdict",""), 0) for r in rows if r["h100_verdict"] != "review") / max(1, sum(1 for r in rows if r["h100_verdict"] != "review"))
    correct_y = (y_dist.get("correct", 0)) / len(rows)
    correct_h = (h_dist.get("correct", 0)) / len(rows)
    partial_or_correct_y = (y_dist.get("correct",0) + y_dist.get("partial",0)) / len(rows)
    partial_or_correct_h = (h_dist.get("correct",0) + h_dist.get("partial",0)) / len(rows)
    lines.append("**점수 환산 (correct=3, partial=2, refusal_acc=1, wrong/refusal_unacc=0):**")
    lines.append("")
    lines.append(f"| 지표 | 어제 | H100 (자동) |")
    lines.append(f"|---|---:|---:|")
    lines.append(f"| correct 비율 | {100*correct_y:.1f}% | {100*correct_h:.1f}% |")
    lines.append(f"| correct+partial 비율 | {100*partial_or_correct_y:.1f}% | {100*partial_or_correct_h:.1f}% |")
    lines.append(f"| 평균 점수 (0~3) | {avg_y:.2f} | {avg_h:.2f} (review 제외) |")
    lines.append("")
    lines.append("> ⚠ H100 자동 채점은 보수적. 직접 검토 시(30 사례 표본 기준 `LLM_PERF_VERIFICATION.md`):")
    lines.append("> - improved 11/30 (37%), improved-safe 6/30 (20%), regressed 1/30 (3%)")
    lines.append("> → 정답률 +10pp 이상 향상 추정. wrong→refusal 전환은 점수상 동일이지만 안전성 ↑")
    lines.append("")

    # 변화 분포
    lines.append("## 2. 어제 → H100 변화 분포")
    lines.append("")
    lines.append(f"| 변화 | 건수 | 비율 |")
    lines.append(f"|---|---:|---:|")
    for k in ["improved", "unchanged", "regressed", "review"]:
        c = chg_dist.get(k, 0)
        lines.append(f"| {k} | {c} | {100*c/len(rows):.1f}% |")
    lines.append("")

    # intent 분포 변화
    y_intent = Counter(r.get("yesterday_intent") for r in rows)
    h_intent = Counter(r.get("h100_intent") for r in rows)
    lines.append("## 3. Intent 분포 변화 (SSH 터널 latency로 분류기 룰 폴백)")
    lines.append("")
    lines.append("| intent | 어제 | H100 | Δ |")
    lines.append("|---|---:|---:|---:|")
    for i in sorted(set(list(y_intent.keys()) + list(h_intent.keys()))):
        if not i:
            continue
        delta = h_intent.get(i, 0) - y_intent.get(i, 0)
        lines.append(f"| {i} | {y_intent.get(i,0)} | {h_intent.get(i,0)} | {delta:+d} |")
    lines.append("")

    # 137 사례별 표
    lines.append("## 4. 사례별 정답률 (137행)")
    lines.append("")
    lines.append("| idx | Q | 어제 verdict | H100 verdict | 변화 | 근거 (자동 채점) |")
    lines.append("|---:|---|---|---|---|---|")
    for r in rows:
        idx = r["idx"]
        q = (r.get("question") or "").replace("|", "\\|").replace("\n", " ")[:50]
        yv = r.get("yesterday_verdict", "") or "—"
        hv = r.get("h100_verdict", "") or "—"
        chg = r["change"]
        d = r.get("score_details", {})
        # 근거 한 줄
        if d.get("refusal"):
            evid = "거부 응답"
        elif d.get("polarity_conflict"):
            evid = "polarity 모순"
        elif d.get("critical_keyword_ok") is False:
            evid = f"핵심 키워드 '{d.get('critical_keyword')}' 모순"
        elif d.get("critical_keyword_ok") is True:
            evid = f"핵심 키워드 '{d.get('critical_keyword')}' 일치"
        else:
            r_tok = d.get("token_match_ratio")
            r_ovl = d.get("gt_overlap_ratio")
            evid = f"토큰 {r_tok}, overlap {r_ovl}"
        lines.append(f"| {idx} | {q} | {yv} | {hv} | {chg} | {evid} |")
    lines.append("")

    # review 상세
    review_rows = [r for r in rows if r["h100_verdict"] == "review"]
    if review_rows:
        lines.append(f"## 5. 직접 검토 필요 (`review` {len(review_rows)}건)")
        lines.append("")
        lines.append("자동 휴리스틱이 판단 못 한 케이스. 어제 verdict + H100 답변 본문 50자.")
        lines.append("")
        for r in review_rows:
            lines.append(f"### idx {r['idx']} — 어제 verdict: `{r.get('yesterday_verdict')}`")
            lines.append(f"**Q**: {r.get('question','')[:100]}")
            lines.append(f"**GT**: {r.get('ground_truth','')[:200]}")
            lines.append(f"**H100 답변**: {(r.get('h100_answer','') or '').replace(chr(10),' ')[:300]}")
            lines.append(f"**근거**: overlap={r['score_details'].get('gt_overlap_ratio')}, "
                         f"token_match={r['score_details'].get('token_match_ratio')}, "
                         f"body_len={r['score_details'].get('body_len')}")
            lines.append("")

    out = DIR / "TABLE_137_h100.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved: {out}")
    print(f"  total: {len(rows)}")
    print(f"  yesterday: {dict(y_dist)}")
    print(f"  h100: {dict(h_dist)}")
    print(f"  change: {dict(chg_dist)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
