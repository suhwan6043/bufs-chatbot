"""
쿼리 분석기 - 규칙 기반 Intent 분류 + 엔티티 추출
LLM 호출 없이 정규식 + 키워드 매칭으로 <5ms 처리
JSX 스키마 기준 9 Intent, 학번 그룹/학생유형/과목명 엔티티 추출
EN 쿼리: FlashText (aliases_en→ko) + BGE-M3 fallback
"""

import re
import logging
from pathlib import Path
from typing import Optional

import yaml
from flashtext import KeywordProcessor

import numpy as np

from app.models import Intent, QueryAnalysis, QuestionType
from app.pipeline.glossary import Glossary
from app.pipeline.language_detector import detect_language
from app.graphdb.academic_graph import get_student_group

logger = logging.getLogger(__name__)

_TERMS_YAML = Path(__file__).parent.parent.parent / "config" / "en_glossary.yaml"
_QT_YAML = Path(__file__).parent.parent.parent / "config" / "question_types.yaml"

# ── QuestionType 분류기 ───────────────────────────────────────
# 원칙 2: BGE-M3 임베딩 재사용, 추가 모델 없이 cosine similarity
# 원칙 4: reference phrases를 YAML 데이터로 관리 (하드코딩 금지)

_QT_NAME_TO_ENUM = {
    "overview": QuestionType.OVERVIEW,
    "factoid": QuestionType.FACTOID,
    "procedural": QuestionType.PROCEDURAL,
    "reasoning": QuestionType.REASONING,
}

# 질문어 패턴 — overview 판정용 (이 단어가 없으면 overview 후보)
_QUESTION_WORDS = (
    "어떻게", "언제", "얼마", "몇", "어디", "뭐", "무엇",
    "왜", "가능", "되나", "할 수", "하나요", "인가요", "인지",
    "방법", "절차", "서류", "조건", "자격",
    "주소", "사이트", "전화", "번호", "이메일",  # 구체 정보 요청
    "알려", "보여", "설명", "찾아", "확인",  # 암시적 요청 표현
    "궁금", "문의",  # 정보 요청 표현
)


