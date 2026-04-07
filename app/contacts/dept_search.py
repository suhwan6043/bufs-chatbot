"""
부서/학과 연락처 검색 모듈

data/contacts/departments.json 을 로드하여
사용자 쿼리에서 학과명/부서명을 인식하고
전화번호를 반환합니다.

특징:
- 한국어 부분 문자열 매칭 (정확 > 시작 > 포함 순)
- 별칭(aliases) 기반 확장 검색
- 학과/전공 계층 구조 지원
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent.parent.parent / "data" / "contacts" / "departments.json"

# 연락처 쿼리 트리거 키워드 (한국어)
CONTACT_KEYWORDS = {
    "전화", "전화번호", "연락처", "연락", "번호",
    "전화기", "내선", "사무실", "직통",
    "몇번", "몇 번", "어떻게 연락",
}
# "어디" 단독은 너무 광범위 → 복합 패턴으로만 트리거
CONTACT_COMPOUND_PATTERNS = {
    "사무실 어디", "위치가 어디", "위치 어디", "어디에 있",
    "어디로 연락", "어디로 전화",
}
# 연락처 쿼리 트리거 키워드 (영어)
CONTACT_KEYWORDS_EN = {
    "phone", "number", "contact", "call", "reach",
    "office", "extension", "hotline", "telephone",
}
CONTACT_COMPOUND_PATTERNS_EN = {
    "phone number", "contact number", "how to contact",
    "how do i reach", "where is the office", "office location",
}


@dataclass
class ContactResult:
    """연락처 검색 결과"""
    name: str                   # 부서/학과 정식 명칭
    phone: str                  # 전화번호 (051-509-XXXX)
    extension: str              # 내선번호 (XXXX)
    college: Optional[str]      # 소속 단과대학 (없으면 None)
    office: Optional[str]       # 사무실 위치 (없으면 None)
    match_type: str             # "exact" | "prefix" | "contains"


class DeptSearcher:
    """
    학과/부서 연락처 검색기.

    사용 예:
        searcher = DeptSearcher()
        results = searcher.search("영어학부 전화번호")
    """

    def __init__(self) -> None:
        self._data = self._load()
        self._flat: List[dict] = []   # 검색용 평면 리스트
        self._build_flat_list()

    # ── 공개 API ──────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 3) -> List[ContactResult]:
        """
        쿼리 문자열에서 학과/부서를 찾아 연락처를 반환합니다.

        Args:
            query:  사용자 질문 (예: "영어학부 전화번호 알려줘")
            top_k:  최대 반환 개수

        Returns:
            ContactResult 리스트 (높은 관련도 순)
        """
        q = query.strip()
        if not q:
            return []

        scored: List[tuple] = []   # (score, ContactResult)

        for entry in self._flat:
            score, match_type = self._score(q, entry["aliases"])
            if score > 0:
                scored.append((score, ContactResult(
                    name=entry["name"],
                    phone=entry["phone"],
                    extension=entry["extension"],
                    college=entry.get("college"),
                    office=entry.get("office"),
                    match_type=match_type,
                )))

        # 점수 내림차순 정렬
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def is_contact_query(self, query: str) -> bool:
        """
        쿼리가 연락처 관련 질문인지 판단합니다.

        연락처 키워드(전화번호, 연락처, 사무실 등)와
        알려진 학과/부서명이 함께 있을 때 True 반환합니다.
        """
        q = query.strip()
        q_lower = q.lower()
        has_keyword = (
            any(kw in q for kw in CONTACT_KEYWORDS)
            or any(pat in q for pat in CONTACT_COMPOUND_PATTERNS)
            or any(kw in q_lower for kw in CONTACT_KEYWORDS_EN)
            or any(pat in q_lower for pat in CONTACT_COMPOUND_PATTERNS_EN)
        )
        if not has_keyword:
            return False
        # EN 쿼리: FlashText로 EN→KO 변환 후 KO 용어로 검색
        search_q = self._en_to_ko_query(q)
        return bool(self.search(search_q, top_k=1))

    # ── 내부 ──────────────────────────────────────────────────────

    @staticmethod
    def _en_to_ko_query(query: str) -> str:
        """EN 쿼리에 포함된 학술 용어를 KO로 치환하여 반환합니다.

        'can you tell me english department number'
        → 'english department number 영어학부'  (KO 용어 병합)
        """
        try:
            from app.pipeline.query_analyzer import EnTermMapper
            terms = EnTermMapper.get().extract(query)
            if terms:
                ko_terms = " ".join(t["ko"] for t in terms)
                return f"{query} {ko_terms}"
        except Exception:
            pass
        return query

    def _load(self) -> dict:
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error("departments.json 파일을 찾을 수 없습니다: %s", DATA_FILE)
            return {}
        except json.JSONDecodeError as e:
            logger.error("departments.json 파싱 오류: %s", e)
            return {}

    def _build_flat_list(self) -> None:
        """계층 JSON을 평면 검색 리스트로 변환합니다."""
        data = self._data
        if not data:
            return

        # 행정부서
        for item in data.get("admin_offices", []):
            self._flat.append({
                "name": item["name"],
                "aliases": item.get("aliases", [item["name"]]),
                "phone": item["phone"],
                "extension": item["extension"],
                "college": None,
                "office": item.get("office"),
            })

        # 단과대학 → 학부/학과 → 전공 (3단계 계층)
        for college in data.get("colleges", []):
            college_name = college["name"]
            for dept in college.get("departments", []):
                self._flat.append({
                    "name": dept["name"],
                    "aliases": dept.get("aliases", [dept["name"]]),
                    "phone": dept["phone"],
                    "extension": dept["extension"],
                    "college": college_name,
                    "office": dept.get("office"),
                })
                for sub in dept.get("sub_units", []):
                    self._flat.append({
                        "name": sub["name"],
                        "aliases": sub.get("aliases", [sub["name"]]),
                        "phone": sub["phone"],
                        "extension": sub["extension"],
                        "college": college_name,
                        "office": sub.get("office"),
                    })

        logger.debug("[DeptSearcher] %d개 항목 로드 완료", len(self._flat))

    @staticmethod
    def _score(query: str, aliases: List[str]) -> tuple:
        """
        쿼리와 별칭 목록 간 매칭 점수를 반환합니다.

        Returns:
            (score, match_type) — score=0 이면 미매칭
        """
        best_score = 0
        best_type = "none"
        query_lower = query.lower()

        for alias in aliases:
            alias_l = alias.strip()
            alias_lower = alias_l.lower()
            # 1. 정확 일치 (대소문자 무시)
            if alias_lower == query_lower or alias_lower in query_lower:
                if alias_lower == query_lower:
                    score = 100 + len(alias_l)
                    mtype = "exact"
                else:
                    score = 80 + len(alias_l)
                    mtype = "contains"
                if score > best_score:
                    best_score = score
                    best_type = mtype
                continue

            # 2. 쿼리가 alias로 시작 또는 alias가 쿼리로 시작 (대소문자 무시)
            if query_lower.startswith(alias_lower) or alias_lower.startswith(query_lower):
                score = 60 + len(alias_l)
                if score > best_score:
                    best_score = score
                    best_type = "prefix"

        return best_score, best_type


# ── 싱글턴 (Streamlit 캐시 없이) ────────────────────────────────

_searcher: Optional[DeptSearcher] = None


def get_dept_searcher() -> DeptSearcher:
    """프로세스 수명 동안 하나의 DeptSearcher 인스턴스를 반환합니다."""
    global _searcher
    if _searcher is None:
        _searcher = DeptSearcher()
    return _searcher
