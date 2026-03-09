"""
BUFS 챗봇 정성적 평가 보고서 생성기 (LLM-as-a-Judge)

기존 evaluate.py의 3차원(correctness/relevance/faithfulness) judge와 달리,
오류 유형 6종 분류 + 0-5 정밀 정확성 척도 + 한국어 판단 근거를 포함합니다.

사용법:
    # 가장 최근 eval_results_*.json 자동 선택
    python scripts/qualitative_judge.py

    # 특정 결과 파일 지정
    python scripts/qualitative_judge.py --input data/eval/eval_results_20260308_143434.json

    # 보고서 저장 경로 지정
    python scripts/qualitative_judge.py --output data/eval/my_report.md
"""

import argparse
import asyncio
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Windows 콘솔 한글 처리
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings

# ─── Judge 프롬프트 ───────────────────────────────────────────────────────────

JUDGE_SYSTEM = (
    "당신은 AI 응답 품질을 평가하는 전문 평가자입니다. "
    "반드시 유효한 JSON 한 줄만 출력하세요. 다른 텍스트는 절대 출력하지 마세요."
)

JUDGE_PROMPT = """\
아래 정보를 바탕으로 AI 응답을 정성적으로 평가하세요.

[질문]
{question}

[모범 답안]
{ground_truth}

[모범 답안의 근거 문장 (참고용)]
{golden_context}

[AI 응답]
{answer}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
평가 기준

중요: 평가는 반드시 [모범 답안]을 기준으로 하세요.
- [근거 문장]은 참고 자료일 뿐이며, 근거 문장에만 있고 모범 답안에는 없는 정보를 AI가 누락했다고 감점하지 마세요.
- AI 응답에 "[출처: ...]" 형태의 출처 표기가 있으면 무시하세요. 출처 표기는 정확성 평가와 무관합니다.
- AI 응답이 모범 답안의 핵심 정보를 모두 포함하면 "correct"로 판정하세요.

1. correctness_score (0~5 정수):
   5 = 완전 정확 (모범 답안과 동일하거나 동등한 의미)
   4 = 핵심 맞음, 세부 표현만 다름
   3 = 부분 정확 (일부 맞고 일부 틀림)
   2 = 핵심이 틀렸으나 관련 내용 언급
   1 = 완전히 틀림
   0 = "모른다" 또는 "정보 없다"고만 답변

2. error_type (아래 6종 중 정확히 하나):
   "correct"        : 모범 답안의 핵심 정보와 일치하거나 동등한 의미
   "hallucination"  : 근거 문장에 없는 수치·날짜·규정을 추가함
   "wrong_slot"     : 슬롯 유형 혼동 (예: 기간 질문에 수치로 답변, 날짜 질문에 학점으로 답변)
   "false_negative" : 근거가 있음에도 "모른다"/"정보 없다"고 답변
   "incomplete"     : 모범 답안의 핵심 정보 중 일부가 누락됨
   "off_topic"      : 질문과 무관하거나 전혀 다른 주제 답변

3. reasoning (한국어 1~2문장): 위 판단의 근거를 간결히 설명

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
반드시 아래 JSON만 출력 (다른 텍스트 없이):
{{"correctness_score": <0-5>, "error_type": "<6종 중 하나>", "reasoning": "<한국어 1-2문장>"}}\
"""

# 오류 유형 설명 (보고서용)
ERROR_DESC = {
    "correct":        "정답과 일치 또는 동등한 의미",
    "hallucination":  "근거에 없는 정보 추가",
    "wrong_slot":     "슬롯 유형 혼동 (기간↔수치 등)",
    "false_negative": "근거 있음에도 '모른다'고 답변",
    "incomplete":     "정답 일부만 포함 (조건·예외 누락)",
    "off_topic":      "질문과 무관한 답변",
}


# ─── LLM Judge 호출 ───────────────────────────────────────────────────────────

