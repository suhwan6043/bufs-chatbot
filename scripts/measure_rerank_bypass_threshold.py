"""direct_answer bypass 게이트 — Tier A (CrossEncoder) + Tier B (Q-Q cosine) 실측.

목적:
  context_merger.py의 direct_answer 채택 루프에 두 단계 게이트를 추가할 때
  각 게이트의 임계치 (tA, tB) 와 조합 효과를 운영 로그 기반으로 검증한다.

게이트 정의:
  Tier A — CrossEncoder logit(user_q, direct_answer) >= tA
  Tier B — cosine(embed(user_q), embed(canonical_source_q)) >= tB
           canonical_source_q = FAQ.question 또는 graph 노드의 대표 질문.

데이터 소스:
  운영 로그(app.log)에서 trace_id로 (CHAT_START.question, DIRECT_ANSWER.preview)
  쌍을 추출 → 라벨링(correct/partial/wrong) + canonical_q 수동 매핑.

사용법:
  python scripts/measure_rerank_bypass_threshold.py [labels.jsonl]
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from statistics import mean, median, stdev

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.shared_resources import get_embedder, get_reranker_model  # noqa: E402

DEFAULT_LABELS = ROOT / "scripts" / "data" / "rerank_threshold_labels.jsonl"


def _load_labels(path: Path) -> list[dict]:
    pairs: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pairs.append(json.loads(line))
    return pairs


def _score_cross(model, pairs: list[dict]) -> list[float]:
    inputs = [[p["question"], p["answer"]] for p in pairs]
    return [float(s) for s in model.predict(inputs)]


def _score_qq(embedder, pairs: list[dict]) -> list[float]:
    """user_q와 canonical_q 사이 코사인 유사도 (bge-m3, normalized → cosine = dot product)."""
    sims: list[float] = []
    for p in pairs:
        canon = p.get("canonical_q") or ""
        if not canon:
            sims.append(float("nan"))
            continue
        e_user = embedder.embed_query(p["question"])
        e_canon = embedder.embed_query(canon)
        # bge-m3는 normalize_embeddings=True로 인코딩 → dot product = cosine.
        sims.append(float(np.dot(e_user, e_canon)))
    return sims


def _percentiles(scores: list[float]) -> dict[str, float]:
    s = sorted([x for x in scores if not math.isnan(x)])
    if not s:
        return {}
    n = len(s)
    return {
        "min": s[0],
        "p25": s[max(0, int(n * 0.25) - 1)],
        "median": median(s),
        "p75": s[min(n - 1, int(n * 0.75))],
        "max": s[-1],
        "mean": mean(s),
        "stdev": stdev(s) if n > 1 else 0.0,
    }


def _print_combined(pairs: list[dict], cross: list[float], qq: list[float]) -> None:
    print(f"\n── 전체 케이스 (n={len(pairs)}) — logit + Q-Q cos ──")
    print(f"{'idx':<5s} {'label':<8s} {'cross':>7s}  {'qq_cos':>7s}  question[:46]")
    print("-" * 95)
    rows = sorted(zip(pairs, cross, qq), key=lambda x: (x[0]["label"], x[1]))
    for p, c, q in rows:
        qstr = f"{q:>7.3f}" if not math.isnan(q) else "    -- "
        text = p["question"][:46].replace("\n", " ")
        print(f"#{p['idx']:<4d} {p['label']:<8s} {c:>7.3f}  {qstr}  {text}")


def _print_group_stats(label: str, scores: list[float], score_name: str) -> None:
    pcts = _percentiles(scores)
    if not pcts:
        return
    print(f"  {label:<10s} {score_name}: "
          f"min={pcts['min']:.3f}  p25={pcts['p25']:.3f}  median={pcts['median']:.3f}  "
          f"p75={pcts['p75']:.3f}  max={pcts['max']:.3f}  mean={pcts['mean']:.3f}±{pcts['stdev']:.3f}")


def _confusion_at(pairs: list[dict], cross: list[float], qq: list[float],
                  tA: float, tB: float | None) -> dict:
    """게이트: cross >= tA (AND qq >= tB if tB is not None) → pass."""
    counts = {"wrong": {"pass": 0, "block": 0, "total": 0},
              "partial": {"pass": 0, "block": 0, "total": 0},
              "correct": {"pass": 0, "block": 0, "total": 0}}
    for p, c, q in zip(pairs, cross, qq):
        lbl = p["label"]
        counts[lbl]["total"] += 1
        passed_a = c >= tA
        passed_b = True if tB is None else (not math.isnan(q) and q >= tB)
        if passed_a and passed_b:
            counts[lbl]["pass"] += 1
        else:
            counts[lbl]["block"] += 1
    return counts


def _confusion_or_at(pairs: list[dict], cross: list[float], qq: list[float],
                     tA: float, tB: float) -> dict:
    """게이트 OR: cross >= tA OR qq >= tB → pass (관대)."""
    counts = {"wrong": {"pass": 0, "block": 0, "total": 0},
              "partial": {"pass": 0, "block": 0, "total": 0},
              "correct": {"pass": 0, "block": 0, "total": 0}}
    for p, c, q in zip(pairs, cross, qq):
        lbl = p["label"]
        counts[lbl]["total"] += 1
        a = c >= tA
        b = (not math.isnan(q)) and (q >= tB)
        if a or b:
            counts[lbl]["pass"] += 1
        else:
            counts[lbl]["block"] += 1
    return counts


def _format_row(thresh_label: str, counts: dict, note: str = "") -> str:
    w = counts["wrong"]
    c = counts["correct"]
    p = counts["partial"]
    return (f"{thresh_label:<24s}  "
            f"wrong={w['block']}/{w['total']} 차단  "
            f"correct={c['pass']}/{c['total']} 통과  "
            f"partial={p['pass']}/{p['total']} 통과  {note}")


def _grid_search(pairs: list[dict], cross: list[float], qq: list[float]) -> None:
    print("\n" + "=" * 95)
    print("Tier A + Tier B 조합 그리드 탐색 (AND 게이트)")
    print("=" * 95)
    print(f"{'tA \\ tB':<10s}" + "".join(f"{tB:>10.2f}" for tB in [0.40, 0.50, 0.60, 0.70, 0.80]))
    print("-" * 95)

    # 셀: wrong차단/total · correct통과/total
    for tA in [0.10, 0.20, 0.30, 0.50]:
        line = f"{tA:<10.2f}"
        for tB in [0.40, 0.50, 0.60, 0.70, 0.80]:
            cnt = _confusion_at(pairs, cross, qq, tA, tB)
            w = cnt["wrong"]
            c = cnt["correct"]
            line += f" w{w['block']}/{w['total']}c{c['pass']}/{c['total']}".rjust(10)
        print(line)
    print("\n범례: w=wrong차단/total  c=correct통과/total — 둘 다 max에 가까울수록 좋음")


def _suggest_best(pairs: list[dict], cross: list[float], qq: list[float]) -> None:
    print("\n" + "=" * 95)
    print("후보 게이트 비교 (단독 vs 조합)")
    print("=" * 95)

    # 후보 케이스
    candidates = [
        ("Tier A 단독 0.20",           lambda: _confusion_at(pairs, cross, qq, 0.20, None)),
        ("Tier A 단독 0.30",           lambda: _confusion_at(pairs, cross, qq, 0.30, None)),
        ("Tier A 단독 0.50",           lambda: _confusion_at(pairs, cross, qq, 0.50, None)),
        ("Tier A 단독 0.70",           lambda: _confusion_at(pairs, cross, qq, 0.70, None)),
        ("Tier B 단독 cos>=0.60",      lambda: _confusion_at(pairs, [99]*len(cross), qq, 0.0, 0.60)),
        ("Tier B 단독 cos>=0.70",      lambda: _confusion_at(pairs, [99]*len(cross), qq, 0.0, 0.70)),
        ("Tier B 단독 cos>=0.80",      lambda: _confusion_at(pairs, [99]*len(cross), qq, 0.0, 0.80)),
        ("A 0.20 AND B 0.60",          lambda: _confusion_at(pairs, cross, qq, 0.20, 0.60)),
        ("A 0.20 AND B 0.70",          lambda: _confusion_at(pairs, cross, qq, 0.20, 0.70)),
        ("A 0.20 AND B 0.75",          lambda: _confusion_at(pairs, cross, qq, 0.20, 0.75)),
        ("A 0.20 AND B 0.80",          lambda: _confusion_at(pairs, cross, qq, 0.20, 0.80)),
        ("A 0.30 AND B 0.70",          lambda: _confusion_at(pairs, cross, qq, 0.30, 0.70)),
    ]
    for name, fn in candidates:
        counts = fn()
        print(_format_row(name, counts))


def main() -> None:
    labels_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LABELS
    if not labels_path.exists():
        print(f"라벨 파일 없음: {labels_path}", file=sys.stderr)
        sys.exit(1)

    pairs = _load_labels(labels_path)
    print(f"라벨 데이터 로드: {labels_path} ({len(pairs)}건)")

    print("모델 로딩 중 (bge-reranker-v2-m3 + bge-m3)...")
    cross_model = get_reranker_model()
    embedder = get_embedder()
    print("로딩 완료.")

    cross_scores = _score_cross(cross_model, pairs)
    qq_scores = _score_qq(embedder, pairs)

    _print_combined(pairs, cross_scores, qq_scores)

    # 그룹별 통계
    print("\n── 그룹별 통계 ──")
    groups_cross: dict[str, list[float]] = {"correct": [], "partial": [], "wrong": []}
    groups_qq: dict[str, list[float]] = {"correct": [], "partial": [], "wrong": []}
    for p, c, q in zip(pairs, cross_scores, qq_scores):
        lbl = p["label"]
        if lbl in groups_cross:
            groups_cross[lbl].append(c)
            if not math.isnan(q):
                groups_qq[lbl].append(q)

    for lbl in ("wrong", "partial", "correct"):
        _print_group_stats(lbl, groups_cross[lbl], "cross logit")
        _print_group_stats(lbl, groups_qq[lbl], "Q-Q cos   ")
        print()

    _grid_search(pairs, cross_scores, qq_scores)
    _suggest_best(pairs, cross_scores, qq_scores)


if __name__ == "__main__":
    main()
