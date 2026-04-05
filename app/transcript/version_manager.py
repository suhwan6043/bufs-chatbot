"""
성적표 버전 관리 (세션 내 한정).

원칙 3: 증분 업데이트 — 업로드마다 스냅샷, diff 감지.
⚠️ 디스크 저장 절대 금지. 세션 종료 시 자동 소멸.
"""

from datetime import datetime
from typing import Optional

from .models import StudentAcademicProfile
from .security import SecureTranscriptStore


class TranscriptVersionManager:
    """세션 내 성적표 버전 히스토리."""

    @staticmethod
    def create_snapshot(profile: StudentAcademicProfile) -> dict:
        """직렬화 가능한 스냅샷 생성 (PII 최소화)."""
        return {
            "version": profile.version,
            "timestamp": datetime.now().isoformat(),
            "총_취득학점": profile.credits.총_취득학점,
            "총_부족학점": profile.credits.총_부족학점,
            "평점평균": profile.credits.평점평균,
            "과목수": len(profile.courses),
            "입학연도": profile.profile.입학연도,
        }

    @staticmethod
    def detect_diff(
        old: StudentAcademicProfile,
        new: StudentAcademicProfile,
    ) -> dict:
        """
        두 성적표 비교. 변경 항목 dict 반환.

        반환 예:
            {
                "총_취득학점": {"old": 120.5, "new": 126.5},
                "평점평균": {"old": 3.85, "new": 3.97},
                "신규과목": ["운영체제", "데이터베이스"],
                "성적변경": [{"과목": "프로그래밍입문", "old": "C+", "new": "B+"}],
            }
        """
        diff = {}

        # 학점 변화
        if old.credits.총_취득학점 != new.credits.총_취득학점:
            diff["총_취득학점"] = {
                "old": old.credits.총_취득학점,
                "new": new.credits.총_취득학점,
            }

        # 평점 변화
        if old.credits.평점평균 != new.credits.평점평균:
            diff["평점평균"] = {
                "old": old.credits.평점평균,
                "new": new.credits.평점평균,
            }

        # 과목 비교 (교과목번호 + 이수학기 기준)
        old_courses = {
            (c.교과목번호, c.이수학기): c for c in old.courses
        }
        new_courses = {
            (c.교과목번호, c.이수학기): c for c in new.courses
        }

        # 신규 과목
        new_keys = set(new_courses.keys()) - set(old_courses.keys())
        if new_keys:
            diff["신규과목"] = [new_courses[k].교과목명 for k in new_keys]

        # 성적 변경
        grade_changes = []
        for key in set(old_courses.keys()) & set(new_courses.keys()):
            oc = old_courses[key]
            nc = new_courses[key]
            if oc.성적 != nc.성적 and nc.성적:
                grade_changes.append({
                    "과목": nc.교과목명,
                    "old": oc.성적 or "(미확정)",
                    "new": nc.성적,
                })
        if grade_changes:
            diff["성적변경"] = grade_changes

        return diff

    @staticmethod
    def store_snapshot(profile: StudentAcademicProfile, session_state=None) -> None:
        """세션 스토어에 버전 스냅샷 추��� (동의 상태 확인)."""
        if session_state is None:
            return
        if not SecureTranscriptStore.has_consent(session_state):
            return

        key = SecureTranscriptStore._KEY_VERSIONS
        versions = session_state.get(key, [])
        snapshot = TranscriptVersionManager.create_snapshot(profile)
        versions.append(snapshot)

        # 최대 10개 버전만 유지
        if len(versions) > 10:
            versions = versions[-10:]

        session_state[key] = versions

    @staticmethod
    def get_version_history(session_state) -> list[dict]:
        """저장된 버전 히스토리 반환."""
        return session_state.get(SecureTranscriptStore._KEY_VERSIONS, [])
