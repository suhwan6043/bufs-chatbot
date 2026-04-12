"""
성적표 파서 + 보안 모듈 테스트.
실제 XLS 파일(20260404205953601.xls)이 있으면 통합 테스트도 실행.
"""

import time
from pathlib import Path

import pytest

from app.transcript.models import (
    CourseRecord,
    CreditCategory,
    CreditsSummary,
    StudentAcademicProfile,
    StudentProfile,
)
from app.transcript.security import (
    PIIRedactor,
    SecureTranscriptStore,
    UploadValidator,
)
from app.transcript.version_manager import TranscriptVersionManager


# ── 실제 XLS 경로 ──
_XLS_PATH = Path("C:/Users/User/Downloads/20260404205953601.xls")


# ══════════════════════════════════════════════════════
# UploadValidator 테스트
# ══════════════════════════════════════════════════════

class TestUploadValidator:
    def test_empty_file(self):
        ok, err = UploadValidator.validate(b"", "test.xls")
        assert not ok
        assert "빈 파일" in err

    def test_wrong_extension(self):
        """진짜 허용되지 않는 확장자(.exe 등)는 거부.

        주의: .pdf, .docx 등은 다중 포맷 지원 이후 허용 확장자가 되었으므로
        거부 케이스 테스트에 부적합. `_ALLOWED_EXTENSIONS` 참조.
        """
        ok, err = UploadValidator.validate(b"\xd0\xcf\x11\xe0" + b"\x00" * 100, "test.exe")
        assert not ok
        assert "지원하지 않는" in err or "지원" in err

    def test_oversized_file(self):
        """MAX_SIZE_MB 초과 파일은 거부. MAX_SIZE_MB=200 기준 201MB 테스트."""
        # 매우 큰 파일 생성은 메모리 낭비. 검증 로직 자체는 크기 계산만 하므로
        # bytearray로 최소 비용 생성.
        from app.transcript.security import UploadValidator as UV
        max_mb = UV.MAX_SIZE_MB
        big_size = int((max_mb + 1) * 1024 * 1024)
        big = b"\xd0\xcf\x11\xe0" + b"\x00" * (big_size - 4)
        ok, err = UploadValidator.validate(big, "test.xls")
        assert not ok
        assert "초과" in err

    def test_wrong_magic(self):
        """매직 바이트가 허용된 포맷 중 어느 것도 아니면 거부.

        주의: ZIP(PK\\x03\\x04)은 .docx/.pptx 지원으로 허용되므로 사용 불가.
        OLE2/PDF/PNG/JPEG/GIF/BMP/ZIP/HTML 전부에 걸리지 않는 무의미한 바이트 사용.
        """
        ok, err = UploadValidator.validate(b"\x00\x01\x02\x03" + b"\x00" * 100, "test.xls")
        assert not ok
        assert "유효한" in err or "XLS" in err

    def test_valid_magic(self):
        ok, err = UploadValidator.validate(b"\xd0\xcf\x11\xe0" + b"\x00" * 100, "test.xls")
        assert ok
        assert err == ""

    def test_accepts_html_in_xls_magic(self):
        """한국 대학 포털이 .xls로 export하는 HTML 테이블도 허용."""
        html = b"<html><body><table><tr><td>a</td></tr></table></body></html>"
        ok, err = UploadValidator.validate(html + b" " * 100, "transcript.xls")
        assert ok, f"HTML-in-XLS가 거부됨: {err}"

    def test_accepts_html_in_xls_with_bom(self):
        """UTF-8 BOM이 앞에 있는 HTML도 허용."""
        html = b"\xef\xbb\xbf<html><table><tr><td>1</td></tr></table></html>"
        ok, err = UploadValidator.validate(html + b" " * 50, "transcript.xls")
        assert ok, f"BOM+HTML-in-XLS가 거부됨: {err}"

    def test_rejects_non_html_non_ole2(self):
        """OLE2/HTML/PDF/PNG/JPEG/GIF/BMP/ZIP 중 어느 것도 아니면 거부 (회귀 방지).

        ZIP(PK\\x03\\x04)은 .docx/.pptx 지원으로 허용되었으므로 사용 불가.
        어떤 매직에도 걸리지 않는 바이트 시퀀스로 테스트.
        """
        ok, err = UploadValidator.validate(b"\x00\x01\x02\x03" + b"\x00" * 100, "test.xls")
        assert not ok
        assert "유효한" in err or "XLS" in err


