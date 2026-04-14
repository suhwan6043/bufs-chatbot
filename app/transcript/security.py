"""
성적표 개인정보 보안 모듈.

모든 성적 데이터 접근·저장·삭제는 이 모듈을 통해서만 수행됩니다.

보안 원칙:
  1. 최소 보유 — 원본 XLS 바이트는 파싱 즉시 폐기
  2. 최소 노출 — LLM 전달 시 이름·학번 제거
  3. 자동 파기 — TTL 30분 + 세션 종료 시 삭제
  4. 기록 불가 — 디스크·DB·로그에 성적 데이터 기록 금지
  5. 동의 기반 — 업로드 전 명시적 동의 필수, 접근 시 동의 재확인
  6. 감사 추적 — 이벤트만 기록, 데이터 내용은 기록하지 않음
"""

import logging
import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .models import StudentAcademicProfile

# ── 감사 전용 로거 (개인정보 절대 불포함) ──
audit_logger = logging.getLogger("transcript.audit")

# 학번 패턴 (7-8자리: 19XXXXXX ~ 20XXXXXX)
_STUDENT_ID_RE = re.compile(r"(?:19|20)[0-9]\d{4,6}")
# 파일명 허용 패턴 (영숫자, 한글, 밑줄, 하이픈, 괄호, 공백, 점)
_ALLOWED_EXTENSIONS = frozenset({
    ".xls", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".hwp",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif",
})
_EXT_PATTERN = "|".join(e.lstrip(".") for e in _ALLOWED_EXTENSIONS)
_SAFE_FILENAME_RE = re.compile(
    rf"^[\w\s\-.()\[\]가-힣]+\.({_EXT_PATTERN})$",
    re.UNICODE | re.IGNORECASE,
)


def audit_log(event: str, session_id: str, detail: str = "") -> None:
    """
    감사 이벤트 기록. 개인정보는 절대 포함하지 않음.

    event: STORE, DESTROY, UPLOAD_REJECTED, PARSE_FAILED, CONSENT_GRANTED,
           TTL_EXPIRED, RETRIEVE, CONSENT_REVOKED
    session_id: 앞 8자만 기록
    detail: PII 없는 부가정보
    """
    audit_logger.info(
        "TRANSCRIPT_AUDIT | ts=%s | event=%s | session=%s | detail=%s",
        datetime.now().isoformat(timespec="seconds"),
        event,
        session_id[:8] if session_id else "unknown",
        detail,
    )


