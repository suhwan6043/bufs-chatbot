"""
evaluation/generate_synthetic.py

합성 평가 데이터 생성 스크립트

생성 모델  : Ollama qwen2.5:14b  (답변 모델 EXAONE 3.5 7.8B와 분리)
출력 경로  : evaluation/synthetic_review/eval_question_{timestamp}.jsonl  (스테이징)
최종 경로  : data/eval_multilingual/eval_question.jsonl  (PDF 검수 후 수동 이동)

언어 구분  : 각 항목의 "lang" 필드 ("ko" / "en")
             check_language 필드 없음 — 평가 파이프라인에서 lang == "en" 으로 판단

⚠️  주의: 생성된 모든 항목은 PDF 원본(2026학년도 1학기 학사안내)으로
         반드시 검수한 뒤 최종 파일에 추가하세요.

실행:
    # 전체 생성
    python evaluation/generate_synthetic.py

    # 특정 섹션만
    python evaluation/generate_synthetic.py --section leave_of_absence

    # 한국어만
    python evaluation/generate_synthetic.py --lang ko

    # 드라이런 (Ollama 없이 프롬프트만 출력)
    python evaluation/generate_synthetic.py --dry-run
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings

# ── 경로 ──────────────────────────────────────────────────────────────────────

_ROOT        = Path(__file__).resolve().parent.parent
REVIEW_DIR   = Path(__file__).parent / "synthetic_review"
FINAL_EVAL   = _ROOT / "data" / "eval_multilingual" / "eval_question.jsonl"
EVAL_EN_SEED = _ROOT / "data" / "eval_multilingual" / "eval_questions_en.jsonl"

# ── 모델 ──────────────────────────────────────────────────────────────────────

GENERATE_MODEL = "qwen2.5:14b"   # 생성 전용 — 답변 모델(EXAONE 3.5 7.8B)과 분리

# ── 섹션 정의 ──────────────────────────────────────────────────────────────────
#
# ⚠️  context 필드의 모든 수치·날짜는 PDF 원본과 대조 필수.
#     PDF 미확인 항목에는 # TODO: PDF 확인 주석을 달았음.

SECTIONS = [
    # ── 1순위: 커버리지 보완 (완전 누락) ────────────────────────────────────────
    {
        "id":       "leave_of_absence",
        "label_ko": "휴학",
        "label_en": "Leave of Absence",
        "target":   8,
        "difficulty_dist": {"easy": 3, "medium": 4, "hard": 1},
        "context": """
휴학 관련 규정 (2026학년도 1학기 학사안내 — PDF 확인 완료):
- 온라인 휴·복학 신청: 7월 6일(월) ~ 8월 30일(일)
- 수강신청을 위해 사전 복학 필수 (휴학생 수강신청 불가)
- 군복무 중 OCU 학점인정신청: 3월 9일(월) ~ 3월 13일(금)
- 재입학 접수: 5월 4일(월) ~ 5월 29일(금)
- 휴학 신청은 학생포털시스템에서 온라인으로 처리
- 수업일수 1/4선 이내에 복학하는 복학생의 수강신청은 학사지원팀에서 처리
        """,
        "edge_cases_ko": [
            "군복무 중 휴학생도 OCU 수강이 가능한가?",
            "휴학 중에 수강신청을 할 수 있는가?",
        ],
        "edge_cases_en": [
            "Can a student on military service take OCU courses?",
            "Can a student apply for courses while on leave of absence?",
        ],
    },
    {
        "id":       "return_from_leave",
        "label_ko": "복학",
        "label_en": "Return from Leave",
        "target":   5,
        "difficulty_dist": {"easy": 2, "medium": 2, "hard": 1},
        "context": """
복학 관련 규정 (2026학년도 1학기 학사안내 — PDF 확인 완료):
- 온라인 휴·복학 신청: 7월 6일(월) ~ 8월 30일(일)
- 수강신청 전에 반드시 복학 처리 완료해야 함
- 수강신청 확인기간 이후 수업일수 1/4선(3/26 17시) 이내에 복학하는 경우
  학사지원팀에서 수강신청 처리, 담당교수에게 출석부 등재 확인 필요