# ══════════════════════════════════════════════════════
# PIIRedactor 테스트
# ══════════════════════════════════════════════════════

class TestPIIRedactor:
    def test_redact_name(self):
        profile = StudentAcademicProfile(
            profile=StudentProfile(성명="박수환", 학번="20201877", 입학연도="2020")
        )
        text = "박수환 학생의 학번은 20201877이며 2020학번입니다."
        result = PIIRedactor.redact_for_llm(text, profile)
        assert "박수환" not in result
        assert "20201877" not in result
        assert "2020학번" in result

    def test_redact_for_log(self):
        text = "학생 20201877의 성적을 조회합니다."
        result = PIIRedactor.redact_for_log(text)
        assert "20201877" not in result
        assert "[REDACTED_ID]" in result

    def test_mask_name(self):
        assert PIIRedactor.mask_name("박수환") == "박○○"
        assert PIIRedactor.mask_name("김") == "김○"
        assert PIIRedactor.mask_name("") == ""


# ══════════════════════════════════════════════════════
# SecureTranscriptStore 테스트
# ══════════════════════════════════════════════════════

class TestSecureTranscriptStore:
    def _make_profile(self):
        return StudentAcademicProfile(
            profile=StudentProfile(학번="20201877", 성명="테스트", 입학연도="2020"),
            version=1,
        )

    def _store_with_consent(self, state, profile=None, session_id="test-session"):
        """동의 → 저장 헬퍼."""
        SecureTranscriptStore.grant_consent(state, session_id)
        SecureTranscriptStore.store(state, profile or self._make_profile(), session_id)

    def test_store_requires_consent(self):
        state = {}
        SecureTranscriptStore.store(state, self._make_profile(), "test")
        # 동의 없이 store → 저장 거부
        assert SecureTranscriptStore.retrieve(state) is None

    def test_store_with_consent(self):
        state = {}
        self._store_with_consent(state)
        retrieved = SecureTranscriptStore.retrieve(state)
        assert retrieved is not None

    def test_store_erases_name_and_full_id(self):
        """store() 후 성명과 8자리 학번이 삭제되었는지 확인."""
        state = {}
        self._store_with_consent(state)
        retrieved = SecureTranscriptStore.retrieve(state)
        assert retrieved.profile.성명 == ""  # 이름 삭제됨
        assert retrieved.profile.학번 == ""  # 8자리 학번 삭제됨
        assert retrieved.profile.입학연도 == "2020"  # 입학연도는 유지

    def test_destroy_clears_all(self):
        state = {}
        self._store_with_consent(state)
        SecureTranscriptStore.destroy(state)
        assert SecureTranscriptStore.retrieve(state) is None
        for key in SecureTranscriptStore._ALL_KEYS:
            assert key not in state

    def test_ttl_expiry(self):
        state = {}
        self._store_with_consent(state)
        state["_transcript_stored_at"] = time.time() - 2000
        assert SecureTranscriptStore.retrieve(state) is None
        assert "_transcript_data" not in state

    def test_remaining_seconds(self):
        state = {}
        self._store_with_consent(state)
        remaining = SecureTranscriptStore.remaining_seconds(state)
        assert 1700 < remaining <= 1800

    def test_is_active(self):
        state = {}
        assert not SecureTranscriptStore.is_active(state)
        self._store_with_consent(state)
        assert SecureTranscriptStore.is_active(state)

    def test_consent_revoke_destroys_data(self):
        """동의 철회 시 데이터 즉시 파기."""
        state = {}
        self._store_with_consent(state)
        assert SecureTranscriptStore.is_active(state)
        SecureTranscriptStore.revoke_consent(state)
        assert not SecureTranscriptStore.is_active(state)
        assert "_transcript_data" not in state

    def test_retrieve_without_consent_destroys(self):
        """동의 상태 제거 후 retrieve → 자동 파기."""
        state = {}
        self._store_with_consent(state)
        # 동의 상태 직접 제거 (시뮬레이션)
        state.pop("_transcript_consent", None)
        assert SecureTranscriptStore.retrieve(state) is None


