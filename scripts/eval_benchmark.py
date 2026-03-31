"""
챗봇 벤치마크 평가 스크립트 - 50문항
LLM 실제 실행 + 정답 핵심 수치/날짜 기반 채점
리랭커 비활성화 (VRAM 부족 방지)
"""

import sys
import os
import json
import re
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RERANKER_ENABLED"] = "false"

from app.pipeline.query_analyzer import QueryAnalyzer
from app.pipeline.query_router import QueryRouter
from app.pipeline.context_merger import ContextMerger
from app.pipeline.answer_generator import AnswerGenerator
from app.graphdb import AcademicGraph
from app.vectordb import ChromaStore
from app.embedding import Embedder

# 50문항 (쉬움 + 중간 + 어려움)
# judge_keys: 정답에 반드시 포함돼야 할 핵심 수치/단어 목록
QUESTIONS = [
    # 쉬움
    {
        "id": "q001", "difficulty": "easy",
        "question": "2026학년도 1학기 개강일은 언제인가?",
        "answer": "2026년 3월 2일이다.",
        "judge_keys": ["3월 2일", "3월2일", "03-02", "3-02"],
    },
    {
        "id": "q002", "difficulty": "easy",
        "question": "2026학년도 1학기 수업시작일은 언제인가?",
        "answer": "2026년 3월 3일이다.",
        "judge_keys": ["3월 3일", "3월3일", "03-03"],
    },
    {
        "id": "q003", "difficulty": "easy",
        "question": "수강신청 확인기간(수강정정기간)은 언제인가?",
        "answer": "2026년 3월 4일부터 3월 6일까지이다.",
        "judge_keys": ["3월 4일", "3월4일"],
    },
    {
        "id": "q004", "difficulty": "easy",
        "question": "2026학년도 1학기 중간고사 기간은 언제인가?",
        "answer": "4월 20일부터 4월 24일까지이다.",
        "judge_keys": ["4월 20일", "4월20일"],
    },
    {
        "id": "q005", "difficulty": "easy",
        "question": "2026학년도 1학기 기말고사 기간은 언제인가?",
        "answer": "6월 8일부터 6월 12일까지이다.",
        "judge_keys": ["6월 8일", "6월8일"],
    },
    {
        "id": "q006", "difficulty": "easy",
        "question": "2026학년도 1학기 하계방학 시작일은 언제인가?",
        "answer": "2026년 6월 15일이다.",
        "judge_keys": ["6월 15일", "6월15일"],
    },
    {
        "id": "q007", "difficulty": "easy",
        "question": "2025학년도 후기 학위수여식은 언제인가?",
        "answer": "2026년 8월 14일이다.",
        "judge_keys": ["8월 14일", "8월14일"],
    },
    {
        "id": "q008", "difficulty": "easy",
        "question": "수강신청 장바구니 신청 기간은 언제인가?",
        "answer": "2026년 1월 28일부터 2월 1일까지이다.",
        "judge_keys": ["1월 28일", "1월28일"],
    },
    {
        "id": "q009", "difficulty": "easy",
        "question": "수강신청 기간은 언제인가?",
        "answer": "2026년 2월 9일부터 2월 12일까지이다.",
        "judge_keys": ["2월 9일", "2월9일"],
    },
    {
        "id": "q010", "difficulty": "easy",
        "question": "수강신청 시작 전에 로그인 가능한 시간은 언제인가?",
        "answer": "수강신청 시작 15분 전인 9시 45분부터 가능하다.",
        "judge_keys": ["9시 45분", "9:45", "09:45"],
    },
    {
        "id": "q011", "difficulty": "easy",
        "question": "2026학년도 1학기 수강신청 취소는 언제까지 가능한가?",
        "answer": "2026년 3월 26일 17시까지이다.",
        "judge_keys": ["3월 26일", "3월26일"],
    },
    {
        "id": "q012", "difficulty": "easy",
        "question": "수업일수 1/2선은 언제인가?",
        "answer": "2026년 4월 22일이다.",
        "judge_keys": ["4월 22일", "4월22일"],
    },
    {
        "id": "q013", "difficulty": "easy",
        "question": "수업일수 3/4선은 언제인가?",
        "answer": "2026년 5월 19일이다.",
        "judge_keys": ["5월 19일", "5월19일"],
    },
    {
        "id": "q014", "difficulty": "easy",
        "question": "중간 수업평가 기간은 언제인가?",
        "answer": "2026년 4월 20일부터 5월 1일까지이다.",
        "judge_keys": ["4월 20일", "4월20일"],
    },
    {
        "id": "q015", "difficulty": "easy",
        "question": "기말 수업평가 기간은 언제인가?",
        "answer": "2026년 6월 8일부터 6월 19일까지이다.",
        "judge_keys": ["6월 8일", "6월8일"],
    },
    {
        "id": "q016", "difficulty": "easy",
        "question": "2023학번 이후 최대 수강신청 학점은 얼마인가?",
        "answer": "18학점이다.",
        "judge_keys": ["18학점"],
    },
    {
        "id": "q017", "difficulty": "easy",
        "question": "2022학번 이전 최대 수강신청 학점은 얼마인가?",
        "answer": "19학점이다.",
        "judge_keys": ["19학점"],
    },
    {
        "id": "q026", "difficulty": "easy",
        "question": "재수강 가능한 성적 기준은 무엇인가?",
        "answer": "C+ 이하의 과목만 가능하다.",
        "judge_keys": ["C+"],
    },
    {
        "id": "q027", "difficulty": "easy",
        "question": "재수강 후 받을 수 있는 최고 성적은 무엇인가?",
        "answer": "A이다.",
        "judge_keys": ["A"],
    },
    {
        "id": "q029", "difficulty": "easy",
        "question": "휴학생이 수강신청을 하기 위해 필요한 절차는 무엇인가?",
        "answer": "사전 복학이 필요하다. 휴학생은 수강신청이 불가하다.",
        "judge_keys": ["복학"],
    },
    {
        "id": "q030", "difficulty": "easy",
        "question": "2026학년도 1학기 온라인 휴복학 신청 기간은 언제인가?",
        "answer": "2026년 7월 6일부터 8월 30일까지이다.",
        "judge_keys": ["7월 6일", "7월6일"],
    },
    {
        "id": "q033", "difficulty": "easy",
        "question": "OCU 교과목 개강일과 수강 가능 시간은 언제인가?",
        "answer": "2026년 3월 2일 오전 10시부터 수강 가능하다.",
        "judge_keys": ["3월 2일", "3월2일"],
    },
    {
        "id": "q034", "difficulty": "easy",
        "question": "OCU 수강신청 기간은 언제인가?",
        "answer": "2026년 2월 9일부터 2월 12일까지이다.",
        "judge_keys": ["2월 9일", "2월9일"],
    },
    {
        "id": "q035", "difficulty": "easy",
        "question": "OCU 정규학기에 수강할 수 있는 최대 학점은 얼마인가?",
        "answer": "최대 6학점(2과목)이다.",
        "judge_keys": ["6학점"],
    },
    {
        "id": "q036", "difficulty": "easy",
        "question": "OCU를 졸업까지 인정받을 수 있는 최대 학점은 얼마인가?",
        "answer": "최대 24학점(8과목)이다.",
        "judge_keys": ["24학점"],
    },
    {
        "id": "q037", "difficulty": "easy",
        "question": "OCU 시스템 사용료는 과목당 얼마인가?",
        "answer": "24,000원이다.",
        "judge_keys": ["24,000", "24000"],
    },
    {
        "id": "q039", "difficulty": "easy",
        "question": "OCU 교과목 수강 시 ID는 어떻게 생성되는가?",
        "answer": "bufs(소문자)와 학번을 조합하여 생성된다.",
        "judge_keys": ["bufs"],
    },
    {
        "id": "q041", "difficulty": "easy",
        "question": "2024학번 이후 졸업에 필요한 최소 학점은 얼마인가?",
        "answer": "120학점 이상이다.",
        "judge_keys": ["120학점"],
    },
    {
        "id": "q047", "difficulty": "easy",
        "question": "야간수업 10교시 시작 시간은 언제인가?",
        "answer": "18시이다.",
        "judge_keys": ["18시", "18:00"],
    },
    {
        "id": "q048", "difficulty": "easy",
        "question": "야간수업 14교시 종료 시간은 언제인가?",
        "answer": "22시 05분이다.",
        "judge_keys": ["22시 05분", "22:05"],
    },
    # 중간
    {
        "id": "q018", "difficulty": "medium",
        "question": "2023학번 이후 학생이 직전학기 평점 4.0 이상이면 최대 몇 학점까지 신청할 수 있는가?",
        "answer": "21학점까지 가능하다.",
        "judge_keys": ["21학점"],
    },
    {
        "id": "q019", "difficulty": "medium",
        "question": "2022학번 이전 학생이 직전학기 평점 4.0 이상이면 최대 몇 학점까지 신청할 수 있는가?",
        "answer": "22학점까지 가능하다.",
        "judge_keys": ["22학점"],
    },
    {
        "id": "q020", "difficulty": "medium",
        "question": "장바구니에 담을 수 있는 최대 학점은 얼마인가?",
        "answer": "30학점이다.",
        "judge_keys": ["30학점"],
    },
    {
        "id": "q021", "difficulty": "medium",
        "question": "장학금 지급을 받기 위한 최소 이수 학점은 얼마인가?",
        "answer": "12학점이다. 단, 4학년은 9학점이다.",
        "judge_keys": ["12학점", "9학점"],
    },
    {
        "id": "q022", "difficulty": "medium",
        "question": "학점이월제는 어떤 학번에 적용되며, 이월 가능한 최대 학점은 얼마인가?",
        "answer": "2022학번 이전 학번에만 적용되며, 최대 3학점까지 이월 가능하다.",
        "judge_keys": ["2022학번", "3학점"],
    },
    {
        "id": "q023", "difficulty": "medium",
        "question": "OCU 수강 신청자에게 허용되는 초과 학점 예외는 무엇인가?",
        "answer": "최대 신청학점에서 3학점 초과 신청이 가능하다.",
        "judge_keys": ["3학점"],
    },
    {
        "id": "q024", "difficulty": "medium",
        "question": "사회봉사 및 서비스러닝 교과목을 수강하면 최대 신청학점보다 몇 학점 더 신청할 수 있는가?",
        "answer": "추가로 1학점까지 신청 가능하다.",
        "judge_keys": ["1학점"],
    },
    {
        "id": "q025", "difficulty": "medium",
        "question": "수강신청 사이트 주소는 무엇인가?",
        "answer": "http://sugang.bufs.ac.kr이다.",
        "judge_keys": ["sugang.bufs.ac.kr"],
    },
    {
        "id": "q028", "difficulty": "medium",
        "question": "2019학번 이후 학생의 재수강 제한 기준은 무엇인가?",
        "answer": "한 학기 최대 6학점, 졸업까지 최대 24학점으로 제한된다.",
        "judge_keys": ["6학점", "24학점"],
    },
    {
        "id": "q031", "difficulty": "medium",
        "question": "2024/2025학번의 제1·2전공 신청 및 변경(전과) 기간은 언제인가?",
        "answer": "2026년 5월 18일부터 5월 29일까지이다.",
        "judge_keys": ["5월 18일", "5월18일"],
    },
    {
        "id": "q032", "difficulty": "medium",
        "question": "수강신청 정정기간 이후 수업일수 1/4선 이내에 복학한 복학생의 수강신청은 어떻게 처리되는가?",
        "answer": "학사지원팀에서 수강신청을 대신 처리해준다.",
        "judge_keys": ["학사지원팀"],
    },
    {
        "id": "q038", "difficulty": "medium",
        "question": "OCU 시스템 사용료 납부기간은 언제인가?",
        "answer": "2026년 2월 23일부터 3월 19일까지이다.",
        "judge_keys": ["2월 23일", "2월23일"],
    },
    {
        "id": "q040", "difficulty": "medium",
        "question": "OCU 최대 수강학점을 초과하여 1과목(3학점) 신청 시 초과수강료는 얼마인가?",
        "answer": "120,000원이다.",
        "judge_keys": ["120,000", "120000"],
    },
    {
        "id": "q042", "difficulty": "medium",
        "question": "2022학번의 졸업에 필요한 최소 학점은 얼마인가?",
        "answer": "130학점 이상이다.",
        "judge_keys": ["130학점"],
    },
    {
        "id": "q043", "difficulty": "medium",
        "question": "2024학번 이후 교양과정 총 이수학점은 얼마인가?",
        "answer": "30학점이다.",
        "judge_keys": ["30학점"],
    },
    {
        "id": "q046", "difficulty": "medium",
        "question": "외국인 학생의 졸업인증 요건 중 TOPIK 기준은 무엇인가?",
        "answer": "TOPIK 4급 이상이다.",
        "judge_keys": ["4급", "TOPIK"],
    },
    {
        "id": "q049", "difficulty": "medium",
        "question": "OCU 성적이 부여되기 위한 출석 요건은 무엇인가?",
        "answer": "전체 출석일수의 12/15 이상을 충족해야 한다.",
        "judge_keys": ["12/15"],
    },
    # 어려움
    {
        "id": "q044", "difficulty": "hard",
        "question": "2023학번이 방법1(주전공+복수전공)로 졸업할 경우 각각 몇 학점을 이수해야 하는가?",
        "answer": "주전공 36학점, 복수전공 27학점이다.",
        "judge_keys": ["36학점", "27학점"],
    },
    {
        "id": "q045", "difficulty": "hard",
        "question": "2022학번이 방법1(주전공+복수전공)로 졸업할 경우 각각 몇 학점을 이수해야 하는가?",
        "answer": "주전공 36학점, 복수전공 30학점이다.",
        "judge_keys": ["36학점", "30학점"],
    },
    {
        "id": "q050", "difficulty": "hard",
        "question": "2024학번 이후, 2023학번, 2022학번, 2021학번의 복수전공 이수학점은 각각 얼마인가?",
        "answer": "2024학번 이후 30학점, 2023학번 27학점, 2022학번 30학점, 2021학번 33학점이다.",
        "judge_keys": ["27학점", "33학점"],
    },
]