- 복학 후 수강신청 방법은 일반 학생과 동일
        """,
        "edge_cases_ko": [
            "수강정정기간 이후에 복학하면 수강신청은 어떻게 하나?",
        ],
        "edge_cases_en": [
            "What happens to course registration if I return after the adjustment period?",
        ],
    },
    {
        "id":       "academic_probation",
        "label_ko": "학사경고",
        "label_en": "Academic Probation",
        "target":   5,
        "difficulty_dist": {"easy": 2, "medium": 2, "hard": 1},
        "context": """
학사경고 관련 (2026학년도 1학기 학사안내 — PDF 확인 완료):
- 학사경고자 대상 교과목: 자기경영학습법Ⅰ(GS1401, 1학기), 자기경영학습법Ⅱ(GS1402, 2학기)
  각 2학점, P/NP 평가, 자유선택 이수구분
- 학사경고자가 자기경영학습법 수강 시 최대 수강학점:
  * 2022학번 이전: 17학점 초과 → 19학점까지 신청 가능
  * 2023학번 이후: 16학점 초과 → 18학점까지 신청 가능
- 학사경고자 외 학생은 해당 과목 수강 불가
- 문의: PSC교수학습기술센터(051-509-6584)
        """,
        "edge_cases_ko": [
            "학사경고를 받은 2023학번 학생의 최대 수강학점은?",
            "학사경고자가 아닌 학생도 자기경영학습법을 수강할 수 있나?",
        ],
        "edge_cases_en": [
            "What is the max credit load for a 2023 cohort student on academic probation?",
            "Can a student not on academic probation take the Self-Management Learning course?",
        ],
    },
    {
        "id":       "teaching_credential",
        "label_ko": "교직과정",
        "label_en": "Teaching Credential",
        "target":   6,
        "difficulty_dist": {"easy": 2, "medium": 3, "hard": 1},
        "context": """
교직과정 이수 요건 (2024학번 이후) (PDF 확인 완료):
- 전공과목 50학점 이상 (경영·회계전공 53학점 이상)
- 교직과목 24학점 이상
  * 교직이론 12학점 이상 (6과목 이상)
  * 교직소양 8학점 + 교육실천세미나 2학점 + 디지털교육 2학점
  * 교육실습 4학점 (학교현장실습 + 교육봉사활동)
- 교직 적성·인성검사 적격 판정 2회 이상
- 응급처치 및 심폐소생술 2회 이상
- 성인지교육 2회 이상
- 외국어 교사자격증: 외국어 구사능력시험 80점 이상
- 전공 평균성적 75점 이상, 교직 평균성적 80점 이상
- 2학년 2학기 중 교직복수전공 신청 가능 (추가 3학점 신청 가능)
        """,
        "edge_cases_ko": [
            "교직과정 이수자는 수강신청 최대학점이 몇 학점인가?",
            "교직과정 부전공과 주전공의 졸업시험은 각각 따로 봐야 하나?",
        ],
        "edge_cases_en": [
            "How many credits can a teaching credential double major student take per semester?",
            "What GPA is required in major courses for teacher certification?",
        ],
    },
    {
        "id":       "global_comm_detail",
        "label_ko": "글로벌소통역량과정 상세",
        "label_en": "Global Communication Program Detail",
        "target":   6,
        "difficulty_dist": {"easy": 2, "medium": 3, "hard": 1},
        "context": """
글로벌소통역량과정 (2026학년도 1학기 — PDF 확인 완료):
- College English: Speaking A1/B1/C1, Writing A1/B1 (각 3학점)
- 외국인 유학생 온라인 과정: Speaking A1/B1/C1 (온라인)
- 한국어 과정 (유학생): 유학생을 위한 한국어 연습 1, 2 (각 3학점)
- 수강 방법: 진단평가 결과에 따라 배정받은 레벨 분반으로 신청
  1학기: 진단평가 결과 레벨로 신청 (예: A1 → Speaking A1 또는 Writing A1)
  2학기: 1학기 레벨의 '2' 교과목 (예: A1 → A2)