class EnTermMapper:
    """
    FlashText 기반 EN→KO 학술 용어 매퍼 (모듈 싱글톤).

    academic_terms.yaml의 aliases_en → ko 매핑을 메모리에 올려두고
    O(N) 속도로 영어 쿼리에서 한국어 용어를 추출합니다.
    """

    _instance: Optional["EnTermMapper"] = None

    @classmethod
    def get(cls) -> "EnTermMapper":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._processor = KeywordProcessor(case_sensitive=False)
        self._ko_processor = KeywordProcessor(case_sensitive=False)
        self._ko_to_en: dict[str, str] = {}  # ko → canonical en
        self._load_terms()

    def _load_terms(self) -> None:
        try:
            with open(_TERMS_YAML, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning("academic_terms.yaml not found: %s", _TERMS_YAML)
            return

        for term in data.get("terms", []):
            ko: str = term.get("ko", "")
            en: str = term.get("en", "")
            if not ko:
                continue
            if en:
                self._ko_to_en[ko] = en
                self._processor.add_keyword(en, ko)
            for alias in term.get("aliases_en", []):
                if alias:
                    self._processor.add_keyword(alias, ko)
            # KO 용어 + aliases_ko → KO 컨텍스트 스캔용
            self._ko_processor.add_keyword(ko, ko)
            for alias in term.get("aliases_ko", []):
                if alias:
                    self._ko_processor.add_keyword(alias, ko)

        logger.debug("EnTermMapper: %d EN + %d KO keywords loaded",
                     len(self._processor), len(self._ko_processor))

    def extract(self, text: str) -> list[dict]:
        """
        영어 텍스트에서 매핑된 학술 용어를 추출합니다.

        Returns:
            [{"ko": "수강신청", "en": "Course Registration"}, ...]
        """
        ko_matches: list[str] = self._processor.extract_keywords(text)
        seen: set[str] = set()
        result: list[dict] = []
        for ko in ko_matches:
            if ko not in seen:
                seen.add(ko)
                result.append({"ko": ko, "en": self._ko_to_en.get(ko, ko)})
        return result

    def extract_from_ko_context(self, ko_text: str, max_terms: int = 30) -> list[dict]:
        """KO 컨텍스트에서 학사 용어를 추출하고 EN 매핑을 반환합니다.

        Returns:
            [{"ko": "수강신청", "en": "Course Registration"}, ...]
        """
        ko_matches: list[str] = self._ko_processor.extract_keywords(ko_text)
        seen: set[str] = set()
        result: list[dict] = []
        for ko in ko_matches:
            if ko not in seen:
                seen.add(ko)
                result.append({"ko": ko, "en": self._ko_to_en.get(ko, ko)})
                if len(result) >= max_terms:
                    break
        return result


class QueryAnalyzer:
    """
    [역할] 질문 의도 분류 + 엔티티 추출 (규칙 기반)
    [핵심] LLM 호출 없음! 정규식 + 키워드 매칭으로 <5ms 처리
    [이유] 7B 모델은 분석보다 생성에 집중시키는 것이 품질 향상에 유리
    """

    STUDENT_ID_PATTERN = re.compile(r"(20[12]\d)학번")
    STUDENT_ID_SHORT_PATTERN = re.compile(r"\b([12]\d)학번")   # 22학번, 23학번 등 2자리 입력
    STUDENT_ID_RANGE_PATTERN = re.compile(r"(20[12]\d)\s*[~\-]\s*(20[12]\d)학번")
    STUDENT_ID_BOUND_PATTERN = re.compile(r"(20[12]\d)학번\s*(이후|이전)")
    # EN 학번 추출: suffix 필수로 오탐 방지 ("the 2020 calendar" 같은 연도 참조 제외)
    # 패턴 1: "class of 2020"  패턴 2: "2020 student/cohort/enrollment/year"
    EN_COHORT_PATTERN = re.compile(
        r"\bclass\s+of\s+(20[12]\d)\b"
        r"|\b(20[12]\d)\s+(?:student|cohort|enrollment|year)\b",
        re.IGNORECASE,
    )

    STUDENT_TYPE_PATTERNS = {
        "외국인": re.compile(r"외국인|유학생|외국인학생"),
        "편입생": re.compile(r"편입생?|편입학"),
    }

    # EN 학생유형 패턴 (Gap 3)
    EN_STUDENT_TYPE_PATTERNS = {
        "외국인": re.compile(
            r"international student|foreign student|exchange student", re.IGNORECASE
        ),
        "편입생": re.compile(
            r"transfer student|transfer admission", re.IGNORECASE
        ),
    }

    # EN 기간/한도 키워드 (Gap 2)
    _EN_PERIOD_KW = (
        "when", "schedule", "deadline", "period", "date",
        "start", "end", "until", "from", "by when",
    )
    _EN_LIMIT_KW = (
        "maximum", "max", "how many credits", "limit",
        "how much", "up to", "at most",
        "how many",                     # "how many courses can I take"
        "minimum", "at least",          # "minimum number of credits"
        "minimum credits", "credits required", "credits needed",
        "credits to graduate", "double major credits", "minor credits", "major credits",
    )

    # EN 엔티티 키워드 상수
    _EN_OCU_KW = ("ocu", "open cyber university", "cyber university consortium")
    _EN_GPA_KW = ("gpa 4.0", "4.0 gpa", "grade point average 4", "prior semester gpa", "previous semester gpa")
    _EN_BASKET_KW = ("wish list", "basket", "shopping cart", "pre-registration cart")
    _EN_PAYMENT_KW = ("tuition payment", "tuition fee payment", "pay tuition", "payment deadline", "fee payment")
    _EN_2ND_MAJOR_CREDITS_KW = (
        "double major credit", "double major requirement",
        "minor credit", "second major credit", "dual major credit",
    )
    _EN_GRAD_CERT_KW = ("topik", "toeic", "toefl", "ielts", "language proficiency", "english proficiency")
    # "option N" / "track N" 오탐 방지: 전공이수 맥락 키워드가 함께 있을 때만 적용
    _EN_MAJOR_METHOD = {
        "method 1": "방법1", "method 2": "방법2", "method 3": "방법3",
    }
    # "option N"은 맥락 키워드(major/double/minor/track) 동반 시에만 인식
    _EN_MAJOR_METHOD_CONTEXT_KW = ("major", "double", "minor", "second major", "track")
    _EN_MAJOR_METHOD_OPTION = {
        "option 1": "방법1", "track 1": "방법1",
        "option 2": "방법2", "track 2": "방법2",
        "option 3": "방법3", "track 3": "방법3",
    }
    _EN_METHOD_KW = ("how to", "how do i", "procedure", "process", "steps to", "how can i apply", "how to apply", "submit")
    _EN_LOCATION_KW = ("where", "which office", "which building", "which department", "which place")
    _EN_ELIGIBILITY_KW = ("eligible", "qualify", "qualified", "can i", "am i able", "is it possible", "allowed to")

    # ── High #1: URL·사이트 기대 질문 키워드 ─────────────────────────────────
    _EN_URL_SEEKING_KW = (
        "where can i", "where do i", "which website", "what website", "what site",
        "what is the website", "what is the url", "what is the link",
        "online portal", "apply online", "register online", "access online",
        "which portal", "where to apply", "where to register",
    )

    # ── High #2: 학기 구분 (전기/후기) ──────────────────────────────────────
    _EN_SEMESTER_HALF_FIRST = (
        "first semester", "spring semester", "1st semester",
        "first-semester", "semester 1",
    )
    _EN_SEMESTER_HALF_SECOND = (
        "second semester", "fall semester", "autumn semester",
        "2nd semester", "second-semester", "semester 2",
    )

    # ── High #3: 성적선택제 (P/NP) — requires_graph=False ──────────────────
    _EN_GRADE_SEL_KW = (
        "pass/fail", "pass or fail", "p/np", "pass/np",
        "pass non-pass", "non-pass", "np grade",
        "grade option", "grade selection", "grade waiver",
        "grade mode", "satisfactory", "s/u grade",
        "grade conversion", "pass fail conversion",
        "retake", "retaken", "retaking",
        "grade cancel", "grade cancellation",
        "partial grade", "partial waiver",
    )

    # ── Medium #4: 수강취소 마감 ────────────────────────────────────────────
    _EN_CANCEL_KW = ("cancel", "drop", "withdraw", "unenroll", "un-enroll")
    _EN_DEADLINE_SUFFIX_KW = (
        "by when", "deadline", "last day", "cutoff", "until when",
        "how long", "last date",
    )

    # ── Medium #5: 교양영역 ──────────────────────────────────────────────────
    _EN_LIBERAL_ARTS_MAP = {
        "인성체험교양": (
            "chapel", "psc seminar", "social service", "volunteer",
            "community service", "character education",
        ),
        "기초교양": (
            "academic writing", "writing course", "reading and discussion",
            "basic liberal arts", "writing class",
        ),
        "균형교양": (
            "general education", "liberal arts", "humanities course",
            "balanced liberal arts", "cultural studies",
        ),
        "글로벌소통역량": (
            "global communication", "college english", "ai plus",
            "global literacy", "english communication",
        ),
    }

    # ── Low #11: table_lookup / rule_list question_focus ────────────────────
    _EN_TABLE_KW = (
        "table", "chart", "by year", "by cohort", "comparison",
        "credits required per", "breakdown",
    )
    _EN_RULE_LIST_KW = (
        "requirements", "qualifications", "conditions", "criteria",
        "what are the requirements", "what do i need", "rules for",
    )

    # ── Low #9: 2자리 코호트 ("22 cohort" → 2022) ───────────────────────────
    EN_COHORT_SHORT_PATTERN = re.compile(
        r"\b([12]\d)\s+(?:cohort|enrollment|admission year|class)\b",
        re.IGNORECASE,
    )

    COURSE_NUMBER_PATTERN = re.compile(r"[A-Z]{2,4}\d{3,4}")

    # 과목명 추출 패턴: "미적분학 대체과목", "영어회화 과목 정보" 등
    COURSE_NAME_BEFORE_KW = re.compile(
        r"([가-힣A-Za-z0-9]{2,12})\s*(?:대체과목|동일과목|대체가능|대체|대신|과목\s*정보)"
    )
    COURSE_NAME_IN_CONTEXT = re.compile(
        r"([가-힣A-Za-z0-9]{2,12})\s+(?:과목|수업|강의)\s"
    )
    _NON_COURSE_WORDS = frozenset({
        "어떤", "무슨", "이번", "다른", "해당", "전체", "모든", "각각",
        "수강신청", "수강", "졸업", "학사", "학점", "성적", "교양",
        "복수전공", "부전공", "제2전공", "마이크로전공", "융합전공",
    })

    INTENT_KEYWORDS = {
        Intent.EARLY_GRADUATION: [
            "조기졸업", "조기 졸업",
            "7학기 졸업", "6학기 졸업",
            "7학기만에", "6학기만에",
            "조기졸업 신청", "조기졸업 자격", "조기졸업 조건",
            "조기졸업 기준", "조기졸업 학점", "조기졸업 신청기간",
            "빨리 졸업", "일찍 졸업",
        ],
        Intent.GRADUATION_REQ: [
            "졸업", "졸업요건", "졸업학점", "이수학점",
            "몇 학점", "전공학점", "글로벌소통역량",
            "취업커뮤니티", "NOMAD", "졸업인증", "졸업시험",
            "학점인정", "선이수",
            # 성적처리기준
            "성적처리", "성적 처리", "성적기준", "평점산출", "평점계산", "학점계산",
            "성적이의", "성적정정", "이의신청",
            # 평가방식
            "상대평가", "절대평가", "성적평가",
        ],
        Intent.REGISTRATION: [
            "수강신청", "수강", "재수강", "학점이월", "학점 이월", "이월", "신청가능학점",
            "최대학점", "신청학점", "취소", "최대신청",
            "한국열린사이버대학교", "OCU", "장바구니", "납부",
            "수강신청 정정", "수강정정", "공인결석계",
            "이수 가능", "신청 가능", "수강 가능",
            "이수구분", "이수구분 변경", "이수구분 신청",
            # "자주 묻는 질문", "FAQ" → GENERAL 자연 분류 (특정 토픽 아님)
            # 계절학기
            "계절학기", "계절수업", "하계학기", "동계학기",
            # 성적선택제도 (A~F / P/NP 선택 신청)
            "성적선택", "성적포기", "Pass", "P/NP", "등급제",
            # 자유학기제
            "자유학기제", "자유학기", "7+1학기", "7+1",
            # 전자출결
            "전자출결", "출결", "출석체크", "전자출석",
            # 등록금반환
            "등록금 반환", "등록금반환", "등록금 환불", "환불기준",
            "등록금납부", "수업료 반환",
        ],
        Intent.SCHEDULE: [
            "언제", "기간", "일정", "일자", "스케줄", "마감", "시작일", "종료일",
            "중간고사", "기말고사", "개강", "종강", "방학",
            "수강취소", "수업일수", "학사일정",
            # 시험 — "시험" 단독은 모호(일정 vs 평가방법)하므로 제외
            "시험기간", "시험일정", "고사",
        ],
        Intent.COURSE_INFO: [
            "과목", "교과목", "수업", "강의", "강의실",
            "개설", "강좌", "온라인", "대면", "플립",
        ],
        Intent.MAJOR_CHANGE: [
            "복수전공", "부전공", "마이크로전공", "전과",
            "제2전공", "융합전공", "전공탐색", "교직",
            "제1전공", "단일전공",
            "방법1", "방법2", "방법3",
            "이수방법1", "이수방법2", "이수방법3",
            "주전공+복수전공", "복수전공 이수학점",
            # 버그 #4 수정 (2026-04-11): "전공 변경" 질문도 MAJOR_CHANGE로 명시 매칭
            "전공 변경", "전공변경", "전공을 변경", "전공 바꾸", "학과 변경",
        ],
        Intent.ALTERNATIVE: [
            "대체", "동일과목", "폐지", "대신",
            "대체과목", "대체가능",
        ],
        Intent.SCHOLARSHIP: [
            "장학금", "장학금 신청", "장학금 자격", "장학금 조건",
            "장학금 신청자격", "장학금 신청기간", "장학금 금액",
            "등록금 지원", "교내장학금", "근로장학금",
            "국가장학금", "외부장학금", "민간장학금",
            "성적우수장학금", "생활비지원", "한국장학재단",
            # Phase 2 Step C (2026-04-12): TA장학 / 교육조교 장학 류 추가.
            # sc03 "TA장학생 선발 기준"이 GENERAL intent로 오분류되어
            # retrieval 단계에서 notice_attachment 필터 미적용되는 문제 해결.
            "TA장학", "TA장학금", "TA장학생", "교육조교",
        ],
        Intent.TRANSCRIPT: [
            "내 성적", "내 학점", "내 평점", "성적표",
            "부족학점", "뭐가 부족", "재수강 추천",
            "수강 가능 학점", "복수전공 현황",
        ],
        Intent.LEAVE_OF_ABSENCE: [
            "휴학", "복학", "휴학 신청", "복학 신청",
            "일반휴학", "군입대휴학", "군입대 휴학",
            "창업휴학", "질병휴학", "출산휴학", "육아휴학",
            "휴학 기간", "복학 기간", "휴학 방법",
            "휴학 서류", "복학 절차", "휴학신청",
            "복학신청", "휴학연장", "휴학 취소", "입대 휴학",
            "전부", "전부(과)", "전학과",
            "재입학", "재입학 신청", "재입학 조건",
            "자퇴", "자퇴 신청", "자퇴 방법", "자퇴하고",
            "제적", "중도이탈", "학적 변동", "학적변동",
            "졸업유보", "졸업유보자",
            "학사학위취득유예", "학위취득유예", "유예자", "학사학위취득유예자",
        ],
    }

    DEPARTMENT_KEYWORDS = [
        # IT·공학
        "컴퓨터공학", "소프트웨어", "빅데이터", "인공지능",
        "스마트융합보안", "스마트에너지", "전자",
        # 어문
        "영어", "일본어", "중국어", "한국어",
        "독일어", "프랑스어", "스페인어", "러시아어",
        "베트남어", "태국어", "미얀마어", "아랍",
        "인도네시아", "인도어", "터키어", "이탈리아어",
        # 사회·경상
        "경영", "경제", "금융", "회계", "무역", "마케팅",
        "관광", "호텔", "항공", "외교", "행정",
        "사회복지", "상담심리", "사이버경찰",
        # 문화·체육
        "영상콘텐츠", "체육", "스포츠", "운동건강",
        # 기타
        "국제개발", "글로벌창업", "비서",
    ]

    LIBERAL_ARTS_KEYWORDS = {
        "인성체험교양": ["채플", "PSC세미나", "사회봉사", "인성체험"],
        "기초교양": ["글쓰기", "독서와토론", "기초교양"],
        "균형교양": ["역사", "철학", "종교", "문학", "문화", "예술", "균형교양"],
        "글로벌소통역량": ["글로벌소통", "College English", "AI플러스"],
    }

    def __init__(self, embedder=None):
        self.glossary = Glossary()
        self._en_mapper = EnTermMapper.get()
        # QuestionType 분류용 임베딩 캐시 (원칙 2: lazy init)
        self._embedder = embedder
        self._qt_config = self._load_qt_config()
        self._qt_ref_cache: dict | None = None  # {type_name: np.ndarray mean_emb}

    def analyze(self, question: str) -> QueryAnalysis:
        lang = detect_language(question)
        if lang == "en":
            return self._analyze_en(question)

        normalized = self.glossary.normalize(question)
        student_groups = self._extract_student_groups(normalized)
        student_id = self._extract_student_id(normalized, student_groups)
        student_type = self._extract_student_type(normalized)
        intent = self._classify_intent(normalized)
        entities = self._extract_entities(normalized)
        if student_groups:
            entities["student_groups"] = student_groups

        # GENERAL도 그래프 활성화 — FAQ 검색은 그래프 경로 내에서 실행되므로
        # GENERAL에서도 FAQ direct_answer를 활용하려면 requires_graph 필요
        requires_graph = intent in (
            Intent.GRADUATION_REQ, Intent.EARLY_GRADUATION,
            Intent.ALTERNATIVE, Intent.SCHEDULE,
            Intent.COURSE_INFO, Intent.MAJOR_CHANGE, Intent.REGISTRATION,
            Intent.SCHOLARSHIP, Intent.LEAVE_OF_ABSENCE,
            Intent.TRANSCRIPT, Intent.GENERAL,
        )
        # ALTERNATIVE(대체/동일과목 등 정의형 질문)도 벡터 검색 필요.
        # 학사안내 PDF p.9에 "대체과목/동일과목 확인하여 재수강" 원문이 있고
        # 그래프 FAQ는 보조적. 벡터를 제외하면 q054 같은 정의 질문을 못 맞춤.
        requires_vector = intent not in (Intent.SCHEDULE,)

        # SCHEDULE이어도 그래프에 없는 정보는 벡터 검색 필요
        _TIMETABLE_KW = ("교시", "야간수업", "시간표", "강의시간")
        if intent == Intent.SCHEDULE and any(kw in normalized for kw in _TIMETABLE_KW):
            requires_vector = True
        # SCHEDULE이어도 성적·제도·요건 등 정책 질문이면 벡터 검색 필요
        _POLICY_KW = ("성적", "제도", "요건", "조건", "규정")
        if intent == Intent.SCHEDULE and any(kw in normalized for kw in _POLICY_KW):
            requires_vector = True
        # SCHEDULE이어도 취소/마감 질문은 등록규칙(p.9) 참조 필요
        if intent == Intent.SCHEDULE and any(kw in normalized for kw in ("취소", "마감")):
            requires_vector = True
        # OCU + 수강신청/기간 질문은 OCU 안내(p.20-23) 벡터 검색 필요
        norm_lower = normalized.lower()
        if "ocu" in norm_lower and any(kw in normalized for kw in ("수강신청", "기간", "신청기간")):
            requires_vector = True

        # 성적선택제도·성적포기제도는 그래프 스키마에 없음 → 그래프 탐색 불필요
        # (그래프 결과 score=1.0이 벡터 결과를 밀어내는 것을 방지)
        _GRADE_SEL_KW = ("성적선택", "성적포기", "Pass", "P/NP", "등급제")
        if any(kw in normalized for kw in _GRADE_SEL_KW):
            requires_graph = False
            requires_vector = True

        # 성적처리기준 질문은 그래프에 노드 있음 → 그래프 탐색 유지
        _GRADE_PROCESS_KW = ("성적처리", "평점산출", "평점계산")
        if any(kw in normalized for kw in _GRADE_PROCESS_KW):
            requires_graph = True
            requires_vector = True

        missing_info = []
        if not student_id and intent in (
            Intent.GRADUATION_REQ, Intent.MAJOR_CHANGE, Intent.REGISTRATION
        ):
            missing_info.append("student_id")

        # 원칙 2: QuestionType 분류 (Embedding 유사도 + Heuristic)
        question_type = self._classify_question_type(
            normalized, entities.get("question_focus"),
        )

        return QueryAnalysis(
            intent=intent,
            student_id=student_id,
            student_type=student_type,
            entities=entities,
            requires_graph=requires_graph,
            requires_vector=requires_vector,
            missing_info=missing_info,
            lang="ko",
            question_type=question_type,
            normalized_query=normalized if normalized != question else None,
        )

    def _analyze_en(self, question: str) -> QueryAnalysis:
        """
        영어 쿼리 분석:
          1. FlashText로 aliases_en → ko 용어 추출
          2. KO 용어로 Intent 분류 (기존 규칙 재사용)
          3. EN 학번 패턴으로 cohort 추출 ("class of 2020", "2020 student")
          4. 키워드 미검출 시 → GENERAL + BGE-M3 시맨틱 fallback
        """
        matched_terms = self._en_mapper.extract(question)
        ko_terms = [t["ko"] for t in matched_terms]

        # KO 용어 문자열로 기존 Intent 분류기 재사용
        ko_text = " ".join(ko_terms) if ko_terms else ""
        intent = self._classify_intent(ko_text) if ko_text else Intent.GENERAL

        # ── 학번 추출 ─────────────────────────────────────────────────────────
        student_id = None
        # 4자리: "class of 2020" / "2020 student|cohort|enrollment|year"
        m = self.EN_COHORT_PATTERN.search(question)
        if m:
            student_id = m.group(1) or m.group(2)
        # Low #9: 2자리 코호트 ("22 cohort" → 2022)
        if student_id is None:
            m_short = self.EN_COHORT_SHORT_PATTERN.search(question)
            if m_short:
                student_id = self._short_to_full_year(m_short.group(1))

        # ── 학생유형 추출 (Medium #6: 미매칭 시 "내국인" 기본값) ───────────────
        student_type = "내국인"
        for stype, pat in self.EN_STUDENT_TYPE_PATTERNS.items():
            if pat.search(question):
                student_type = stype
                break

        q_lower = question.lower()
        entities: dict = {}

        # ── OCU ──────────────────────────────────────────────────────────────
        if any(kw in q_lower for kw in self._EN_OCU_KW):
            entities["ocu"] = True

        # ── GPA exception (직전학기 평점 4.0 이상) ───────────────────────────
        if any(kw in q_lower for kw in self._EN_GPA_KW):
            entities["gpa_exception"] = True

        # ── 장바구니 한도 (period 질문이 아닐 때만) ──────────────────────────
        if any(kw in q_lower for kw in self._EN_BASKET_KW):
            if not any(kw in q_lower for kw in self._EN_PERIOD_KW):
                entities["basket_limit"] = True

        # ── 등록금 납부 기간 ─────────────────────────────────────────────────
        if any(kw in q_lower for kw in self._EN_PAYMENT_KW):
            entities["payment_period"] = True

        # ── 복수전공 이수학점 ────────────────────────────────────────────────
        if any(kw in q_lower for kw in self._EN_2ND_MAJOR_CREDITS_KW):
            entities["second_major_credits"] = True

        # ── 졸업인증 자격 (TOPIK / TOEIC / TOEFL / IELTS) ────────────────────
        for cert_kw in self._EN_GRAD_CERT_KW:
            if cert_kw in q_lower:
                entities["graduation_cert"] = (
                    cert_kw.upper()
                    if cert_kw in ("topik", "toeic", "toefl", "ielts")
                    else cert_kw
                )
                break

        # ── 전공이수방법 (방법1/2/3) ─────────────────────────────────────────
        for en_kw, ko_val in self._EN_MAJOR_METHOD.items():
            if en_kw in q_lower:
                entities["major_method"] = ko_val
                break
        if "major_method" not in entities:
            has_major_ctx = any(kw in q_lower for kw in self._EN_MAJOR_METHOD_CONTEXT_KW)
            if has_major_ctx:
                for en_kw, ko_val in self._EN_MAJOR_METHOD_OPTION.items():
                    if en_kw in q_lower:
                        entities["major_method"] = ko_val
                        break

        # ── 학과/부서 엔티티 추출 (matched_terms 기반) ───────────────────────
        dept_terms = [t["ko"] for t in matched_terms if any(
            kw in t["ko"] for kw in self.DEPARTMENT_KEYWORDS
        )]
        if dept_terms:
            entities["department"] = dept_terms[0]

        # ── Low #10: 과목 코드 추출 (영문 코드 형식 그대로 사용) ──────────────
        m_course = self.COURSE_NUMBER_PATTERN.search(question)
        if m_course:
            entities["course_number"] = m_course.group()

        # ── High #1: URL·사이트 기대 질문 → reranker URL boost ───────────────
        if any(kw in q_lower for kw in self._EN_URL_SEEKING_KW):
            entities["asks_url"] = True

        # ── High #2: 학기 구분 (전기/후기) ──────────────────────────────────
        if any(kw in q_lower for kw in self._EN_SEMESTER_HALF_FIRST):
            entities["semester_half"] = "전기"
        elif any(kw in q_lower for kw in self._EN_SEMESTER_HALF_SECOND):
            entities["semester_half"] = "후기"

        # ── Medium #4: 수강취소 마감 ─────────────────────────────────────────
        if (
            any(kw in q_lower for kw in self._EN_CANCEL_KW)
            and any(kw in q_lower for kw in self._EN_DEADLINE_SUFFIX_KW)
        ):
            entities["registration_deadline"] = True

        # ── Medium #5: 교양영역 ──────────────────────────────────────────────
        for area, kws in self._EN_LIBERAL_ARTS_MAP.items():
            if any(kw in q_lower for kw in kws):
                entities["liberal_arts_area"] = area
                break

        # ── question_focus ────────────────────────────────────────────────────
        # Low #11: table_lookup / rule_list 우선 (구체적 슬롯)
        _has_table = any(kw in q_lower for kw in self._EN_TABLE_KW)
        _has_year  = any(kw in q_lower for kw in ("cohort", "enrollment year", "year of admission"))
        if _has_table and _has_year:
            entities["question_focus"] = "table_lookup"
        elif any(kw in q_lower for kw in self._EN_RULE_LIST_KW):
            entities["question_focus"] = "rule_list"
        elif any(kw in q_lower for kw in self._EN_LIMIT_KW):
            entities["question_focus"] = "limit"
        elif any(kw in q_lower for kw in self._EN_METHOD_KW):
            entities["question_focus"] = "method"
        elif any(kw in q_lower for kw in self._EN_LOCATION_KW):
            entities["question_focus"] = "location"
        elif any(kw in q_lower for kw in self._EN_PERIOD_KW):
            entities["question_focus"] = "period"
        elif any(kw in q_lower for kw in self._EN_ELIGIBILITY_KW):
            entities["question_focus"] = "eligibility"

        if entities.get("question_focus") == "period" and any(
            phrase in q_lower for phrase in (
                "what changed", "changed in", "changed starting",
                "starting from the", "from the 20",
            )
        ):
            entities.pop("question_focus", None)

        # ── EN 기능어 → KO 검색 신호 보강 ─────────────────────────────────────
        # FlashText는 학술 용어 매핑에 강하지만 "application period", "where apply"처럼
        # 검색에 중요한 기능어는 누락될 수 있다. ko_query에만 보강하고 원문 EN은 보존한다.
        extra_ko_terms: list[str] = []
        focus = entities.get("question_focus")
        if focus == "period":
            extra_ko_terms.append("신청기간")
        elif focus == "location":
            extra_ko_terms.append("확인")
        elif focus == "eligibility":
            extra_ko_terms.append("자격")
        elif focus == "rule_list":
            extra_ko_terms.append("기준")

        if "application period" in q_lower or "application window" in q_lower:
            extra_ko_terms.append("신청기간")
        if "where do you apply" in q_lower or "where can i apply" in q_lower:
            extra_ko_terms.append("신청")
        if student_type == "편입생":
            extra_ko_terms.extend(["편입학생", "교육과정 이수방법"])

        if extra_ko_terms:
            seen_terms: set[str] = set()
            enriched_terms: list[str] = []
            for term in ko_terms + extra_ko_terms:
                if term and term not in seen_terms:
                    seen_terms.add(term)
                    enriched_terms.append(term)
            ko_terms = enriched_terms
            ko_text = " ".join(ko_terms)
            intent = self._classify_intent(ko_text) if ko_text else intent

        # ── requires_graph 결정 ───────────────────────────────────────────────
        requires_graph = intent in (
            Intent.GRADUATION_REQ, Intent.EARLY_GRADUATION,
            Intent.ALTERNATIVE, Intent.SCHEDULE,
            Intent.COURSE_INFO, Intent.MAJOR_CHANGE, Intent.REGISTRATION,
            Intent.SCHOLARSHIP, Intent.LEAVE_OF_ABSENCE,
            Intent.TRANSCRIPT,  # Low #12: TRANSCRIPT 추가
            Intent.GENERAL,
        )
        # High #3: 성적선택제(P/NP) / 성적포기 → REGISTRATION + 그래프 OFF
        # EN "pass/fail conversion" → 글로서리에서 "성적평가 선택제도" 추출 →
        # "성적평가"가 GRADUATION_REQ에 먼저 매칭되는 오분류 수정
        if any(kw in q_lower for kw in self._EN_GRADE_SEL_KW):
            intent = Intent.REGISTRATION
            requires_graph = entities.get("question_focus") == "period"

        # EN은 항상 vector 검색 (BGE-M3 크로스링구얼)
        requires_vector = True

        # Medium #7: missing_info — 학번 필요 intent에서 student_id 없으면 표시
        missing_info = []
        if not student_id and intent in (
            Intent.GRADUATION_REQ, Intent.MAJOR_CHANGE, Intent.REGISTRATION
        ):
            missing_info.append("student_id")

        # EN 쿼리도 QuestionType 분류 (KO 용어 기반)
        qt_text = ko_text if ko_text else question
        question_type = self._classify_question_type(qt_text)

        logger.debug(
            "EN query analyzed: intent=%s, qt=%s, student_type=%s, matched=%s",
            intent.value, question_type.value, student_type,
            [t["ko"] for t in matched_terms],
        )

        return QueryAnalysis(
            intent=intent,
            student_id=student_id,
            student_type=student_type,
            entities=entities,
            requires_graph=requires_graph,
            requires_vector=requires_vector,
            missing_info=missing_info,
            lang="en",
            question_type=question_type,
            matched_terms=matched_terms,
            ko_query=ko_text if ko_text else None,
        )

    @staticmethod
    def _short_to_full_year(short: str) -> str:
        """2자리 학번 → 4자리 연도 변환 (22 → 2022, 17 → 2017)"""
        return f"20{short}"

    def _extract_student_id(
        self, text: str, student_groups: Optional[list] = None
    ) -> Optional[str]:
        match = self.STUDENT_ID_PATTERN.search(text)
        if match:
            return match.group(1)

        # 2자리 학번 처리 (22학번 → 2022)
        short_match = self.STUDENT_ID_SHORT_PATTERN.search(text)
        if short_match:
            return self._short_to_full_year(short_match.group(1))

        range_match = self.STUDENT_ID_RANGE_PATTERN.search(text)
        if range_match:
            return range_match.group(1)

        bound_match = self.STUDENT_ID_BOUND_PATTERN.search(text)
        if bound_match:
            return bound_match.group(1)

        if student_groups:
            first_group = student_groups[0]
            if first_group == "2024_2025":
                return "2024"
            if first_group == "2017_2020":
                return "2017"
            if first_group == "2016_before":
                return "2016"
            return first_group

        return None

    def _extract_student_groups(self, text: str) -> list[str]:
        groups = []

        def add(group: str) -> None:
            if group not in groups:
                groups.append(group)

        range_match = self.STUDENT_ID_RANGE_PATTERN.search(text)
        if range_match:
            start = range_match.group(1)
            end = range_match.group(2)
            if start == "2024" and end == "2025":
                add("2024_2025")
            else:
                add(self._year_to_group(start))
                add(self._year_to_group(end))

        for year, bound in self.STUDENT_ID_BOUND_PATTERN.findall(text):
            if bound == "이후":
                add(self._year_to_group(year))
            elif bound == "이전":
                add(year)

        for year in re.findall(r"(20[12]\d)학번", text):
            add(self._year_to_group(year))

        # 2자리 학번 처리 (22학번 → 2022 → 그룹 매핑)
        for short in re.findall(r"\b([12]\d)학번", text):
            full_year = self._short_to_full_year(short)
            add(self._year_to_group(full_year))

        return groups

    @staticmethod
    def _year_to_group(year: str) -> str:
        return get_student_group(year)

    def _extract_student_type(self, text: str) -> Optional[str]:
        for stype, pattern in self.STUDENT_TYPE_PATTERNS.items():
            if pattern.search(text):
                return stype
        return "내국인"

    # 기간/일정 관련 키워드 (wrong_slot 방지용)
    _PERIOD_KW = ("언제", "기간", "일정", "날짜", "날", "일자", "시작", "종료", "마감", "신청일", "며칠")
    # 한도/수치 관련 키워드
    _LIMIT_KW  = ("최대", "얼마", "몇 학점", "한도", "제한", "이수 가능", "신청 가능", "수강 가능")

    # Phase 2 Step B (2026-04-12): URL·사이트 답변을 기대하는 질문 감지 키워드.
    # 매칭 시 `entities["asks_url"] = True` → reranker가 URL 포함 청크에 가산점 부여.
    # 대상 문항: c01 (sugang.bufs.ac.kr), sc01 (kosaf.go.kr), l01 (학생포털 URL) 등.
    # 단순 substring 기반: BUFS 챗봇 도메인에서 "어디서/어디에서"는 거의 항상
    # 사이트·URL·기관 질문이므로 과분류 위험이 낮다. 하드코딩이 아니라 단일 테이블.
    _URL_SEEKING_KWS = ("어디서", "어디에서", "어느 사이트", "어느 페이지", "어느 홈페이지",
                        "홈페이지 주소", "어느 기관", "신청 기관", "신청기관", "신청사이트",
                        "신청 사이트", "접속 주소")

    def _classify_intent(self, text: str) -> Intent:
        # 조기졸업은 매우 구체적인 복합어 → glossary 정규화 후에도 남아있으면 우선 처리
        # (SCHEDULE "기간"+"언제" 등에 밀리지 않도록 조기 리턴)
        if "조기졸업" in text:
            return Intent.EARLY_GRADUATION

        # "장바구니 기간/언제" → SCHEDULE로 보내야 함 (REGISTRATION에 잡히면 안 됨)
        if "장바구니" in text and any(kw in text for kw in self._PERIOD_KW):
            return Intent.SCHEDULE

        if (
            any(kw in text for kw in ("직전학기", "평점 4.0", "학점이월", "재수강", "장바구니"))
            or ("ocu" in text and any(kw in text for kw in ("납부", "사용료", "출석", "id")))
        ):
            return Intent.REGISTRATION

        # 학사경고 + 학점/수강신청 → REGISTRATION (SCHEDULE에 빠지지 않도록)
        if "학사경고" in text and any(kw in text for kw in ("학점", "수강신청", "몇", "줄어")):
            return Intent.REGISTRATION

        # 대체과목/동일과목 질문 → ALTERNATIVE (REGISTRATION에 빠지지 않도록)
        if any(kw in text for kw in ("대체과목", "동일과목")):
            return Intent.ALTERNATIVE

        # 이수구분 변경 → REGISTRATION
        if "이수구분" in text:
            return Intent.REGISTRATION

        # 과목명 중복/동일 수강 → REGISTRATION
        if "과목명" in text and any(kw in text for kw in ("동일", "같은", "중복", "코드", "다르")):
            return Intent.REGISTRATION

        # 계절학기 + 날짜/기간 질문 → SCHEDULE (성적확정 언제, 기간 등)
        if "계절학기" in text and any(kw in text for kw in ("기간", "언제", "일정", "확정", "성적확정")):
            return Intent.SCHEDULE

        # 계절학기 질문 (방법/자격/수강 등) → REGISTRATION
        if "계절학기" in text:
            return Intent.REGISTRATION

        # 졸업유보/유예 + 수강/등록금 → REGISTRATION
        if any(kw in text for kw in ("졸업유보", "학사학위취득유예", "유예")) and any(
            kw in text for kw in ("수강", "등록금", "학점")
        ):
            return Intent.REGISTRATION

        # 증명서/발급 → GENERAL (전용 처리)
        if any(kw in text for kw in ("증명서", "발급", "재학증명", "성적증명", "휴학증명")):
            return Intent.GENERAL


        if (
            any(kw in text for kw in ("전과", "제1·2전공", "제1,2전공", "제2전공"))
            and any(kw in text for kw in ("기간", "언제", "일정", "마감"))
        ):
            return Intent.SCHEDULE

        # 수강신청 + 기간 질문 → SCHEDULE (언제/기간을 묻는 것이지 방법을 묻는 게 아님)
        # 단, "방법", "어떻게", "어디서", "사이트" 등 방법 질문은 REGISTRATION 유지
        _REG_METHOD_KW = ("방법", "어떻게", "어디서", "사이트", "주소", "id", "로그인",
                          "취소하고", "취소 후", "재신청", "초과", "복학생", "처리")
        if (
            "수강신청" in text
            and any(kw in text for kw in ("기간", "언제", "시작", "마감", "까지", "일자", "스케줄", "날"))
            and not any(kw in text for kw in _REG_METHOD_KW)
        ):
            return Intent.SCHEDULE

        # 전공이수방법(방법1/2/3) + 학점 질문 → MAJOR_CHANGE
        if any(kw in text for kw in ("방법1", "방법2", "방법3", "이수방법1", "이수방법2", "이수방법3")):
            return Intent.MAJOR_CHANGE

        # 전과(학과 변경) 질문 → MAJOR_CHANGE (LEAVE_OF_ABSENCE와 혼동 방지)
        if any(kw in text for kw in ("전과", "전공변경", "학과 변경", "학과변경")) and not any(
            kw in text for kw in ("휴학", "복학", "자퇴", "제적")
        ):
            return Intent.MAJOR_CHANGE

        scores = {}
        for intent, keywords in self.INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scores[intent] = score

        if not scores:
            return Intent.GENERAL

        # 학적변동 관련 키워드는 SCHEDULE의 "기간" 등에 잡히지 않도록 우선 처리
        _LOA_KW = ("휴학", "복학", "전부(과)", "재입학", "자퇴", "제적", "학적변동",
                   "졸업유보", "학사학위취득유예", "학위취득유예", "유예자")
        if any(kw in text for kw in _LOA_KW) and not any(
            kw in text for kw in ("조기졸업", "수강신청", "수업")
        ):
            return Intent.LEAVE_OF_ABSENCE

        # 동적 타이브레이커: question_focus(period/limit)로 동점 해소
        # 원칙 4: 하드코딩 priority 대신 질문 분석 결과로 동적 결정
        is_period = any(kw in text for kw in self._PERIOD_KW)
        is_limit = any(kw in text for kw in self._LIMIT_KW)

        max_score = max(scores.values())
        top = [i for i, s in scores.items() if s == max_score]

        focus_is_method = any(kw in text for kw in ("어떻게", "방법", "절차"))
        if len(top) > 1:
            if is_period and Intent.SCHEDULE in top:
                return Intent.SCHEDULE
            if is_limit:
                for candidate in (Intent.REGISTRATION, Intent.GRADUATION_REQ):
                    if candidate in top:
                        return candidate
            # method focus: 휴학/복학/전과 방법 질문 → LEAVE_OF_ABSENCE
            if focus_is_method and Intent.LEAVE_OF_ABSENCE in top:
                return Intent.LEAVE_OF_ABSENCE

        # fallback: 최후 수단으로만 사용
        priority = [
            Intent.ALTERNATIVE, Intent.EARLY_GRADUATION, Intent.GRADUATION_REQ,
            Intent.REGISTRATION, Intent.SCHEDULE,
            Intent.MAJOR_CHANGE, Intent.COURSE_INFO, Intent.SCHOLARSHIP,
            Intent.LEAVE_OF_ABSENCE, Intent.TRANSCRIPT,
        ]
        for p in priority:
            if p in top:
                return p
        return top[0]

    def _extract_entities(self, text: str) -> dict:
        entities = {}

        for dept in self.DEPARTMENT_KEYWORDS:
            if dept in text:
                entities["department"] = dept
                break

        m = self.COURSE_NUMBER_PATTERN.search(text)
        if m:
            entities["course_number"] = m.group()

        # course_name 추출 (대체과목/과목정보 앞 명사)
        for pattern in (self.COURSE_NAME_BEFORE_KW, self.COURSE_NAME_IN_CONTEXT):
            m_course = pattern.search(text)
            if m_course:
                candidate = m_course.group(1).strip()
                if candidate not in self._NON_COURSE_WORDS and len(candidate) >= 2:
                    entities["course_name"] = candidate
                    break

        for area, keywords in self.LIBERAL_ARTS_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                entities["liberal_arts_area"] = area
                break

        m2 = re.search(r"방법\s*([123])", text)
        if m2:
            entities["major_method"] = f"방법{m2.group(1)}"

        if "장바구니" in text and not any(kw in text for kw in self._PERIOD_KW):
            entities["basket_limit"] = True

        if "직전학기" in text or "평점 4.0" in text:
            entities["gpa_exception"] = True

        if "취소" in text and any(kw in text for kw in ("언제까지", "마감", "까지")):
            entities["registration_deadline"] = True

        if "ocu" in text:
            entities["ocu"] = True

        if "납부" in text and "기간" in text:
            entities["payment_period"] = True

        if "복수전공" in text and "이수학점" in text:
            entities["second_major_credits"] = True

        if "topik" in text.lower():
            entities["graduation_cert"] = "TOPIK"

        # 질문 슬롯 유형 감지 (답변 생성 힌트 + intent 라우팅용)
        # 원칙 4: 하드코딩 priority 대신 question_focus로 동적 라우팅
        _METHOD_KW_FOCUS  = ("어떻게", "방법", "절차", "서류", "어디서")
        _LOCATION_KW      = ("어디", "어디서", "장소", "건물")
        _ELIGIBILITY_KW   = ("가능한가", "가능한지", "되나요", "자격", "조건", "요건")

        # table_lookup: 학번/년도 + 수치 질문 → 표 데이터 추출 전용
        _TABLE_KW = ("이론", "실습", "이수과목", "이수학점표")
        _YEAR_KW = ("학번", "학년도", "입학")
        _has_table_kw = any(kw in text for kw in _TABLE_KW)
        _has_year_kw = any(kw in text for kw in _YEAR_KW)

        # rule_list: 자격 요건/조건 나열
        _RULE_LIST_KW = ("요건", "자격요건", "이수요건", "조건은", "자격은", "기준은")

        if _has_table_kw and _has_year_kw:
            entities["question_focus"] = "table_lookup"
        elif any(kw in text for kw in _RULE_LIST_KW):
            entities["question_focus"] = "rule_list"
        elif any(kw in text for kw in self._PERIOD_KW):
            entities["question_focus"] = "period"
        elif any(kw in text for kw in self._LIMIT_KW):
            entities["question_focus"] = "limit"
        elif any(kw in text for kw in _METHOD_KW_FOCUS):
            entities["question_focus"] = "method"
        elif any(kw in text for kw in _LOCATION_KW):
            entities["question_focus"] = "location"
        elif any(kw in text for kw in _ELIGIBILITY_KW):
            entities["question_focus"] = "eligibility"

        # 버그 #4 수정 (2026-04-11): 학기 구분자(전기/후기) 추출
        # s04 "2025학년도 **전기** 학위수여식" 같은 질문에서 구별자 entity 생성.
        # 다운스트림(direct_answer aligns Keyword Anchor Gate)에서 활용됨.
        if "전기" in text:
            entities["semester_half"] = "전기"
        elif "후기" in text:
            entities["semester_half"] = "후기"

        # Phase 2 Step B (2026-04-12): URL-aware boost 신호.
        # "어디서/어디에서/어느 사이트/신청 기관" 등 URL·사이트 답변을 기대하는 질문은
        # retrieved 청크 중 URL을 포함한 것에 가산점을 부여해
        # c01(sugang URL), sc01(kosaf URL) 같은 문항을 복구.
        # reranker에서 analysis.entities.get("asks_url")을 확인해 활용.
        if any(kw in text for kw in self._URL_SEEKING_KWS):
            entities["asks_url"] = True

        return entities

    # ── QuestionType 분류 ─────────────────────────────────────
    # 원칙 2: Embedding cosine similarity + heuristic fallback
    # 원칙 4: reference phrases를 YAML로 관리 (config/question_types.yaml)

    @staticmethod
    def _load_qt_config() -> dict:
        """question_types.yaml 로드."""
        if not _QT_YAML.exists():
            return {}
        with _QT_YAML.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _build_qt_ref_embeddings(self) -> dict:
        """Reference phrase 임베딩을 생성하고 캐시합니다.

        원칙 2: 앱 시작 후 첫 호출 시 1회만 계산, 이후 재사용.
        각 type별 reference phrases를 임베딩 → 평균 벡터로 요약.
        """
        if self._qt_ref_cache is not None:
            return self._qt_ref_cache

        qt_defs = self._qt_config.get("question_types", {})
        if not qt_defs or not self._embedder:
            self._qt_ref_cache = {}
            return self._qt_ref_cache

        cache: dict = {}
        for type_name, type_def in qt_defs.items():
            refs = type_def.get("references", [])
            if not refs or type_name not in _QT_NAME_TO_ENUM:
                continue
            # Batch embed reference phrases
            embeddings = self._embedder.embed_passages_batch(refs)
            # Mean pooling → single representative vector
            mean_emb = np.mean(embeddings, axis=0)
            # L2 normalize
            norm = np.linalg.norm(mean_emb)
            if norm > 0:
                mean_emb = mean_emb / norm
            cache[type_name] = mean_emb

        self._qt_ref_cache = cache
        logger.info(
            "QuestionType reference embeddings 생성: %s",
            list(cache.keys()),
        )
        return cache

    def _classify_question_type(
        self, text: str, question_focus: str | None = None,
    ) -> QuestionType:
        """Embedding 유사도 + heuristic으로 질문 유형을 분류합니다.

        분류 순서:
        1. 짧은 쿼리 + 질문어 없음 → OVERVIEW (최우선, 임베딩보다 앞)
        2. 조건문 패턴("~하면", "~면") → REASONING
        3. question_focus → QuestionType 매핑
        4. Embedding cosine similarity
        5. Heuristic 기본값
        """
        max_tokens = (
            self._qt_config
            .get("classification", {})
            .get("overview_max_tokens", 4)
        )
        tokens = [t for t in text.split() if len(t) > 0]
        is_short = len(tokens) <= max_tokens or len(text) <= 8
        has_question_word = any(qw in text for qw in _QUESTION_WORDS)

        # ── 1단계: OVERVIEW (짧은 토픽 쿼리 — 최우선) ──────
        if is_short and not has_question_word:
            return QuestionType.OVERVIEW

        # ── 2단계: 조건문 패턴 → REASONING ────────────────────
        _CONDITIONAL_PATTERNS = ("하면", "으면", "면서", "경우", "때", "중에")
        if any(p in text for p in _CONDITIONAL_PATTERNS) and has_question_word:
            return QuestionType.REASONING

        # ── 3단계: question_focus 연계 ────────────────────────
        _FOCUS_MAP = {
            "period": QuestionType.FACTOID,
            "limit": QuestionType.FACTOID,
            "table_lookup": QuestionType.FACTOID,
            "rule_list": QuestionType.FACTOID,
            "method": QuestionType.PROCEDURAL,
            "location": QuestionType.FACTOID,
            "eligibility": QuestionType.REASONING,
        }
        if question_focus and question_focus in _FOCUS_MAP:
            return _FOCUS_MAP[question_focus]

        # ── 4단계: Embedding 유사도 ──────────────────────────
        ref_cache = self._build_qt_ref_embeddings()
        if ref_cache and self._embedder:
            q_emb = self._embedder.embed_query(text)
            q_norm = np.linalg.norm(q_emb)
            if q_norm > 0:
                q_emb = q_emb / q_norm

            best_type = None
            best_sim = -1.0
            for type_name, ref_emb in ref_cache.items():
                sim = float(np.dot(q_emb, ref_emb))
                if sim > best_sim:
                    best_sim = sim
                    best_type = type_name

            threshold = (
                self._qt_config
                .get("classification", {})
                .get("similarity_threshold", 0.35)
            )
            if best_type and best_sim >= threshold:
                logger.debug(
                    "QuestionType(embed): %s (sim=%.3f) for '%s'",
                    best_type, best_sim, text[:30],
                )
                return _QT_NAME_TO_ENUM.get(best_type, QuestionType.FACTOID)

        # ── 5단계: 기본값 ────────────────────────────────────
        return QuestionType.FACTOID