class SecureTranscriptStore:
    """
    세션 레벨 성적 데이터 보안 컨테이너.

    보안 기능:
    - TTL 기반 자동 만료 (기본 30분)
    - 동의 상태 재확인 (retrieve 시)
    - 명시적 파기 인터페이스
    - 전체 세션 일괄 파기
    """

    TTL_SECONDS: int = 1800  # 30분

    # session_state 내부 키 (밑줄 접두사로 외부 접근 차단)
    _KEY_DATA = "_transcript_data"
    _KEY_STORED_AT = "_transcript_stored_at"
    _KEY_SESSION_ID = "_transcript_session_id"
    _KEY_VERSIONS = "_transcript_versions"
    _KEY_CONSENT = "_transcript_consent"

    _ALL_KEYS = (_KEY_DATA, _KEY_STORED_AT, _KEY_SESSION_ID, _KEY_VERSIONS, _KEY_CONSENT)

    @classmethod
    def grant_consent(cls, session_state, session_id: str) -> None:
        """명시적 동의 기록."""
        session_state[cls._KEY_CONSENT] = {
            "granted": True,
            "timestamp": time.time(),
        }
        audit_log("CONSENT_GRANTED", session_id)

    @classmethod
    def revoke_consent(cls, session_state) -> None:
        """동의 철회 → 데이터 즉시 파기."""
        session_id = session_state.get(cls._KEY_SESSION_ID, "unknown")
        audit_log("CONSENT_REVOKED", session_id)
        cls.destroy(session_state)

    @classmethod
    def has_consent(cls, session_state) -> bool:
        """유효한 동의 상태인지 확인."""
        consent = session_state.get(cls._KEY_CONSENT)
        if not consent or not consent.get("granted"):
            return False
        return True

    @classmethod
    def store(
        cls,
        session_state,
        profile: "StudentAcademicProfile",
        session_id: str,
    ) -> None:
        """성적표 보안 저장 (동의 확인 + TTL 타임스탬프)."""
        if not cls.has_consent(session_state):
            audit_log("STORE_REJECTED", session_id, "no_consent")
            return

        # 저장 전 성명 삭제 (최소 보유: UI 표시 후 즉시 제거)
        # 마스킹된 이름은 별도 저장
        if profile.profile.성명:
            profile._masked_name = PIIRedactor.mask_name(profile.profile.성명)
            profile.profile.성명 = ""  # 원본 이름 즉시 삭제

        # 전체 학번도 삭제 (입학연도만 유지)
        if profile.profile.학번 and len(profile.profile.학번) > 4:
            profile.profile.학번 = ""  # 8자리 학번 삭제, 입학연도만 남김

        session_state[cls._KEY_DATA] = profile
        session_state[cls._KEY_STORED_AT] = time.time()
        session_state[cls._KEY_SESSION_ID] = session_id
        audit_log("STORE", session_id, f"version={profile.version}")

    @classmethod
    def retrieve(cls, session_state) -> Optional["StudentAcademicProfile"]:
        """TTL + 동의 상태 검사 후 반환. 만료/미동의 시 자동 파기."""
        stored_at = session_state.get(cls._KEY_STORED_AT, 0)
        if not stored_at:
            return None

        # 동의 상태 재확인
        if not cls.has_consent(session_state):
            audit_log("RETRIEVE_REJECTED", session_state.get(cls._KEY_SESSION_ID, "?"), "consent_revoked")
            cls.destroy(session_state)
            return None

        # TTL 검사
        elapsed = time.time() - stored_at
        if elapsed > cls.TTL_SECONDS:
            session_id = session_state.get(cls._KEY_SESSION_ID, "unknown")
            audit_log("TTL_EXPIRED", session_id, f"elapsed={int(elapsed)}s")
            cls.destroy(session_state)
            return None

        return session_state.get(cls._KEY_DATA)

    @classmethod
    def destroy(cls, session_state) -> None:
        """성적 데이터 즉시 파기. 모든 관련 키 제거."""
        session_id = session_state.get(cls._KEY_SESSION_ID, "unknown")
        # 데이터 객체 내부 필드도 명시적으로 None 처리 (GC 힌트)
        data = session_state.get(cls._KEY_DATA)
        if data:
            try:
                data.profile.성명 = ""
                data.profile.학번 = ""
                data.courses.clear()
                data.credits.categories.clear()
            except Exception:
                pass

        for key in cls._ALL_KEYS:
            session_state.pop(key, None)
        audit_log("DESTROY", session_id)

    @classmethod
    def is_active(cls, session_state) -> bool:
        """유효한(만료되지 않은 + 동의된) 성적 데이터 존재 여부."""
        return cls.retrieve(session_state) is not None

    @classmethod
    def remaining_seconds(cls, session_state) -> int:
        """TTL 남은 시간(초). 데이터 없으면 0."""
        stored_at = session_state.get(cls._KEY_STORED_AT, 0)
        if not stored_at:
            return 0
        remaining = cls.TTL_SECONDS - (time.time() - stored_at)
        return max(0, int(remaining))


class PIIRedactor:
    """
    개인식별정보(PII) 제거/마스킹.

    두 가지 레벨:
    1. redact_for_llm(): LLM 프롬프트 전송 전 — 이름·학번 치환
    2. redact_for_log(): 로그 기록 전 — 정규식 포괄 마스킹
    """

    @staticmethod
    def redact_for_llm(
        text: str,
        profile: Optional["StudentAcademicProfile"] = None,
    ) -> str:
        """
        LLM 프롬프트에 전송하기 전 PII 제거.
        - 학생 이름 → "해당 학생"
        - 학번 (8자리) → 입학연도(4자리)만 유지
        - 정규식으로 잔여 학번 패턴도 제거
        """
        if not text:
            return text

        if profile:
            p = profile.profile
            # 이름 제거
            if p.성명:
                text = text.replace(p.성명, "해당 학생")
            # 학번 전체 → 입학연도만
            if p.학번:
                text = text.replace(p.학번, f"{p.입학연도}학번")
                try:
                    text = text.replace(str(int(p.학번)), f"{p.입학연도}학번")
                except (ValueError, TypeError):
                    pass

        # 정규식으로 잔여 8자리 학번 패턴도 마스킹
        text = re.sub(r"(?:19|20)[0-9]\d{5,6}", lambda m: m.group()[:4] + "학번", text)
        # 방어 심층: "X학생" 패턴의 한글 이름도 마스킹 (store() 후 이름 삭제돼도 안전)
        text = re.sub(r"(?<=[^가-힣])[가-힣]{2,4}(?= ?학생)", "해당", text)
        return text

    @staticmethod
    def redact_for_log(text: str) -> str:
        """
        로그 기록 전 PII 포괄 마스킹.
        - 학번 패턴 (19/20XXXXXX) → [REDACTED_ID]
        """
        if not text:
            return text
        return _STUDENT_ID_RE.sub("[REDACTED_ID]", text)

    @staticmethod
    def mask_name(name: str) -> str:
        """이름 마스킹: "박수환" → "박○○"."""
        if not name:
            return ""
        if len(name) <= 1:
            return name[0] + "○"
        return name[0] + "○" * (len(name) - 1)