- 레벨테스트 일정 (2026):
  [1차] 1.29(목) ~ 2.5(목) / 결과: 2.6(금)
  [2차] 2.7(토) ~ 2.10(화) / 결과: 2.11(수)
  [3차] 2.19(목) ~ 2.22(일) / 결과: 2.23(월)
  [4차] 2.26(목) ~ 3.2(월) / 결과: 3.3(화)
- 대체과목 (2022학번·2021학번·2017~2020학번):
  실전토익(GS1318), 토익Ⅰ~Ⅵ(TOE107~112)로 이수 가능
- 문의: 영어과정 051-509-6486 / 한국어과정 051-509-5931
        """,
        "edge_cases_ko": [
            "1학기에 Speaking A1을 들었으면 2학기에는 무엇을 들어야 하나?",
            "2022학번이 글로벌소통역량 대신 토익으로 이수할 수 있나?",
        ],
        "edge_cases_en": [
            "If I took Speaking A1 in semester 1, what should I take in semester 2?",
            "Can a 2022 cohort student substitute TOEIC for the Global Communication program?",
        ],
    },
    {
        "id":       "major_exploration",
        "label_ko": "전공탐색과정",
        "label_en": "Major Exploration Program",
        "target":   5,
        "difficulty_dist": {"easy": 2, "medium": 2, "hard": 1},
        "context": """
전공탐색과정 (PDF 확인 완료):
- 대상: 글로벌자유전공학부(통합모집) 1학년 학생
- 이수학점: 3~12학점 (1,2학기 합산)
- 최소이수: 3학점 (2024학년도 2학기부터 축소)
- 나머지 학점: 체험하고 싶은 전공의 1학년 선이수 교과목 자유 이수
- 별도모집(사회체육, 스포츠재활, 항공서비스) 학생은 전공탐색 이수 불필요
- 수강신청일: 1학년 신청일(2/9)에 신청
- 수강신청 시스템에서 [타과전공]으로 검색하여 신청
- 2026학번 전공배정: 6월 희망전공 예비조사 → 10~11월 전공신청
        """,
        "edge_cases_ko": [
            "항공서비스전공 학생도 전공탐색과정을 이수해야 하나?",
            "전공탐색과정은 수강신청 시스템에서 어떻게 찾나?",
        ],
        "edge_cases_en": [
            "Is the major exploration program required for aviation service major students?",
            "How do I search for major exploration courses in the registration system?",
        ],
    },
    # ── 2순위: 기존 섹션 보완 ───────────────────────────────────────────────────
    {
        "id":       "scholarship_detail",
        "label_ko": "장학금 상세",
        "label_en": "Scholarship Detail",
        "target":   5,
        "difficulty_dist": {"easy": 2, "medium": 2, "hard": 1},
        "context": """
장학금 관련 (PDF 확인 완료):
- 장학금은 12학점(4학년은 9학점) 이상 취득자에 한해 지급
- 성적평가 선택제(P/NP)로 이수한 학점은 장학 산정 시 원성적(등급제) 기준 적용
- 부분적 성적포기 후에도 장학 산정은 원성적 기준
- GLE이수자(직전학기 GLE 성적 평점 3.5 이상): 최대 24학점 신청 가능
- 파이데이아창의인재학과 및 영어권 대학 복수학위과정 이수자: 27학점 신청 가능
        """,
        "edge_cases_ko": [
            "P/NP로 수강한 과목이 장학금 수급에 영향을 주나?",
            "4학년 학생의 장학금 수급을 위한 최소 이수학점은?",
        ],
        "edge_cases_en": [
            "Do P/NP grades affect scholarship eligibility?",
            "What is the minimum credit requirement for a 4th-year student to receive a scholarship?",
        ],
    },
    {
        "id":       "community_service_detail",
        "label_ko": "사회봉사 상세",
        "label_en": "Community Service Detail",
        "target":   4,
        "difficulty_dist": {"easy": 2, "medium": 2, "hard": 0},
        "context": """