# ══════════════════════════════════════════════════════
# VersionManager 테스트
# ══════════════════════════════════════════════════════

class TestVersionManager:
    def test_detect_diff_no_changes(self):
        p1 = StudentAcademicProfile(
            credits=CreditsSummary(총_취득학점=120.0, 평점평균=3.5),
            courses=[CourseRecord(교과목번호="A001", 이수학기="2025/1", 교과목명="테스트")]
        )
        p2 = StudentAcademicProfile(
            credits=CreditsSummary(총_취득학점=120.0, 평점평균=3.5),
            courses=[CourseRecord(교과목번호="A001", 이수학기="2025/1", 교과목명="테스트")]
        )
        diff = TranscriptVersionManager.detect_diff(p1, p2)
        assert diff == {}

    def test_detect_diff_credits_changed(self):
        p1 = StudentAcademicProfile(credits=CreditsSummary(총_취득학점=120.0, 평점평균=3.5))
        p2 = StudentAcademicProfile(credits=CreditsSummary(총_취득학점=126.5, 평점평균=3.97))
        diff = TranscriptVersionManager.detect_diff(p1, p2)
        assert "총_취득학점" in diff
        assert "평점평균" in diff

    def test_detect_diff_new_courses(self):
        p1 = StudentAcademicProfile(courses=[])
        p2 = StudentAcademicProfile(courses=[
            CourseRecord(교과목번호="NEW001", 이수학기="2026/1", 교과목명="신규과목")
        ])
        diff = TranscriptVersionManager.detect_diff(p1, p2)
        assert "신규과목" in diff

    def test_snapshot_creation(self):
        p = StudentAcademicProfile(
            profile=StudentProfile(입학연도="2020"),
            credits=CreditsSummary(총_취득학점=126.5, 평점평균=3.97),
            courses=[CourseRecord()] * 53,
            version=1,
        )
        snap = TranscriptVersionManager.create_snapshot(p)
        assert snap["version"] == 1
        assert snap["총_취득학점"] == 126.5
        assert snap["과목수"] == 53


# ══════════════════════════════════════════════════════
# HTML-in-XLS 파싱 (한국 대학 포털 export 포맷)
# ══════════════════════════════════════════════════════