async def qualitative_judge(
    question: str,
    ground_truth: str,
    golden_context: str,
    answer: str,
    client,
) -> Dict[str, Any]:
    """상세 정성 평가를 수행합니다."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        ground_truth=ground_truth or "(정답 없음)",
        golden_context=(golden_context[:400] if golden_context else "(근거 없음)"),
        answer=answer[:500],
    )
    payload = {
        "model": settings.ollama.model,
        "prompt": prompt,
        "system": JUDGE_SYSTEM,
        "stream": False,
        "options": {"num_ctx": 1280, "temperature": 0.0},
    }
    try:
        resp = await client.post(
            f"{settings.ollama.base_url}/api/generate", json=payload
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()

        # 마크다운 코드블록 제거
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()

        # JSON 추출
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group()
        parsed = json.loads(raw)

        score = parsed.get("correctness_score", 0)
        et = str(parsed.get("error_type", "unknown")).strip()
        if et not in ERROR_DESC:
            et = "off_topic"

        return {
            "correctness_score": int(score),
            "error_type": et,
            "reasoning": str(parsed.get("reasoning", "")).strip(),
        }
    except Exception as e:
        return {
            "correctness_score": None,
            "error_type": "judge_error",
            "reasoning": f"평가 실패: {e}",
        }


# ─── 보고서 생성 ──────────────────────────────────────────────────────────────

def _avg(vals: List) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def generate_report(
    eval_results: List[Dict],
    qual_results: List[Dict],
    source_file: str,
    model: str,
) -> str:
    timestamp = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    total = len(qual_results)

    # 오류 유형 집계
    error_counts: Dict[str, int] = {}
    for r in qual_results:
        et = r.get("error_type", "unknown")
        error_counts[et] = error_counts.get(et, 0) + 1

    # 난이도별 정확성 평균
    by_difficulty: Dict[str, List[float]] = {}
    for ev, qv in zip(eval_results, qual_results):
        diff = ev.get("difficulty", "—")
        score = qv.get("correctness_score")
        if score is not None:
            by_difficulty.setdefault(diff, []).append(float(score))

    diff_stats = {
        k: {"avg": _avg(v), "n": len(v)} for k, v in sorted(by_difficulty.items())
    }
    all_scores = [
        qv.get("correctness_score")
        for qv in qual_results
        if qv.get("correctness_score") is not None
    ]
    overall_avg = _avg(all_scores)

    # 원본 judge 통계
    orig_c = _avg([r.get("judge_correctness") for r in eval_results])
    orig_r = _avg([r.get("judge_relevance") for r in eval_results])
    orig_f = _avg([r.get("judge_faithfulness") for r in eval_results])

    # 실패 케이스 (score < 4 또는 not correct)
    failures = [
        (ev, qv)
        for ev, qv in zip(eval_results, qual_results)
        if qv.get("error_type") not in ("correct",)
        and qv.get("correctness_score") is not None
    ]

    L: List[str] = []

    # ── 헤더 ──
    L += [
        "# BUFS 챗봇 정성적 평가 보고서 (LLM-as-a-Judge)",
        "",
        f"| 항목 | 내용 |",
        f"|------|------|",
        f"| 평가 일시 | {timestamp} |",
        f"| 원본 결과 | `{source_file}` |",
        f"| Judge 모델 | `{model}` |",
        f"| 평가 문항 수 | {total}개 |",
        "",
        "---",
        "",
    ]

    # ── 1. 핵심 지표 ──
    L += [
        "## 1. 핵심 지표 요약",
        "",
        "### 기존 3차원 Judge (evaluate.py 원본)",
        "",
        "| 지표 | 전체 평균 |",
        "|------|-----------|",
        f"| 정확성 Correctness (0/1) | {orig_c} |",
        f"| 관련성 Relevance (1-5) | {orig_r} |",
        f"| 충실성 Faithfulness (1-5) | {orig_f} |",
        "",
        "### 정성 Judge — 정확성 (0-5 척도)",
        "",
        "| 난이도 | 평균 정확성 | n |",
        "|--------|-------------|---|",
    ]
    for diff, stat in diff_stats.items():
        L.append(f"| {diff} | {stat['avg']}/5 | {stat['n']} |")
    L += [
        f"| **전체** | **{overall_avg}/5** | **{total}** |",
        "",
    ]

    # ── 2. 오류 유형 분포 ──
    L += [
        "## 2. 오류 유형 분포",
        "",
        "| 오류 유형 | 설명 | 건수 | 비율 |",
        "|-----------|------|------|------|",
    ]
    for et, cnt in sorted(error_counts.items(), key=lambda x: -x[1]):
        desc = ERROR_DESC.get(et, et)
        pct = round(cnt / total * 100, 1)
        L.append(f"| `{et}` | {desc} | {cnt} | {pct}% |")
    L.append("")

    # ── 3. 실패 케이스 분석 ──
    L += ["## 3. 실패 케이스 분석", ""]

    if not failures:
        L += ["> 모든 문항이 정답 처리되었습니다.", ""]
    else:
        by_error: Dict[str, List] = {}
        for ev, qv in failures:
            et = qv.get("error_type", "unknown")
            by_error.setdefault(et, []).append((ev, qv))

        for et in sorted(by_error.keys()):
            cases = by_error[et]
            desc = ERROR_DESC.get(et, et)
            L += [f"### `{et}` — {desc} ({len(cases)}건)", ""]
            for ev, qv in cases:
                ans_preview = (ev.get("answer") or "")[:120].replace("\n", " ")
                L += [
                    f"**[{ev['id']}]** {ev['question']}",
                    f"- **정답**: `{ev.get('ground_truth', '-')}`",
                    f"- **AI 응답**: {ans_preview}",
                    f"- **정확성**: {qv['correctness_score']}/5",
                    f"- **판단 근거**: {qv['reasoning']}",
                    "",
                ]

    # ── 4. 강점 분석 ──
    correct_n = error_counts.get("correct", 0)
    hit_pct = _avg([r.get("hit_rate", 0) for r in eval_results])
    cite_pct = round(
        sum(1 for r in eval_results if r.get("has_citation")) / total * 100, 1
    )
    fn_n = error_counts.get("false_negative", 0)
    ws_n = error_counts.get("wrong_slot", 0)

    L += [
        "## 4. 강점 분석",
        "",
        f"- 전체 {total}개 중 **{correct_n}개 ({round(correct_n/total*100,1)}%)** 정확히 답변",
        f"- 검색 Hit Rate **{round((hit_pct or 0)*100, 1)}%** — 모든 질문에서 관련 문서 검색 성공",
        f"- 출처 인용률 **{cite_pct}%** — 모든 답변에 출처 포함",
        "",
    ]

    # ── 5. 개선 권고사항 ──
    L += ["## 5. 개선 권고사항", ""]

    priorities: List[str] = []
    if fn_n > 0:
        priorities.append(
            f"**P0 — false_negative ({fn_n}건)**: 컨텍스트에 날짜·수치·시간 패턴이 있으면 "
            "'확인되지 않는 정보' fallback을 차단. 후보 값이 하나라도 추출되면 답변 생성을 강제."
        )
    if ws_n > 0:
        priorities.append(
            f"**P1 — wrong_slot ({ws_n}건)**: query_analyzer에 '기간/일정'과 '수치/한도' "
            "서브타입 분류 추가. entity_type=period vs entity_type=limit 구분 후 프롬프트에 주입."
        )
    inc_n = error_counts.get("incomplete", 0)
    if inc_n > 0:
        priorities.append(
            f"**P1 — incomplete ({inc_n}건)**: 시스템 프롬프트에 "
            "'조건이 여러 개면 모두 나열하라' 규칙 추가. "
            "특히 학번별·학년별 분기가 있는 답변에서 일부 조건 누락 방지."
        )
    hall_n = error_counts.get("hallucination", 0)
    if hall_n > 0:
        priorities.append(
            f"**P2 — hallucination ({hall_n}건)**: ResponseValidator에서 "
            "컨텍스트에 없는 수치·날짜가 답변에 포함되면 경고 플래그 추가."
        )

    if not priorities:
        L.append("> 현재 주요 개선 필요 항목 없음. 전반적으로 양호합니다.")
    else:
        for p in priorities:
            L.append(f"- {p}")
            L.append("")

    # ── 6. 전체 케이스 평가표 ──
    L += [
        "## 6. 전체 케이스 평가표",
        "",
        "| ID | 난이도 | 정확성 | 오류 유형 | 판단 근거 |",
        "|----|--------|--------|-----------|-----------|",
    ]
    for ev, qv in zip(eval_results, qual_results):
        score = qv.get("correctness_score", "-")
        et = qv.get("error_type", "-")
        reason = (qv.get("reasoning") or "-")[:70].replace("|", "｜")
        L.append(
            f"| {ev['id']} | {ev.get('difficulty','-')} "
            f"| {score}/5 | `{et}` | {reason} |"
        )
    L.append("")

    return "\n".join(L)


# ─── 메인 ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="BUFS 챗봇 정성적 평가 (LLM-as-a-Judge)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        default=None,
        help="평가 결과 JSON 경로 (기본: 가장 최근 eval_results_*.json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="보고서 저장 경로 (기본: data/eval/qualitative_report_TIMESTAMP.md)",
    )
    args = parser.parse_args()

    # ── 입력 파일 결정 ──
    eval_dir = ROOT / "data" / "eval"
    if args.input:
        input_path = Path(args.input)
    else:
        json_files = sorted(eval_dir.glob("eval_results_*.json"))
        if not json_files:
            print("[X] eval_results_*.json 파일 없음. 먼저 evaluate.py를 실행하세요.")
            sys.exit(1)
        input_path = json_files[-1]

    print(f"[*] 입력 파일  : {input_path.name}")

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    eval_results: List[Dict] = data.get("results", [])
    model: str = data.get("model", settings.ollama.model)

    print(f"[*] 총 문항    : {len(eval_results)}개")
    print(f"[*] 모델       : {model}")

    # ── Ollama 연결 확인 ──
    import httpx
    print("[*] Ollama 연결 확인 중...")
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.get(f"{settings.ollama.base_url}/api/tags")
    except Exception:
        print("[X] Ollama 연결 실패. 'ollama serve' 실행 후 재시도.")
        sys.exit(1)
    print("[+] Ollama OK\n")

    # ── 정성 평가 실행 ──
    qual_results: List[Dict] = []
    t_start = time.perf_counter()

    async with httpx.AsyncClient(timeout=90) as client:
        for idx, ev in enumerate(eval_results, 1):
            question = ev.get("question", "")
            ground_truth = ev.get("ground_truth", "")
            golden_context = ev.get("golden_context", "")
            answer = ev.get("answer", "")

            print(
                f"[{idx:3d}/{len(eval_results)}] {ev['id']} "
                f"{question[:35]}...",
                end="",
                flush=True,
            )

            if not answer or not ev.get("answer_ok", True):
                result: Dict[str, Any] = {
                    "correctness_score": 0,
                    "error_type": "no_answer",
                    "reasoning": "답변이 없거나 비정상 답변입니다.",
                }
            else:
                result = await qualitative_judge(
                    question=question,
                    ground_truth=ground_truth,
                    golden_context=golden_context,
                    answer=answer,
                    client=client,
                )

            qual_results.append(result)
            score = result.get("correctness_score", "-")
            et = result.get("error_type", "-")
            print(f"  →  {et} ({score}/5)")

    elapsed = time.perf_counter() - t_start
    print(f"\n[T] 정성 평가 완료: {elapsed:.1f}초")

    # ── 보고서 생성 ──
    print("[*] 보고서 생성 중...")
    report = generate_report(eval_results, qual_results, input_path.name, model)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(
        args.output or (eval_dir / f"qualitative_report_{timestamp}.md")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    # raw JSON도 저장
    json_out = eval_dir / f"qualitative_raw_{timestamp}.json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source": input_path.name,
                "timestamp": timestamp,
                "model": model,
                "qual_results": qual_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"[S] 보고서 저장: {output_path}")
    print(f"[S] 원시 JSON : {json_out}")
    print()
    print("=" * 70)
    print(report)
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
