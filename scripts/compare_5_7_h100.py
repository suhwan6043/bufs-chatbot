"""어제 measurement (responses_new.jsonl, 로컬 CPU + gemma4:e4b) vs
오늘 H100 measurement (responses_h100.jsonl, gemma4:26b)을 비교.

객관적 LLM 성능 차이 분석:
  1. intent 분포 차이
  2. 응답 길이 분포 (길이 = 정보량 proxy)
  3. duration_ms 분포 (응답 시간)
  4. 동일 GT 기준으로 어제 verdict 적용 가능성 (자동 휴리스틱)
  5. 30개 핵심 idx에 대해 답변 텍스트 비교 표

출력: reports/eval_5_7/COMPARE_h100_vs_yesterday.md
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent.parent
DIR = BASE / "reports" / "eval_5_7"


def load_concat_json(path: Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8").strip()
    import json as _j
    dec = _j.JSONDecoder()
    pos, items = 0, []
    while pos < len(txt):
        while pos < len(txt) and txt[pos] in " \t\n\r":
            pos += 1
        if pos >= len(txt):
            break
        obj, end = dec.raw_decode(txt, pos)
        items.append(obj)
        pos = end
    return items


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    y = load_jsonl(DIR / "responses_new.jsonl")  # 어제 (로컬 CPU 환경)
    h = load_jsonl(DIR / "responses_h100.jsonl")  # 오늘 (H100)
    g_new = load_concat_json(DIR / "graded.jsonl")  # 어제 채점 (new = 어제 환경)
    g_old = load_concat_json(DIR / "graded_old.jsonl")
    targets = json.loads((DIR / "check_targets.json").read_text(encoding="utf-8"))

    y_by_idx = {r["idx"]: r for r in y}
    h_by_idx = {r["idx"]: r for r in h}
    gnew_by_idx = {r["idx"]: r for r in g_new}
    gold_by_idx = {r["idx"]: r for r in g_old}

    print(f"어제 (responses_new.jsonl): {len(y)} rows")
    print(f"오늘 (responses_h100.jsonl): {len(h)} rows")
    print()

    # 1) intent 분포
    yc = Counter(r.get("new_intent", "") for r in y)
    hc = Counter(r.get("h100_intent", "") for r in h)
    intents = sorted(set(yc) | set(hc))
    print("=== intent 분포 ===")
    print(f"{'intent':32s} {'yesterday':>10} {'h100':>10} {'Δ':>8}")
    for i in intents:
        delta = hc.get(i, 0) - yc.get(i, 0)
        print(f"{i:32s} {yc.get(i,0):>10} {hc.get(i,0):>10} {delta:+8d}")
    print()

    # 2) 응답 길이
    y_lens = [len(r.get("new_answer", "") or "") for r in y]
    h_lens = [len(r.get("h100_answer", "") or "") for r in h]
    print("=== 응답 길이 (chars) ===")
    print(f"  yesterday: avg={statistics.mean(y_lens):.0f}, median={statistics.median(y_lens):.0f}, "
          f"min={min(y_lens)}, max={max(y_lens)}")
    print(f"  h100:      avg={statistics.mean(h_lens):.0f}, median={statistics.median(h_lens):.0f}, "
          f"min={min(h_lens)}, max={max(h_lens)}")
    print()

    # 3) duration
    y_dur = [r.get("new_duration_ms", 0) for r in y]
    h_dur = [r.get("h100_duration_ms", 0) for r in h]
    print("=== duration_ms ===")
    print(f"  yesterday: avg={statistics.mean(y_dur):.0f}, p50={statistics.median(y_dur):.0f}, "
          f"p95={sorted(y_dur)[int(len(y_dur)*0.95)]}")
    print(f"  h100:      avg={statistics.mean(h_dur):.0f}, p50={statistics.median(h_dur):.0f}, "
          f"p95={sorted(h_dur)[int(len(h_dur)*0.95)]}")
    print()

    # 4) 거부 응답 (refusal) 개수
    REFUSAL_MARK = "관련 정보를 찾을 수 없습니다"
    y_ref = sum(1 for r in y if REFUSAL_MARK in (r.get("new_answer", "") or ""))
    h_ref = sum(1 for r in h if REFUSAL_MARK in (r.get("h100_answer", "") or ""))
    print(f"=== 거부 응답 (refusal) ===")
    print(f"  yesterday: {y_ref}/{len(y)} ({100*y_ref/len(y):.1f}%)")
    print(f"  h100:      {h_ref}/{len(h)} ({100*h_ref/len(h):.1f}%)")
    print()

    # 5) 30 target idx 답변 비교 표 → markdown
    md_lines: list[str] = []
    md_lines.append("# LLM 모델 성능 비교: 어제(로컬 CPU) vs 오늘(H100 gemma4:26b)")
    md_lines.append("")
    md_lines.append("**측정 환경 통제**: 동일 137문, 동일 backend 코드, 동일 retrieval·merging.")
    md_lines.append("차이는 **답변 LLM 모델**(어제 gemma4:e4b 추정 4B effective → 오늘 gemma4:26b 27B effective)")
    md_lines.append("+ **GPU 환경**(어제 호스트 CPU → 오늘 H100 GPU MIG 47GB)")
    md_lines.append("")
    md_lines.append("## 1. 자동 집계")
    md_lines.append("")
    md_lines.append("### Intent 분포")
    md_lines.append("")
    md_lines.append("| intent | 어제 | H100 | Δ |")
    md_lines.append("|---|---:|---:|---:|")
    for i in intents:
        delta = hc.get(i, 0) - yc.get(i, 0)
        md_lines.append(f"| {i} | {yc.get(i,0)} | {hc.get(i,0)} | {delta:+d} |")
    md_lines.append("")
    md_lines.append("### 응답 품질 proxy")
    md_lines.append("")
    md_lines.append(f"| 지표 | 어제 (CPU + e4b 추정) | H100 (gemma4:26b) | 변화 |")
    md_lines.append(f"|---|---:|---:|---:|")
    md_lines.append(f"| 평균 답변 길이 (chars) | {statistics.mean(y_lens):.0f} | {statistics.mean(h_lens):.0f} | {statistics.mean(h_lens)-statistics.mean(y_lens):+.0f} |")
    md_lines.append(f"| 중앙값 답변 길이 | {statistics.median(y_lens):.0f} | {statistics.median(h_lens):.0f} | {statistics.median(h_lens)-statistics.median(y_lens):+.0f} |")
    md_lines.append(f"| 평균 응답 시간 (ms) | {statistics.mean(y_dur):.0f} | {statistics.mean(h_dur):.0f} | {statistics.mean(h_dur)-statistics.mean(y_dur):+.0f} |")
    md_lines.append(f"| 거부 응답 비율 | {100*y_ref/len(y):.1f}% | {100*h_ref/len(h):.1f}% | {100*h_ref/len(h)-100*y_ref/len(y):+.1f}pp |")
    md_lines.append("")

    md_lines.append("## 2. 핵심 30개 사례 (어제 wrong/partial/refusal_unacc) 답변 비교")
    md_lines.append("")
    md_lines.append("각 사례에서 어제 verdict + GT는 어제 Sonnet 채점 결과. 오늘 H100 답변은 직접 fact-check 대상.")
    md_lines.append("")
    for tidx in targets:
        y_r = y_by_idx.get(tidx, {})
        h_r = h_by_idx.get(tidx, {})
        g_r = gnew_by_idx.get(tidx, {})
        q = y_r.get("question") or g_r.get("question") or ""
        gt = g_r.get("ground_truth", "")
        old_verdict = g_r.get("verdict", "?")
        y_ans = (y_r.get("new_answer", "") or "").replace("\n", " ").strip()[:500]
        h_ans = (h_r.get("h100_answer", "") or "").replace("\n", " ").strip()[:500]
        md_lines.append(f"### idx {tidx} — `{old_verdict}` — \"{q}\"")
        md_lines.append("")
        md_lines.append(f"**Ground Truth** (어제 Sonnet 채점):")
        md_lines.append(f"> {gt[:400]}")
        md_lines.append("")
        md_lines.append(f"**어제 답변** ({y_r.get('new_intent','?')}, {y_r.get('new_duration_ms','?')}ms):")
        md_lines.append(f"> {y_ans or '(빈 응답)'}")
        md_lines.append("")
        md_lines.append(f"**오늘 H100 답변** ({h_r.get('h100_intent','?')}, {h_r.get('h100_duration_ms','?')}ms):")
        md_lines.append(f"> {h_ans or '(빈 응답)'}")
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    out_md = DIR / "COMPARE_h100_vs_yesterday.md"
    out_md.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"saved: {out_md}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
