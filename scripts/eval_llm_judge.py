"""
BUFS 챗봇 정성적 평가 — LLM-as-a-Judge
실행: .venv/Scripts/python -X utf8 scripts/eval_llm_judge.py

평가 방법:
  1. 12개 테스트 질문을 전체 파이프라인(검색 → LLM 답변)으로 실행
  2. 각 질문에 대해 5개 차원을 1-5점으로 채점 (Claude 기준)
  3. 결과를 JSON으로 저장 → reports/eval_result.json
"""

import sys, io, json, asyncio, time, logging
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.CRITICAL)

# ── 평가 테스트셋 정의 ──────────────────────────────────────────────────────
# 카테고리: GRADUATION_REQ / REGISTRATION / SCHEDULE / COURSE_INFO /
#           NOTICE_ATT(첨부파일) / HALLUCINATION(없는정보)
TEST_CASES = [
    # ── GRADUATION_REQ ──────────────────────────────────────────
    {
        "id": "GR-01",
        "category": "졸업요건",
        "question": "2024학번 학생의 졸업학점은 몇 학점이야?",
        "student_id": "2024",
        "ground_truth": "130학점 이상 + 교양(A) + 전공(B) + 글로벌소통역량과정 이수",
        "expected_keywords": ["130학점", "교양", "전공", "글로벌소통역량"],
    },
    {
        "id": "GR-02",
        "category": "졸업요건",
        "question": "복수전공을 하려면 몇 학점을 이수해야 해?",
        "student_id": "2023",
        "ground_truth": "복수전공 30학점 이상 이수",
        "expected_keywords": ["30학점", "복수전공"],
    },
    {
        "id": "GR-03",
        "category": "졸업요건",
        "question": "마이크로전공 이수학점은 몇 학점이야?",
        "student_id": "2022",
        "ground_truth": "마이크로전공 9학점 이상 이수",
        "expected_keywords": ["9학점", "마이크로전공"],
    },
    # ── REGISTRATION ─────────────────────────────────────────────
    {
        "id": "RE-01",
        "category": "수강신청",
        "question": "2026학년도 1학기 수강신청 기간이 언제야?",
        "student_id": None,
        "ground_truth": "1월 28일(수) 10:00 ~ 2월 1일(일) 16:00",
        "expected_keywords": ["1월 28일", "2월 1일", "수강신청"],
    },
    {
        "id": "RE-02",
        "category": "수강신청",
        "question": "OCU 교과목 수강신청 방법을 알려줘",
        "student_id": None,
        "ground_truth": "OCU 홈페이지(cons.ocu.ac.kr)에서 신청, 수강료 별도 납부",
        "expected_keywords": ["OCU", "cons.ocu.ac.kr", "수강"],
    },
    # ── SCHEDULE ──────────────────────────────────────────────────
    {
        "id": "SC-01",
        "category": "학사일정",
        "question": "졸업시험(논문제출) 일정이 어떻게 돼?",
        "student_id": None,
        "ground_truth": "2026학년도 1학기 졸업시험(논문제출) 실시 — 공지 참조",
        "expected_keywords": ["졸업시험", "2026"],
    },
    # ── COURSE_INFO ───────────────────────────────────────────────
    {
        "id": "CI-01",
        "category": "과목정보",
        "question": "성적 포기제도가 뭐야? 어떻게 신청해?",
        "student_id": None,
        "ground_truth": "부분적 성적포기제도: 학생포털 로그인 → 성적 → 성적선택제 신청, 포기한 성적은 복구 불가",
        "expected_keywords": ["성적포기", "학생포털", "복구 불가"],
    },
    {
        "id": "CI-02",
        "category": "과목정보",
        "question": "OCU 시험은 어떻게 봐?",
        "student_id": None,
        "ground_truth": "CS방식 온라인 시험 — OCU 컨소시엄 홈페이지 시험/퀴즈 메뉴",
        "expected_keywords": ["OCU", "온라인", "시험"],
    },
    # ── NOTICE_ATTACHMENT (첨부파일 청크 활용) ─────────────────────
    {
        "id": "NA-01",
        "category": "공지첨부-XLSX",
        "question": "2026-1학기에 폐강된 교과목 목록을 알려줘",
        "student_id": None,
        "ground_truth": "2026-1 최종 폐강 교과목260311.xlsx 참조 — 31개 교과목 폐강",
        "expected_keywords": ["폐강"],
    },
    {
        "id": "NA-02",
        "category": "공지첨부-PDF",
        "question": "학부 사무실 전화번호 어디서 확인해?",
        "student_id": None,
        "ground_truth": "051-509-XXXX 형식, 학부과 사무실 전화번호.pdf 참조",
        "expected_keywords": ["051-509", "전화번호"],
    },
    {
        "id": "NA-03",
        "category": "공지첨부-HWP",
        "question": "군 복무 중 OCU 수강으로 학점인정 받으려면 어떻게 해?",
        "student_id": None,
        "ground_truth": "복학 후 학점인정신청서를 학사지원팀에 제출, 학기당 6학점/연간 12학점 이내",
        "expected_keywords": ["학점인정", "학사지원팀", "6학점"],
    },
    # ── HALLUCINATION TEST ────────────────────────────────────────
    {
        "id": "HA-01",
        "category": "환각테스트",
        "question": "2026학년도 2학기 등록금 납부 기간은 언제야?",
        "student_id": None,
        "ground_truth": "DB에 없는 정보 → '확인되지 않는 정보입니다' 또는 유사 거절 응답",
        "expected_keywords": ["확인되지 않", "정보가 없"],
    },
]

