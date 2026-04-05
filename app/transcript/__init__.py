"""
성적표 파싱·분석·보안 패키지.

학업성적사정표(.xls)를 파싱하여 구조화된 학생 프로필을 생성하고,
기존 RAG 파이프라인과 결합하여 맞춤형 학사 상담을 제공합니다.

⚠️ 개인정보 보안:
  - 모든 데이터 접근은 SecureTranscriptStore를 통해서만 수행
  - LLM 전달 시 PIIRedactor로 이름·학번 제거
  - TTL 30분 후 자동 파기
"""

from .models import (
    StudentAcademicProfile,
    StudentProfile,
    CourseRecord,
    CreditCategory,
    CreditsSummary,
)
from .parser import TranscriptParser
from .analyzer import TranscriptAnalyzer
from .version_manager import TranscriptVersionManager
from .security import SecureTranscriptStore, PIIRedactor, UploadValidator, audit_log

__all__ = [
    "StudentAcademicProfile",
    "StudentProfile",
    "CourseRecord",
    "CreditCategory",
    "CreditsSummary",
    "TranscriptParser",
    "TranscriptAnalyzer",
    "TranscriptVersionManager",
    "SecureTranscriptStore",
    "PIIRedactor",
    "UploadValidator",
    "audit_log",
]