class TestHtmlInXls:
    """.xls 확장자로 저장된 HTML 테이블을 파서가 자동 감지·처리해야 한다.

    한국 대학 포털이 IE6 호환성 때문에 <table>을 .xls로 export하는 관행 대응.
    원칙 1(스키마 진화): 포맷 판단이 확장자가 아닌 파일 바이트에서 자동 유도.
    """

    def _make_html_grid_bytes(self) -> bytes:
        return (
            b"<html><body><table>"
            b"<tr><td>cell_0_0</td><td>cell_0_1</td></tr>"
            b"<tr><td>cell_1_0</td><td>cell_1_1</td></tr>"
            b"<tr><td>cell_2_0</td><td>cell_2_1</td></tr>"
            b"</table></body></html>"
        )

    def test_read_xls_auto_detects_html(self):
        from app.transcript.parser import TranscriptParser
        parser = TranscriptParser()
        grid = parser._read_xls(self._make_html_grid_bytes())
        assert len(grid) == 3
        assert grid[0] == ["cell_0_0", "cell_0_1"]
        assert grid[2][1] == "cell_2_1"

    def test_read_xls_picks_largest_table(self):
        """여러 <table>이 있으면 셀 수가 가장 많은 것을 선택."""
        from app.transcript.parser import TranscriptParser
        html = (
            b"<html><body>"
            b"<table><tr><td>tiny</td></tr></table>"  # 1 cell
            b"<table>"
            b"<tr><td>big_0_0</td><td>big_0_1</td><td>big_0_2</td></tr>"
            b"<tr><td>big_1_0</td><td>big_1_1</td><td>big_1_2</td></tr>"
            b"</table>"
            b"</body></html>"
        )
        parser = TranscriptParser()
        grid = parser._read_xls(html)
        assert len(grid) == 2
        assert grid[0][2] == "big_0_2"

    def test_read_xls_html_colspan_expanded(self):
        """colspan은 단순 복제돼 행 길이가 균일해야 한다."""
        from app.transcript.parser import TranscriptParser
        html = (
            b"<html><table>"
            b"<tr><td colspan='2'>header</td><td>x</td></tr>"
            b"<tr><td>a</td><td>b</td><td>c</td></tr>"
            b"</table></html>"
        )
        grid = TranscriptParser()._read_xls(html)
        # colspan=2 → "header"가 2칸에 복제되어 3열이 됨
        assert len(grid[0]) == 3
        assert grid[0][0] == "header"
        assert grid[0][1] == "header"
        assert grid[0][2] == "x"

    def test_read_html_table_raises_on_no_table(self):
        from app.transcript.parser import TranscriptParser
        html = b"<html><body><p>No table here</p></body></html>"
        with pytest.raises(ValueError, match="table"):
            TranscriptParser()._read_xls(html)


# ══════════════════════════════════════════════════════
# 실제 XLS 파싱 통합 테스트 (로컬 파일 있을 때만)
# ══════════════════════════════════════════════════════


@pytest.mark.skipif(not _XLS_PATH.exists(), reason="XLS 파일 없음")
class TestTranscriptParserReal:
    @pytest.fixture
    def parsed(self):
        from app.transcript.parser import TranscriptParser
        with open(_XLS_PATH, "rb") as f:
            data = f.read()
        return TranscriptParser().parse(data, _XLS_PATH.name)

    def test_profile_basic(self, parsed):
        p = parsed.profile
        assert p.학번 == "20201877"
        assert p.입학연도 == "2020"
        assert p.student_group == "2017_2020"
        assert p.학부과 == "소프트웨어학부"
        assert p.전공 == "소프트웨어전공"
        assert p.학년 == 4
        assert p.성명 == "박수환"
        assert p.내외국인 == "내국인"
        assert p.학적상태 == "재학"

    def test_profile_dual_major(self, parsed):
        assert "스마트융합보안" in parsed.profile.복수전공

    def test_credits_summary(self, parsed):
        c = parsed.credits
        assert c.총_졸업기준 == 130.0
        assert c.총_취득학점 == 126.5
        assert c.총_부족학점 == 3.5
        assert c.평점평균 == 3.97

    def test_graduation_exams(self, parsed):
        assert parsed.credits.졸업시험.get("주전공") == "N"
        assert parsed.credits.졸업인증.get("기업정신") == "Y"

    def test_course_count(self, parsed):
        assert len(parsed.courses) >= 50

    def test_dual_major_courses(self, parsed):
        dual = [c for c in parsed.courses if "복수전공" in c.category]
        assert len(dual) == 8

    def test_current_semester(self, parsed):
        current = [c for c in parsed.courses if c.이수학기 == "2026/1"]
        assert len(current) >= 7

    def test_retake_detection(self, parsed):
        retakes = [c for c in parsed.courses if c.is_retake]
        assert len(retakes) >= 1
        assert any("토익" in c.교과목명 for c in retakes)

    def test_no_category_duplicates(self, parsed):
        names = [c.name for c in parsed.credits.categories]
        deduped = set((c.name, c.졸업기준, c.취득학점) for c in parsed.credits.categories)
        assert len(deduped) == len(parsed.credits.categories)


