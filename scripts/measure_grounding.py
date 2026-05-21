"""direct_answer grounding dry-run 측정.

목적:
  enforce 모드 켜기 전, 실제 direct 트래픽에 grounding 게이트가 어떤
  비율로 통과/차단하는지 측정. 차단율을 모른 채 enforce 켜면
  generate 부하 급증 또는 장식 코드 위험.

## 측정 시나리오

  A. 실 direct 쿼리 (app.log 추출 24건):
     각 쿼리를 backend SSE로 호출 → backend log의 GROUNDING_CHECK 라인 추출.
     - grounded 비율 → 게이트 통과율
     - missing fact 분포 → 어떤 fact가 가장 자주 누락되는지

  B. 합성 reject 시나리오 (cohort 변형):
     실 direct_answer 텍스트의 학점·학번 숫자를 의도적으로 변경.
     같은 chunks와 비교했을 때 reject되는지 확인.
     - 차단 효과 검증 ("게이트가 실제로 환각을 잡는가")

## 해석 기준 (95/70)

  - **grounded 비율 ≥ 95%**: 게이트가 거의 모든 걸 통과시킴 → 너무 느슨.
    → substring 매칭의 한계 명백. 근접-윈도우 보강 또는 cohort-aware 개선 필요.
  - **grounded 비율 ≤ 70%**: 차단율 30%+ → direct→stream 전환 폭증.
    → generate 부하 우려 (세마포어 2 + 60s timeout). enforce 보류, 임계 완화.
  - **70% < grounded 비율 < 95%**: 정상 범위. enforce 켤 만함.

  + 합성 reject 시나리오는 **100% 차단**이어야 정상. 실패 시 게이트 무용지물.

사용:
  # 1. 백엔드를 dry-run 모드로 시작 (GROUNDING_MODE=dry_run, default)
  bash scripts/run_backend.sh > /tmp/backend.log 2>&1 &

  # 2. 측정 실행
  python scripts/measure_grounding.py --backend-log /tmp/backend.log \
      --base-url http://localhost:8000

  # 3. 결과 리포트 자동 출력
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx


# ── app.log에서 추출한 24 direct 쿼리 ─────────────────────────────────
# 실제 운영 로그 (5/11)에서 path=direct인 쿼리들 (capture_golden 작업 시 추출).
# 중복 제거된 unique 쿼리만.

REAL_DIRECT_QUERIES = [
    "졸업학점 알려줘",
    "졸업 요건",
    "수강신청 기간",
    "졸업요건 중 전공과 교양 기준 알려줘",
    "졸업학점 영역별로 알려줘",
    "수강신청 일정 알려줘",
    "계절학기 학점 제한 알려줘",
    "중간고사 기간 언제야?",
    "학사 관련 자주 묻는 질문을 알려줘",
    "학사일정 알려줘",
    "이번 학기 주요 학사일정을 알려줘",
    "수강신청 날짜 알려줘",
    "재수강 후보 2과목 (6학점) — B0 이하 성적 과목",
    "재수강 후보 3과목 (9학점) — B0 이하 성적 과목",
    "졸업 학점 요건 충족 — 총 131/130학점 취득 완료",
    "4학년 2학기 이수중인데 졸업까지 3학점이 모자릅니다",
    "수강신청이 아니라 휴학이라니까요",
    "학생증",
    "계절학기",
    "계절학기가 뭐야",
]


# ── 합성 reject 쿼리 (날조 시나리오) ───────────────────────────────
# 정상 응답이 chunks에 grounding 가능하지만, 의도적으로 답변 텍스트의 fact를
# 변형하면 reject되어야 정상. 직접 backend 호출은 불가하므로
# grounding.is_grounded()를 직접 호출하여 검증.

SYNTHETIC_FABRICATIONS = [
    {
        "label": "fake_credit",
        "direct_answer": "2023학번 내국인 학생의 졸업학점은 999학점입니다.",
        "chunks_text": ["Q: 졸업학점이 얼마인가요?\nA: 2023학번 내국인 학생의 졸업학점은 120학점입니다."],
        "expect_grounded": False,
        "expect_missing_contains": "credit:999",
    },
    {
        "label": "fake_phone",
        "direct_answer": "학사지원팀(051-999-9999)에 문의하세요.",
        "chunks_text": ["학사지원팀 051-509-5182로 문의"],
        "expect_grounded": False,
        "expect_missing_contains": "phone:051-999-9999",
    },
    {
        "label": "fake_url",
        "direct_answer": "수강신청은 fake-sugang.bufs.ac.kr 에서 가능합니다.",
        "chunks_text": ["진짜 URL: https://sugang.bufs.ac.kr"],
        "expect_grounded": False,
        "expect_missing_contains": "url_bufs:fake-sugang.bufs.ac.kr",
    },
    {
        "label": "fake_cohort",
        "direct_answer": "2030학번 졸업학점은 120학점입니다.",
        "chunks_text": ["2023학번 120학점 적용"],
        "expect_grounded": False,
        "expect_missing_contains": "cohort:2030학번",
    },
    {
        "label": "real_intact",  # 대조군: 실제 응답은 grounded여야
        "direct_answer": "2023학번 내국인 학생의 졸업학점은 120학점입니다.",
        "chunks_text": ["Q: 졸업학점이 얼마인가요?\nA: 2023학번 내국인 학생의 졸업학점은 120학점입니다."],
        "expect_grounded": True,
        "expect_missing_contains": None,
    },
]


# ── 백엔드 SSE 호출 ───────────────────────────────────────────────────

def _call_backend(base_url: str, query: str) -> int:
    """SSE 호출 1회 (응답 본문 무시). backend log에 GROUNDING_CHECK 기록되도록만."""
    session_id = f"grounding-{uuid.uuid4().hex[:8]}"
    url = f"{base_url.rstrip('/')}/api/chat/stream"
    params = {"session_id": session_id, "question": query}
    try:
        with httpx.Client(timeout=120.0) as client:
            with client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                # iter_lines로 완료까지 흘려보냄
                for _ in response.iter_lines():
                    pass
        return 0
    except Exception as e:
        print(f"  [ERR] {query[:40]}: {e}", file=sys.stderr)
        return 1


# ── backend log에서 GROUNDING_CHECK 라인 추출 ──────────────────────

_GROUNDING_LINE_RE = re.compile(r"GROUNDING_CHECK (\{.*\})\s*$")


def _read_grounding_lines(log_path: Path, since_pos: int = 0) -> list[dict]:
    """backend log 파일에서 GROUNDING_CHECK 줄 파싱 → dict 리스트.
    since_pos: 이 바이트 위치 이후만 읽음 (측정 전후 누적 차단)."""
    if not log_path.exists():
        return []
    records = []
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(since_pos)
        for line in f:
            m = _GROUNDING_LINE_RE.search(line)
            if not m:
                continue
            try:
                records.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                continue
    return records


def _log_file_size(log_path: Path) -> int:
    return log_path.stat().st_size if log_path.exists() else 0


# ── 합성 시나리오 직접 검증 ─────────────────────────────────────────

def _verify_synthetic():
    """SYNTHETIC_FABRICATIONS를 is_grounded()로 직접 검증."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.models import SearchResult
    from app.pipeline.grounding import is_grounded

    print("\n── 합성 reject 시나리오 (chunks 변형 없이 직접) ──")
    print(f"{'label':<22s} {'grounded':>10s} {'missing':>20s} {'OK':>4s}")
    print("-" * 70)
    pass_count = 0
    for s in SYNTHETIC_FABRICATIONS:
        chunks = [SearchResult(text=t, score=1.0, source="syn", metadata={}) for t in s["chunks_text"]]
        grounded, missing = is_grounded(s["direct_answer"], chunks)
        expected_match = grounded == s["expect_grounded"]
        missing_match = (
            s["expect_missing_contains"] is None
            or any(s["expect_missing_contains"] in m for m in missing)
        )
        ok = expected_match and missing_match
        if ok:
            pass_count += 1
        print(f"{s['label']:<22s} {str(grounded):>10s} {str(missing[:1]):>20s} "
              f"{'✅' if ok else '❌':>4s}")
    print(f"\n합성 검증: {pass_count}/{len(SYNTHETIC_FABRICATIONS)} 통과")
    return pass_count == len(SYNTHETIC_FABRICATIONS)