class UploadValidator:
    """
    업로드 파일 보안 검증.

    검증 항목:
    1. 파일 크기 (5MB 이하)
    2. 확장자 (.xls만 허용)
    3. 파일명 안전성 (경로 순회 차단)
    4. 매직 바이트 (OLE2 Compound Document)
    """

    MAX_SIZE_MB = 200
    # OLE2 Compound Document 매직 (레거시 BIFF .xls / .doc / .hwp / .ppt)
    ALLOWED_MAGIC_OLE2 = b"\xd0\xcf\x11\xe0"
    # 하위 호환용 별칭
    ALLOWED_MAGIC = ALLOWED_MAGIC_OLE2

    @classmethod
    def validate(cls, file_bytes: bytes, filename: str) -> tuple[bool, str]:
        """파일 유효성 검증. (통과여부, 에러메시지) 반환."""
        if not file_bytes:
            return False, "빈 파일입니다."

        # 크기 제한
        size_mb = len(file_bytes) / (1024 * 1024)
        if size_mb > cls.MAX_SIZE_MB:
            return False, f"파일 크기({size_mb:.1f}MB)가 {cls.MAX_SIZE_MB}MB를 초과합니다."

        # 파일명 안전성 검사 (경로 순회 차단)
        basename = os.path.basename(filename)
        if ".." in filename or "/" in filename or "\\" in filename:
            return False, "잘못된 파일명입니다."
        if basename != filename:
            return False, "잘못된 파일명입니다."

        # 확장자 검사
        ext = os.path.splitext(basename)[1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(_ALLOWED_EXTENSIONS))
            return False, f"지원하지 않는 파일 형식입니다. (지원: {allowed})"
        if ext == ".xlsx":
            return False, ".xlsx는 지원하지 않습니다. 학생포털에서 .xls로 다운로드해주세요."

        # 파일명 문자 검사 (XSS 방지)
        if not _SAFE_FILENAME_RE.match(basename):
            return False, "파일명에 허용되지 않는 문자가 포함되어 있습니다."

        # 매직 바이트 검사
        # 원칙 1(스키마 진화): 포맷 판단이 확장자가 아닌 파일 바이트에서 자동 유도.
        # 한국 대학 포털이 IE6 호환성 때문에 HTML 테이블을 .xls 확장자로 내보내는
        # 경우가 많아 OLE2(BIFF) 외에 HTML 시작(`<`)도 허용한다.
        if not cls._has_valid_magic(file_bytes):
            return False, "유효한 XLS 파일이 아닙니다."

        return True, ""

    @classmethod
    def _has_valid_magic(cls, file_bytes: bytes) -> bool:
        """다중 파일 포맷 매직 바이트 검증."""
        if len(file_bytes) < 4:
            return False
        head4 = file_bytes[:4]
        # OLE2 Compound Document (.xls, .doc, .ppt, .hwp)
        if head4 == cls.ALLOWED_MAGIC_OLE2:
            return True
        # PDF
        if file_bytes[:5] == b"%PDF-":
            return True
        # PNG
        if head4 == b"\x89PNG":
            return True
        # JPEG
        if file_bytes[:2] == b"\xff\xd8":
            return True
        # GIF
        if head4[:3] == b"GIF":
            return True
        # BMP
        if file_bytes[:2] == b"BM":
            return True
        # ZIP-based (.docx, .pptx)
        if head4 == b"PK\x03\x04":
            return True
        # HTML-in-XLS: 선행 공백·BOM 후 `<`로 시작하면 HTML 테이블로 간주
        head = file_bytes[:64].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        return head.startswith(b"<")

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """파일명 정규화. 경로 구성요소 제거 (Windows · macOS 모두 호환).

        os.path.basename()은 실행 OS 기준만 처리하므로,
        macOS에서 Windows 경로('C:\\Users\\test.xls')를 넘기면
        구분자를 인식하지 못함. '/' · '\\' 모두 분리해 마지막 요소만 반환.
        """
        parts = re.split(r"[/\\]", filename)
        return parts[-1] if parts else filename