# ══════════════════════════════════════════════════════
# 보안 네거티브 테스트
# ══════════════════════════════════════════════════════

class TestSecurityNegative:
    """공격 벡터 및 경계 조건 테스트."""

    def test_path_traversal_filename(self):
        """경로 순회 파일명 거부."""
        ok, err = UploadValidator.validate(
            b"\xd0\xcf\x11\xe0" + b"\x00" * 100,
            "../../../etc/passwd.xls"
        )
        assert not ok

    def test_backslash_traversal(self):
        ok, err = UploadValidator.validate(
            b"\xd0\xcf\x11\xe0" + b"\x00" * 100,
            "..\\..\\windows\\system32.xls"
        )
        assert not ok

    def test_xss_in_filename(self):
        """XSS 페이로드 파일명 거부."""
        ok, err = UploadValidator.validate(
            b"\xd0\xcf\x11\xe0" + b"\x00" * 100,
            '<img src=x onerror=alert(1)>.xls'
        )
        assert not ok

    def test_sanitize_filename(self):
        assert UploadValidator.sanitize_filename("../../../etc/test.xls") == "test.xls"
        assert UploadValidator.sanitize_filename("C:\\Users\\test.xls") == "test.xls"
        assert UploadValidator.sanitize_filename("normal.xls") == "normal.xls"

    def test_pii_not_in_safe_format(self):
        """format_*_safe() 출력에 이름/8자리학번이 없는지 확인."""
        from app.transcript.analyzer import TranscriptAnalyzer
        profile = StudentAcademicProfile(
            profile=StudentProfile(
                성명="박수환", 학번="20201877", 입학연도="2020",
                학부과="소프트웨어학부", 전공="소프트웨어전공",
                student_group="2017_2020",
            ),
            credits=CreditsSummary(
                총_졸업기준=130, 총_취득학점=126.5, 총_부족학점=3.5, 평점평균=3.97,
            ),
            courses=[CourseRecord(교과목명="운영체제", 교과목번호="SOF304", 학점=3)],
        )
        tx = TranscriptAnalyzer(profile)
        for method in [tx.format_gap_context_safe, tx.format_profile_summary_safe]:
            text = method()
            assert "박수환" not in text, f"이름 유출: {method.__name__}"
            assert "20201877" not in text, f"학번 유출: {method.__name__}"

        courses_text = tx.format_courses_context_safe(profile.courses)
        assert "박수환" not in courses_text
        assert "20201877" not in courses_text

    def test_redact_catches_residual_ids(self):
        """정규식이 잔여 학번 패턴도 잡는지 확인."""
        text = "학생 20201877의 학번은 20191234이고 19987654입니다."
        result = PIIRedactor.redact_for_log(text)
        assert "20201877" not in result
        assert "20191234" not in result
        assert "19987654" not in result

    def test_store_erases_pii_before_persistence(self):
        """store() 후 원본 PII가 삭제되었는지 확인."""
        state = {}
        profile = StudentAcademicProfile(
            profile=StudentProfile(학번="20201877", 성명="박수환", 입학연도="2020"),
            version=1,
        )
        SecureTranscriptStore.grant_consent(state, "test")
        SecureTranscriptStore.store(state, profile, "test")
        stored = state.get("_transcript_data")
        assert stored.profile.성명 == ""
        assert stored.profile.학번 == ""
        assert stored.profile.입학연도 == "2020"  # 이건 유지
        assert stored._masked_name == "박○○"  # 마스킹된 이름만 유지