사회봉사 교과목 (PDF 확인 완료):
- 1학점, 일반선택, P/NP 평가
- 이수 방법: 자원봉사 25시간 완료 (인정기간: 입학일부터 종강 1주 전까지)
- 온라인 분반: 12시간 + 온라인 수업 (대상: 졸업 전 마지막 학기, 1~7학기 학생 불가)
- 기본교육(1주차), 중간교육(8주차), 기말교육(14주차) 필수 참석
- 봉사활동 인정 기관: 1365, VMS, DOVOL 등 포털에서 확인서 발급 가능한 봉사
- 군 경력증명서 봉사활동: 최대 25시간까지 인정
- 2016학번부터 이수 필수 (졸업요건)
- 수강신청 최대학점 초과 교과목: 사회봉사 신청 시 추가 1학점까지 초과 신청 가능
        """,
        "edge_cases_ko": [
            "군 복무 중 봉사시간이 사회봉사 교과목에 인정되나?",
            "사회봉사 온라인 분반은 누구나 수강할 수 있나?",
        ],
        "edge_cases_en": [
            "Can military service volunteer hours count toward the community service course?",
            "Who can take the online section of the community service course?",
        ],
    },
    # ── 3순위: 학번 경계 엣지 케이스 ───────────────────────────────────────────
    {
        "id":       "cohort_boundary",
        "label_ko": "학번별 분기",
        "label_en": "Cohort Boundary Cases",
        "target":   10,
        "difficulty_dist": {"easy": 1, "medium": 5, "hard": 4},
        "context": """
학번별 주요 차이점 (PDF 확인 완료 — p.24, p.25):
┌──────────────┬──────────┬──────────┬──────────┬────────────┐
│              │2024이후  │2023학번  │2022학번  │2021이전    │
├──────────────┼──────────┼──────────┼──────────┼────────────┤
│졸업학점      │120학점   │120학점   │130학점   │130학점     │
│최대수강학점  │18학점    │18학점    │19학점    │19학점      │
│복수전공학점  │30학점    │27학점    │30학점    │33학점      │
│부전공        │불가      │18학점    │15학점    │18학점      │
│마이크로전공  │9학점     │9학점     │9학점     │선택사항    │
│취업커뮤니티  │미해당    │필수      │필수      │필수        │
│학점이월제    │미적용    │미적용    │적용      │적용        │
│졸업인증제    │미해당    │해당      │해당      │해당(2017~) │
└──────────────┴──────────┴──────────┴──────────┴────────────┘
경계 케이스:
- 2022학번과 2023학번: 졸업학점 130 vs 120 (10학점 차이)
- 2023학번: 부전공 가능, 2024학번: 부전공 불가
- 학점이월제: 2022학번까지 적용, 2023학번부터 폐지
        """,
        "edge_cases_ko": [
            "2022학번과 2023학번의 졸업학점 차이는?",
            "2024학번 학생이 부전공을 신청할 수 있나?",
            "학점이월제가 적용되는 마지막 학번은?",
        ],
        "edge_cases_en": [
            "What is the difference in graduation credits between 2022 and 2023 cohorts?",
            "Can a 2024 cohort student declare a minor?",
            "Which is the last cohort that can use the credit carryover system?",
        ],
    },
]

# ── 생성 프롬프트 ──────────────────────────────────────────────────────────────

_PROMPT_EN = """You are generating evaluation questions for a Korean university academic chatbot.

## Context (source document excerpt)
{context}

## Task
Generate {n} diverse evaluation questions in ENGLISH about the above regulations.

## Requirements
- Difficulty distribution: {difficulty_dist}
- Each question must be answerable from the context above
- Include {n_edge} of these specific edge case questions (use them exactly as given):
{edge_cases}
- Remaining questions should cover different aspects of the context
- Vary question phrasing: "How do I...", "What is...", "When can...", "Is it possible to...", etc.

## Output Format
Return ONLY a JSON array, no explanation:
[
  {{
    "question": "...",
    "ground_truth": "...",
    "key_facts": ["number or date strings that must appear in the answer"],
    "difficulty": "easy|medium|hard"
  }}
]