# 채점 함수
def judge(generated: str, judge_keys: list) -> tuple:
    """
    judge_keys 중 하나라도 generated에 포함되면 hit.
    각 key는 독립 필수조건. 모든 key가 hit돼야 정답.
    """
    gen_norm = re.sub(r"[\s,.]", "", generated).lower()
    hit, miss = [], []
    for key in judge_keys:
        key_norm = re.sub(r"[\s,.]", "", key).lower()
        if key_norm in gen_norm:
            hit.append(key)
        else:
            miss.append(key)
    return len(miss) == 0, hit, miss


# 파이프라인 초기화
def init_pipeline():
    from app.config import settings
    settings.reranker.enabled = False

    analyzer = QueryAnalyzer()
    embedder = Embedder()
    chroma = ChromaStore(embedder=embedder)
    graph = AcademicGraph()
    router = QueryRouter(chroma_store=chroma, academic_graph=graph, reranker=False)
    merger = ContextMerger()
    generator = AnswerGenerator()

    print(f"  ChromaDB: {chroma.collection.count()} chunks  |  Graph: {graph.G.number_of_nodes()} nodes")
    return analyzer, router, merger, generator


# 단일 질문 실행
def run_single(q, analyzer, router, merger, generator) -> dict:
    question = q["question"]
    analysis = analyzer.analyze(question)
    search = router.route_and_search(question, analysis)
    merged = merger.merge(
        vector_results=search["vector_results"],
        graph_results=search["graph_results"],
    )
    try:
        generated = asyncio.run(
            generator.generate_full(
                question=question,
                context=merged.formatted_context,
                student_id=analysis.student_id,
            )
        ).strip()
    except Exception as e:
        generated = f"[ERROR] {e}"

    is_correct, hit, miss = judge(generated, q["judge_keys"])
    return {
        "id": q["id"],
        "difficulty": q["difficulty"],
        "question": question,
        "ground_truth": q["answer"],
        "generated": generated,
        "intent": analysis.intent.value,
        "is_correct": is_correct,
        "hit_keys": hit,
        "miss_keys": miss,
        "ctx_len": len(merged.formatted_context),
    }


