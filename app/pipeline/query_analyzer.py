"""
쿼리 분석기 - 규칙 기반 Intent 분류 + 엔티티 추출
LLM 호출 없이 정규식 + 키워드 매칭으로 <5ms 처리
JSX 스키마 기준 9 Intent, 학번 그룹/학생유형/과목명 엔티티 추출
"""

import re
import logging
from typing import Optional

from app.models import Intent, QueryAnalysis
from app.pipeline.glossary import Glossary

logger = logging.getLogger(__name__)


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

    STUDENT_TYPE_PATTERNS = {
        "외국인": re.compile(r"외국인|유학생|외국인학생"),
        "편입생": re.compile(r"편입생?|편입학"),
    }

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
        Intent.GRADUATION_REQ: [
            "졸업", "졸업요건", "졸업학점", "이수학점",
            "몇 학점", "교양", "전공학점", "글로벌소통역량",
            "취업커뮤니티", "NOMAD", "졸업인증", "졸업시험",
            "학점인정", "선이수", "인정",
        ],
        Intent.REGISTRATION: [
            "수강신청", "수강", "재수강", "학점이월",
            "최대학점", "신청학점", "취소", "최대신청",
            "한국열린사이버대학교", "OCU", "장바구니", "납부",
            "수강신청 정정", "수강정정", "공인결석계",
            "이수 가능", "신청 가능", "수강 가능",
            "이수구분",
            # 성적선택제도 (A~F / P/NP 선택 신청)
            "성적선택", "성적포기", "Pass", "P/NP", "등급제",
        ],
        Intent.SCHEDULE: [
            "언제", "기간", "일정", "마감", "시작일", "종료일",
            "중간고사", "기말고사", "개강", "종강", "방학",
            "수강취소", "수업일수", "학사일정",
        ],
        Intent.COURSE_INFO: [
            "과목", "교과목", "수업", "강의",
            "개설", "강좌", "온라인", "대면", "플립",
        ],
        Intent.MAJOR_CHANGE: [
            "복수전공", "부전공", "마이크로전공", "전과",
            "제2전공", "융합전공", "전공탐색", "교직",
        ],
        Intent.ALTERNATIVE: [
            "대체", "동일과목", "폐지", "변경", "대신",
            "대체과목", "대체가능",
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

    def __init__(self):
        self.glossary = Glossary()

    def analyze(self, question: str) -> QueryAnalysis:
        normalized = self.glossary.normalize(question)
        student_groups = self._extract_student_groups(normalized)
        student_id = self._extract_student_id(normalized, student_groups)
        student_type = self._extract_student_type(normalized)
        intent = self._classify_intent(normalized)
        entities = self._extract_entities(normalized)
        if student_groups:
            entities["student_groups"] = student_groups

        requires_graph = intent in (
            Intent.GRADUATION_REQ, Intent.ALTERNATIVE, Intent.SCHEDULE,
            Intent.COURSE_INFO, Intent.MAJOR_CHANGE, Intent.REGISTRATION,
        )
        requires_vector = intent not in (Intent.SCHEDULE, Intent.ALTERNATIVE)

        # SCHEDULE이어도 그래프에 없는 정보는 벡터 검색 필요
        _TIMETABLE_KW = ("교시", "야간수업", "시간표", "강의시간")
        if intent == Intent.SCHEDULE and any(kw in normalized for kw in _TIMETABLE_KW):
            requires_vector = True
        # SCHEDULE이어도 성적·제도·요건 등 정책 질문이면 벡터 검색 필요
        _POLICY_KW = ("성적", "제도", "요건", "조건", "규정")
        if intent == Intent.SCHEDULE and any(kw in normalized for kw in _POLICY_KW):
            requires_vector = True

        # 성적선택제도·성적포기제도는 그래프 스키마에 없음 → 그래프 탐색 불필요
        # (그래프 결과 score=1.0이 벡터 결과를 밀어내는 것을 방지)
        _GRADE_SEL_KW = ("성적선택", "성적포기", "Pass", "P/NP", "등급제")
        if any(kw in normalized for kw in _GRADE_SEL_KW):
            requires_graph = False
            requires_vector = True

        missing_info = []
        if not student_id and intent in (
            Intent.GRADUATION_REQ, Intent.MAJOR_CHANGE, Intent.REGISTRATION
        ):
            missing_info.append("student_id")

        return QueryAnalysis(
            intent=intent,
            student_id=student_id,
            student_type=student_type,
            entities=entities,
            requires_graph=requires_graph,
            requires_vector=requires_vector,
            missing_info=missing_info,
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
        value = int(year)
        if value >= 2024:
            return "2024_2025"
        if value == 2023:
            return "2023"
        if value == 2022:
            return "2022"
        if value == 2021:
            return "2021"
        if value >= 2017:
            return "2017_2020"
        return "2016_before"

    def _extract_student_type(self, text: str) -> Optional[str]:
        for stype, pattern in self.STUDENT_TYPE_PATTERNS.items():
            if pattern.search(text):
                return stype
        return "내국인"

    # 기간/일정 관련 키워드 (wrong_slot 방지용)
    _PERIOD_KW = ("언제", "기간", "일정", "날짜", "시작", "종료", "마감", "부터", "까지")
    # 한도/수치 관련 키워드
    _LIMIT_KW  = ("최대", "얼마", "몇 학점", "한도", "제한", "이수 가능", "신청 가능", "수강 가능")

    def _classify_intent(self, text: str) -> Intent:
        if (
            any(kw in text for kw in ("직전학기", "평점 4.0", "학점이월", "재수강", "장바구니"))
            or ("ocu" in text and any(kw in text for kw in ("납부", "사용료", "출석", "id")))
        ):
            return Intent.REGISTRATION


        if (
            any(kw in text for kw in ("전과", "제1·2전공", "제1,2전공", "제2전공"))
            and any(kw in text for kw in ("기간", "언제", "일정", "마감"))
        ):
            return Intent.SCHEDULE

        scores = {}
        for intent, keywords in self.INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scores[intent] = score

        if not scores:
            return Intent.GENERAL

        priority = [
            Intent.ALTERNATIVE, Intent.GRADUATION_REQ,
            Intent.REGISTRATION, Intent.SCHEDULE,
            Intent.MAJOR_CHANGE, Intent.COURSE_INFO,
        ]
        max_score = max(scores.values())
        top = [i for i, s in scores.items() if s == max_score]
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

        # 질문 슬롯 유형 감지 (답변 생성 힌트용)
        if any(kw in text for kw in self._PERIOD_KW):
            entities["question_focus"] = "period"
        elif any(kw in text for kw in self._LIMIT_KW):
            entities["question_focus"] = "limit"

        return entities