Rules for ground_truth:
- Write in English only
- Include all relevant numbers, dates, conditions from the context
- For cohort-specific rules, always specify which cohort applies
- key_facts: extract numbers (e.g. "18", "120") and dates (e.g. "7.6", "3.26") that MUST appear
"""

_PROMPT_KO = """당신은 한국 대학교 학사 안내 챗봇의 평가 질문을 생성하는 역할입니다.

## 컨텍스트 (학사안내 문서 발췌)
{context}

## 작업
위 규정에 대한 평가 질문 {n}개를 한국어로 생성하세요.

## 요구사항
- 난이도 배분: {difficulty_dist}
- 모든 질문은 위 컨텍스트만으로 답변 가능해야 함
- 다음 엣지 케이스 질문 {n_edge}개를 반드시 포함 (그대로 사용):
{edge_cases}
- 나머지 질문은 컨텍스트의 다른 측면을 다룰 것
- 질문 표현 다양화: "어떻게...", "언제...", "~이 가능한가?", "~의 조건은?", "~학번은..." 등

## 출력 형식
JSON 배열만 반환하고, 설명 없음:
[
  {{
    "question": "...",
    "ground_truth": "...",
    "key_facts": ["답변에 반드시 포함되어야 할 숫자 또는 날짜 문자열"],
    "difficulty": "easy|medium|hard"
  }}
]