# ── 메인 ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--backend-log", default="/tmp/backend_audit.log")
    parser.add_argument("--output", default="reports/llm_audit/grounding_dryrun.json")
    args = parser.parse_args()

    log_path = Path(args.backend_log)

    # 0. 백엔드 헬스체크
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{args.base_url}/api/health")
            r.raise_for_status()
            print(f"[health] {r.json()}")
    except Exception as e:
        print(f"[ERROR] 백엔드 헬스체크 실패: {e}", file=sys.stderr)
        sys.exit(1)

    # 1. 합성 시나리오 (오프라인 검증)
    synthetic_pass = _verify_synthetic()

    # 2. 실 direct 쿼리 호출
    print(f"\n── 실 direct 쿼리 호출 ({len(REAL_DIRECT_QUERIES)}건) ──")
    log_pos_start = _log_file_size(log_path)
    err = 0
    for i, q in enumerate(REAL_DIRECT_QUERIES, 1):
        print(f"  [{i:02d}/{len(REAL_DIRECT_QUERIES)}] {q[:50]}")
        err += _call_backend(args.base_url, q)
    print(f"\nSSE 호출 완료. 에러={err}")

    # 3. backend log에서 GROUNDING_CHECK 추출
    time.sleep(0.5)  # log flush 대기
    records = _read_grounding_lines(log_path, since_pos=log_pos_start)
    print(f"\n── backend log 추출 GROUNDING_CHECK ({len(records)}건) ──")

    # 4. 통계
    grounded_count = sum(1 for r in records if r.get("grounded"))
    n = len(records)
    grounded_pct = (grounded_count / n * 100) if n > 0 else 0.0

    # missing fact 분포
    missing_counter: Counter = Counter()
    for r in records:
        for m in r.get("missing", []):
            label = m.split(":", 1)[0]
            missing_counter[label] += 1

    # intent 분포
    intent_counter = Counter(r.get("intent") for r in records)

    # reject 샘플 (grounded=false)
    reject_samples = [r for r in records if not r.get("grounded")][:5]

    print(f"\n총 측정: {n}건")
    print(f"grounded: {grounded_count}/{n} ({grounded_pct:.1f}%)")
    print(f"reject:   {n - grounded_count}/{n} ({100 - grounded_pct:.1f}%)")
    print(f"\nintent 분포:")
    for intent, count in intent_counter.most_common():
        print(f"  {intent:<25s} {count}")
    if missing_counter:
        print(f"\nmissing fact 분포:")
        for label, count in missing_counter.most_common():
            print(f"  {label:<15s} {count}")
    if reject_samples:
        print(f"\nreject 샘플 (최대 5):")
        for r in reject_samples:
            print(f"  query: {r.get('query','')}")
            print(f"    missing: {r.get('missing')}")
            print(f"    direct_preview: {r.get('direct_preview','')[:80]}")

    # 5. 해석
    print("\n" + "=" * 72)
    print("해석 (95/70 기준)")
    print("=" * 72)
    if grounded_pct >= 95.0:
        verdict = (
            "⚠️  grounded ≥ 95% — 게이트 너무 느슨. substring 매칭 한계 명백.\n"
            "    → 근접-윈도우 또는 cohort-aware 보강 검토. enforce 켜도 효과 미미."
        )
    elif grounded_pct <= 70.0:
        verdict = (
            "⚠️  grounded ≤ 70% — 차단율 30%+. direct→stream 전환 폭증 우려.\n"
            "    → generate 부하 (세마포어 2 + 60s timeout) 위험. enforce 보류, 패턴 완화."
        )
    else:
        verdict = (
            f"✅ grounded {grounded_pct:.1f}% — 정상 범위 (70~95%). enforce 켤 만함.\n"
            "    → reject 샘플이 명백한 환각이면 enforce 진행. 모호하면 추가 검토."
        )
    print(verdict)
    print(f"\n합성 시나리오: {'✅ 통과' if synthetic_pass else '❌ 실패'} (게이트 차단 효과)")

    # 6. JSON 저장
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "queries_attempted": len(REAL_DIRECT_QUERIES),
        "grounding_check_records": n,
        "grounded_count": grounded_count,
        "grounded_pct": round(grounded_pct, 2),
        "intent_distribution": dict(intent_counter),
        "missing_fact_distribution": dict(missing_counter),
        "reject_samples": reject_samples,
        "synthetic_pass": synthetic_pass,
        "verdict": verdict.split("\n")[0],
        "interpretation_thresholds": {
            "loose_gate": "grounded >= 95%",
            "tight_gate": "grounded <= 70%",
            "normal_range": "70% < grounded < 95%",
        },
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