def main():
    print("pipeline init...")
    analyzer, router, merger, generator = init_pipeline()
    print()

    results = []
    print("=" * 72)
    print(f"{'ID':<6} {'level':<8} {'intent':<18} {'ok':<5} miss_keys")
    print("=" * 72)

    for q in QUESTIONS:
        r = run_single(q, analyzer, router, merger, generator)
        results.append(r)
        mark = "O" if r["is_correct"] else "X"
        miss_str = ", ".join(r["miss_keys"]) if r["miss_keys"] else "-"
        print(f"{r['id']:<6} {r['difficulty']:<8} {r['intent']:<18} [{mark}]   {miss_str}")
        if not r["is_correct"]:
            print(f"         gen: {r['generated'][:80]}")

    # 집계
    total = len(results)
    correct = sum(1 for r in results if r["is_correct"])
    acc = correct / total * 100

    # F1 계산 (Precision/Recall/F1)
    # Precision = 정답 / 답변시도 (항상 답변하므로 = 정답/전체)
    # Recall    = 정답 / 전체문항
    TP = correct
    FP = total - correct
    FN = 0  # 거부/무응답 없음
    precision = TP / (TP + FP) if (TP + FP) > 0 else 0
    recall = TP / (TP + FN + correct) if (TP + FN + correct) > 0 else 0
    # 단순하게: recall = correct/total (항상 답변)
    recall = correct / total
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    by_diff = {}
    for diff in ("easy", "medium", "hard"):
        sub = [r for r in results if r["difficulty"] == diff]
        if sub:
            c = sum(1 for r in sub if r["is_correct"])
            by_diff[diff] = {"n": len(sub), "correct": c}

    print("\n" + "=" * 72)
    print("[BUFS chatbot -- " + str(total) + " questions]")
    print("=" * 72)
    print(f"  Accuracy  : {correct}/{total} = {acc:.1f}%")
    print(f"  Precision : {precision:.2f}")
    print(f"  Recall    : {recall:.2f}")
    print(f"  F1        : {f1:.2f}")
    print()
    for diff, s in by_diff.items():
        print(f"  {diff:<8}: {s['correct']}/{s['n']} ({s['correct']/s['n']*100:.0f}%)")

    wrong = [r for r in results if not r["is_correct"]]
    if wrong:
        print(f"\n  -- wrong answers ({len(wrong)}) --")
        for r in wrong:
            print(f"  [{r['id']}] {r['question'][:45]}")
            print(f"    truth: {r['ground_truth']}")
            print(f"    gen  : {r['generated'][:100]}")
            print(f"    miss : {r['miss_keys']}")

    out = Path("data/eval_results_50q.json")
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total": total, "correct": correct,
                "accuracy": round(acc / 100, 3),
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
                "by_difficulty": {k: {"n": v["n"], "correct": v["correct"],
                                      "acc": round(v["correct"]/v["n"], 3)}
                                  for k, v in by_diff.items()},
            },
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  saved: {out}")


if __name__ == "__main__":
    main()