ground_truth 규칙:
- 한국어로만 작성
- 컨텍스트의 숫자, 날짜, 조건을 모두 포함
- 학번별로 다른 규정은 반드시 해당 학번 명시
- key_facts: 답변에 반드시 등장해야 할 숫자(예: "18", "120")와 날짜(예: "3.26", "7.6") 추출
"""


# ── 중복 제거 ──────────────────────────────────────────────────────────────────

def _jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def is_duplicate(question: str, existing: list[str], threshold: float = 0.6) -> bool:
    return any(_jaccard(question, q) >= threshold for q in existing)


# ── Ollama 호출 ────────────────────────────────────────────────────────────────

async def generate_with_ollama(prompt: str, model: str, timeout: int = 180) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_ctx": 4096},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{settings.ollama.base_url}/api/generate", json=payload
        )
        resp.raise_for_status()
        return resp.json().get("response", "")


def parse_json_response(raw: str) -> list[dict]:
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip().replace('```', '').strip()
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 배열 없음: {raw[:200]}")
    return json.loads(m.group())


# ── 단일 섹션 생성 ─────────────────────────────────────────────────────────────

async def generate_section(
    section: dict,
    lang: str,
    model: str,
    existing_questions: list[str],
    dry_run: bool = False,
) -> list[dict]:
    n = section["target"]
    edge_key = f"edge_cases_{lang}"
    edge_cases = section.get(edge_key, [])
    n_edge = min(len(edge_cases), max(1, n // 3))
    selected_edges = edge_cases[:n_edge]

    diff_dist = section["difficulty_dist"]
    if lang == "ko":
        diff_str = ", ".join(f"{d}: {c}개" for d, c in diff_dist.items() if c > 0)
    else:
        diff_str = ", ".join(f"{d}: {c} cases" for d, c in diff_dist.items() if c > 0)

    edge_str = "\n".join(f"  - {q}" for q in selected_edges) or "  (none)"
    prompt_tmpl = _PROMPT_KO if lang == "ko" else _PROMPT_EN
    prompt = prompt_tmpl.format(
        context=section["context"].strip(),
        n=n,
        difficulty_dist=diff_str,
        n_edge=n_edge,
        edge_cases=edge_str,
    )

    if dry_run:
        print(f"\n{'='*60}")
        print(f"[DRY RUN] {section['id']} / {lang}")
        print(f"{'='*60}")
        print(prompt[:800] + "...\n")
        return []

    print(f"  생성 중: {section['id']} ({lang}) — 목표 {n}개")
    raw = await generate_with_ollama(prompt, model)

    try:
        items = parse_json_response(raw)
    except Exception as e:
        print(f"  ⚠️  파싱 실패: {e}")
        return []

    results = []
    for item in items:
        q  = item.get("question", "").strip()
        gt = item.get("ground_truth", "").strip()
        if not q or not gt:
            continue
        if is_duplicate(q, existing_questions):
            print(f"  ⏭️  중복 제거: {q[:50]}")
            continue
        results.append({
            "id":           f"syn_{lang}_{section['id']}_{len(results)+1:03d}",
            "category":     section["id"],
            "question":     q,
            "ground_truth": gt,
            "key_facts":    item.get("key_facts", []),
            "difficulty":   item.get("difficulty", "medium"),
            "lang":         lang,       # "ko" 또는 "en" — 평가 시 lang == "en" 으로 언어 체크
            "synthetic":    True,       # 합성 데이터 마커 — PDF 검수 후 최종 파일로 이동
        })
        existing_questions.append(q)

    print(f"  ✅ {len(results)}개 생성 ({section['id']} / {lang})")
    return results


# ── 기존 질문 로드 (중복 방지) ─────────────────────────────────────────────────

def _load_questions(path: Path) -> list[str]:
    if not path.exists():
        return []
    questions = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                questions.append(json.loads(line)["question"])
            except (json.JSONDecodeError, KeyError):
                pass
    return questions


# ── 메인 ──────────────────────────────────────────────────────────────────────

async def main(args):
    langs = ["ko", "en"] if args.lang == "both" else [args.lang]

    sections = SECTIONS
    if args.section:
        sections = [s for s in SECTIONS if s["id"] == args.section]
        if not sections:
            print(f"❌ 섹션 '{args.section}' 없음")
            print("사용 가능:", [s["id"] for s in SECTIONS])
            return

    # 기존 질문 로드 — 언어별 dedup 풀
    existing: dict[str, list[str]] = {
        "ko": _load_questions(FINAL_EVAL),          # 최종 파일 (ko+en 혼재)
        "en": _load_questions(FINAL_EVAL),
    }
    # 기존 영어 seed 파일도 포함
    for q in _load_questions(EVAL_EN_SEED):
        if q not in existing["en"]:
            existing["en"].append(q)

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())
    staging_path = REVIEW_DIR / f"eval_question_{timestamp}.jsonl"

    all_items: list[dict] = []

    for section in sections:
        print(f"\n📂 섹션: {section['id']} ({section['label_ko']} / {section['label_en']})")

        for lang in langs:
            items = await generate_section(
                section, lang, GENERATE_MODEL, existing[lang], args.dry_run
            )
            all_items.extend(items)
            await asyncio.sleep(1)

    if args.dry_run:
        return

    # 스테이징 파일 저장
    with staging_path.open("w", encoding="utf-8") as f:
        for item in all_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # 요약
    from collections import Counter
    print(f"\n{'='*50}")
    print(f"생성 완료 — {len(all_items)}개")
    print(f"{'='*50}")
    for lang in langs:
        lang_items = [i for i in all_items if i["lang"] == lang]
        if not lang_items:
            continue
        diff_c = Counter(i["difficulty"] for i in lang_items)
        cat_c  = Counter(i["category"]   for i in lang_items)
        label  = "한국어" if lang == "ko" else "영어"
        print(f"\n{label} ({len(lang_items)}개):")
        print(f"  난이도: {dict(diff_c)}")
        print(f"  카테고리: {dict(cat_c)}")

    print(f"\n📋 스테이징 파일 → {staging_path}")
    print(f"⚠️  PDF 원본 검수 후 {FINAL_EVAL} 에 추가하세요.")


def entry():
    parser = argparse.ArgumentParser(description="합성 평가 데이터 생성 (qwen2.5:14b)")
    parser.add_argument("--section", default=None, help="특정 섹션만 생성")
    parser.add_argument("--lang",    default="both",
                        choices=["ko", "en", "both"], help="생성 언어")
    parser.add_argument("--dry-run", action="store_true",
                        help="프롬프트만 출력, Ollama 미호출")
    args = parser.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    entry()
