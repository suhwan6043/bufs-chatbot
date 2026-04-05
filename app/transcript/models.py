"""
성적표 데이터 모델.

⚠️ StudentProfile.성명 / 학번은 PII입니다.
   LLM·로그 전달 시 반드시 PIIRedactor를 거쳐야 합니다.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CourseRecord:
    """개별 이수 과목."""
    category: str = ""          # 섹션 헤더: "주전공", "복수전공", "균형교양" 등
    이수구분: str = ""          # 약어: "전본", "복전", "자선" 등
    교과목번호: str = ""        # "EIT101", "CIS107" 등
    교과목명: str = ""
    이수학기: str = ""          # "2025/1"
    학점: float = 0.0
    성적: str = ""              # "A+", "P", "NP", "" (미확정)
    is_retake: bool = False     # [재] 마커 여부


@dataclass
class CreditCategory:
    """학점 요약표의 개별 카테고리."""
    name: str = ""              # "교양_인성_채플", "전공_기본", "총계" 등
    졸업기준: float = 0.0
    취득학점: float = 0.0
    부족학점: float = 0.0


@dataclass
class CreditsSummary:
    """학점 요약표 전체 (XLS 4-9행)."""
    categories: list[CreditCategory] = field(default_factory=list)
    평점평균: float = 0.0
    졸업시험: dict = field(default_factory=dict)    # {"주전공": "N", "복수전공": "N"}
    졸업인증: dict = field(default_factory=dict)    # {"기업정신": "Y", "사회봉사": "Y"}
    총_졸업기준: float = 0.0
    총_취득학점: float = 0.0
    총_부족학점: float = 0.0
    신청학점: float = 0.0


@dataclass
class StudentProfile:
    """학생 인적정보 (XLS 2-3행)."""
    학번: str = ""              # "20201877" ⚠️ PII
    입학연도: str = ""          # "2020" (학번 앞 4자리)
    student_group: str = ""     # "2017_2020" (get_student_group()으로 매핑)
    학부과: str = ""            # "소프트웨어학부"
    전공: str = ""              # "소프트웨어전공"
    학년: int = 0
    이수학기: int = 0
    성명: str = ""              # ⚠️ PII — LLM 전송 시 반드시 제거
    복수전공: str = ""
    부전공: str = ""
    학생설계복수전공: str = ""
    융합모듈: str = ""          # 마이크로전공
    내외국인: str = "내국인"
    student_type: str = "내국인"  # "내국인"/"외국인"/"편입생"
    학적상태: str = ""          # "재학", "휴학" 등
    취커합격: str = ""          # "Y"/"N"
    교직: str = ""              # "해당없음" 등
    교직상태: str = ""


@dataclass
class StudentAcademicProfile:
    """파싱된 성적표 전체 — 세션 레벨 중심 데이터 구조."""
    profile: StudentProfile = field(default_factory=StudentProfile)
    credits: CreditsSummary = field(default_factory=CreditsSummary)
    courses: list[CourseRecord] = field(default_factory=list)
    source_filename: str = ""
    parse_timestamp: str = ""   # ISO format
    version: int = 1
    extra_fields: dict = field(default_factory=dict)  # 마커 파싱 시 미지 필드 자동 포착
    _masked_name: str = ""      # 마스킹된 이름 (store() 시 설정, 원본은 삭제)