# ── 파이프라인 임포트 ──────────────────────────────────────────────────────
from app.vectordb.chroma_store import ChromaStore
from app.pipeline.query_analyzer import QueryAnalyzer
from app.pipeline.query_router import QueryRouter
from app.pipeline.context_merger import ContextMerger
from app.pipeline.answer_generator import AnswerGenerator
from app.graphdb.academic_graph import AcademicGraph

store    = ChromaStore()
analyzer = QueryAnalyzer()
graph    = AcademicGraph()
router   = QueryRouter(store, graph)
merger   = ContextMerger()
generator= AnswerGenerator()

async def run_pipeline(tc: dict) -> dict:
    """한 테스트케이스를 전체 파이프라인으로 실행합니다."""
    q   = tc["question"]
    t0  = time.perf_counter()

    # 1) 쿼리 분석 (student_id는 질문 텍스트에서 자동 추출)
    analysis = analyzer.analyze(q)

    # 2) 라우팅 + 검색
    search_results = router.route_and_search(q, analysis)

    # 3) 컨텍스트 병합
    merged = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
    )

    # 4) 컨텍스트 없으면 조기 종료
    if not merged.formatted_context.strip():
        return {
            "id": tc["id"], "category": tc["category"], "question": q,
            "student_id": analysis.student_id, "intent": analysis.intent.value,
            "n_retrieved": len(search_results["vector_results"]),
            "context_len": 0, "context_preview": "",
            "answer": "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.",
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }

    # 5) 그래프 direct_answer 있으면 즉시 반환
    if merged.direct_answer:
        return {
            "id": tc["id"], "category": tc["category"], "question": q,
            "student_id": analysis.student_id, "intent": analysis.intent.value,
            "n_retrieved": len(search_results["vector_results"]),
            "context_len": len(merged.formatted_context),
            "context_preview": merged.formatted_context[:200],
            "answer": merged.direct_answer,
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }

    # 6) LLM 답변 생성
    answer = await generator.generate_full(
        question=q,
        context=merged.formatted_context,
        student_id=analysis.student_id,
        question_focus=analysis.entities.get("question_focus"),
    )

    elapsed = time.perf_counter() - t0

    return {
        "id":              tc["id"],
        "category":        tc["category"],
        "question":        q,
        "student_id":      analysis.student_id,
        "intent":          analysis.intent.value,
        "n_retrieved":     len(search_results["vector_results"]),
        "context_len":     len(merged.formatted_context),
        "context_preview": merged.formatted_context[:300],
        "answer":          answer,
        "elapsed_s":       round(elapsed, 2),
    }


async def run_all():
    results = []
    for i, tc in enumerate(TEST_CASES, 1):
        print(f"\n[{i:02d}/{len(TEST_CASES)}] {tc['id']} — {tc['category']}")
        print(f"  Q: {tc['question']}")
        try:
            r = await run_pipeline(tc)
            print(f"  Intent: {r['intent']}  검색:{r['n_retrieved']}건  컨텍스트:{r['context_len']}자  {r['elapsed_s']}s")
            print(f"  A: {r['answer'][:120].replace(chr(10),' ')}{'...' if len(r['answer'])>120 else ''}")
            results.append({"tc": tc, "result": r, "error": None})
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"tc": tc, "result": None, "error": str(e)})
    return results


if __name__ == "__main__":
    print("=" * 65)
    print("BUFS 챗봇 LLM-as-a-Judge 평가 실행")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"테스트 케이스: {len(TEST_CASES)}개")
    print("=" * 65)

    results = asyncio.run(run_all())

    # 결과 저장
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "eval_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n결과 저장: {out_path}")
    ok  = sum(1 for r in results if r["error"] is None)
    err = sum(1 for r in results if r["error"] is not None)
    print(f"성공: {ok}개  오류: {err}개")
