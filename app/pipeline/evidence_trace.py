"""Evidence Trace — 검색 후보·답변·출처를 cite_key로 통합 추적.

해결하는 문제:
1. 출처 페이지 수준 부족: doc/page/section + 청크 내 위치 + answer-source 매핑
2. source_urls와 corroborated pages 분리: cite_key로 단일 증거 체계 통합
3. 답변 항목 기준 부재: intent별 required/optional/excluded manifest 검사

로그 키:
- `[evidence]`           — 검색 후보 1개당 1줄, cite_key 포함
- `[answer-evidence-map]` — 답변 내 각 단위값이 어떤 cite_key에 근거하는지 매핑
- `[answer-manifest]`     — intent별 required/optional/excluded 충족 여부

원칙:
- cite_key는 결정적 (동일 입력 → 동일 키). doc + page + chunk_idx 조합.
- URL 기반 출처와 PDF 페이지 기반 출처 모두 cite_key로 표현 가능.
- manifest는 보수적으로 정의 — 누락 시 경고만, 강제 차단 없음.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models import SearchResult


@dataclass(frozen=True)
class EvidenceItem:
    """단일 증거 단위. cite_key는 doc + page + chunk_idx 결합."""
    cite_key: str           # 예: "PDF:학사안내.pdf#p38#c2" / "GRAPH:FAQ#취업커뮤니티" / "URL:m.bufs.ac.kr"
    doc: str                # 문서 파일명 또는 그래프 노드 이름
    page: Optional[int]     # 페이지 번호 (없으면 None)
    section: str            # section_path metadata
    source_type: str        # "vector" / "graph" / "notice" / "faq"
    score: float            # relevance score
    url: str                # source_url (있으면)
    text: str               # 원문 텍스트 (전체)
    chunk_idx: int          # 검색 결과 내 순서 (0-base)


def make_cite_key(r: SearchResult, idx: int) -> str:
    """SearchResult → 결정적 cite_key 생성.

    형식:
    - PDF 청크:  `PDF:<basename>#p<page>#c<idx>`
    - 그래프:    `GRAPH:<node_type>#<doc_or_title>`
    - 공지/포털: `URL:<host>#<idx>` (source_url 기반)
    - FAQ:       `FAQ:<title_or_node>`
    """
    meta = r.metadata or {}
    src_type = (meta.get("source_type") or "").lower()
    doc_type = (meta.get("doc_type") or "").lower()
    source_url = meta.get("source_url") or ""
    page = r.page_number or 0
    doc_basename = _doc_basename(r.source or meta.get("source") or "")
    title = meta.get("title") or meta.get("node_id") or ""
    node_type = (meta.get("node_type") or "").upper()

    if doc_type in ("notice", "notice_attachment") and source_url.startswith("http"):
        host = _url_host(source_url)
        return f"URL:{host}#{idx}"
    if doc_type == "faq" or node_type == "FAQ":
        # FAQ는 title 우선 (개별 식별), 없으면 doc_basename + idx
        identifier = (title or doc_basename or "unknown")[:40]
        return f"FAQ:{identifier}#{idx}"
    if src_type == "graph" or node_type:
        identifier = (title or doc_basename or node_type or "node")[:40]
        return f"GRAPH:{node_type or 'node'}#{identifier}"
    if doc_basename:
        if page:
            return f"PDF:{doc_basename}#p{page}#c{idx}"
        return f"PDF:{doc_basename}#c{idx}"
    return f"SRC:unknown#{idx}"


def _doc_basename(path: str) -> str:
    if not path:
        return ""
    name = path.replace("\\", "/").split("/")[-1]
    return name[:60]


def _url_host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else url)[:50]


def build_evidence_list(results: list, max_items: int = 16) -> list[EvidenceItem]:
    """SearchResult 리스트 → EvidenceItem 리스트 (cite_key 부여)."""
    out: list[EvidenceItem] = []
    for i, r in enumerate(results[:max_items]):
        meta = r.metadata or {}
        ci = EvidenceItem(
            cite_key=make_cite_key(r, i),
            doc=_doc_basename(r.source or meta.get("source") or ""),
            page=(r.page_number or None),
            section=(meta.get("section_path") or "")[:80],
            source_type=(meta.get("source_type") or "?"),
            score=float(r.score or 0.0),
            url=(meta.get("source_url") or ""),
            text=(r.text or ""),
            chunk_idx=i,
        )
        out.append(ci)
    return out


# ── 답변 → 증거 cite_key 매핑 ────────────────────────────────────────
# "key: value" 형식 답변 라인 추출용 패턴
_KV_LINE_RE = re.compile(
    r"^[\s\-\*•]*([가-힣A-Za-z][가-힣A-Za-z0-9 /·_]+?)\s*[:=：]\s*(.+?)\s*$",
    re.MULTILINE,
)


def map_answer_to_evidence(
    answer: str,
    evidence: list[EvidenceItem],
) -> list[dict]:
    """답변에서 추출한 각 사실값이 어떤 cite_key에 근거하는지 매핑.

    반환: [{"item": "credit:130", "matched_cites": ["PDF:..."], "in_context": True}, ...]

    매칭 방식 2가지:
    1. `present_units` 기반 (credit/won/url/grade 등) — 정규식 단위값 매칭
    2. "key: value" 라인 기반 — "졸업학점: 130" 같은 답변 라인을
       evidence에서 key+value 동시 등장 여부로 매칭. value만 매칭(false positive 다발)
       → key를 보조 anchor로 사용해 정확도 보강.
    """
    from app.pipeline.answer_units import present_units

    items: list[dict] = []
    seen_keys: set[str] = set()

    # ── 1) present_units 기반 매칭 ─────────────────────────────
    UNIT_FORMS = {
        "credit": lambda v: [f"{v}학점", f"{v} 학점"],   # raw 숫자 제외 (false positive 다발)
        "won":    lambda v: [f"{v}원", v.replace(",", "")],
        "course": lambda v: [f"{v}과목"],
        "grade":  lambda v: [v],
        "url":    lambda v: [v],
        "phone":  lambda v: [v, v.replace("-", "")],
        "room":   lambda v: [v],
        "date":   lambda v: [v],
        "time":   lambda v: [v],
    }

    a_units = present_units(answer or "")
    for unit, vals in a_units.items():
        forms_fn = UNIT_FORMS.get(unit, lambda v: [v])
        for v in vals:
            forms = forms_fn(v)
            matched = [ev.cite_key for ev in evidence
                        if any(f in (ev.text or "") for f in forms)]
            key = f"{unit}:{v}"
            if key not in seen_keys:
                seen_keys.add(key)
                items.append({
                    "item": key,
                    "matched_cites": matched,
                    "in_context": bool(matched),
                })

    # ── 2) "key: value" 라인 기반 매칭 ─────────────────────────
    # 답변에서 "졸업학점: 130", "균형교양: 14학점" 같은 라인을 추출.
    # evidence에 key와 value가 가까운 위치에 함께 등장하면 매칭.
    for m in _KV_LINE_RE.finditer(answer or ""):
        key_str = m.group(1).strip()
        val_str = m.group(2).strip()
        # 키가 너무 길면 noise (정상 문장이 매칭됨) — 12자 이하만
        if len(key_str) > 12 or not val_str:
            continue
        # 숫자값 추출 시도 (있으면 value 정합성 보강)
        num_m = re.search(r"\d+", val_str)
        num_val = num_m.group(0) if num_m else ""

        matched = []
        for ev in evidence:
            txt = ev.text or ""
            # key 단어 부분 일치 OR 핵심 명사 일치
            key_hit = key_str in txt or _key_partial_match(key_str, txt)
            val_hit = (val_str in txt) or (num_val and num_val in txt) or (val_str == "없음" and ("없" in txt or "미실시" in txt))
            if key_hit and val_hit:
                matched.append(ev.cite_key)

        item_key = f"kv:{key_str}={val_str}"
        if item_key in seen_keys:
            continue
        seen_keys.add(item_key)
        items.append({
            "item": item_key,
            "matched_cites": matched,
            "in_context": bool(matched),
        })

    return items


_KEY_SUFFIXES = ("여부", "요건", "기준", "확인", "신청", "방법", "정보", "안내", "조건")


def _key_partial_match(key: str, text: str) -> bool:
    """답변 key의 핵심 명사가 evidence text에 등장하는지 (조사/접미사 무관).

    전략:
    1) key 그대로 substring 매칭
    2) 흔한 접미사 ("여부", "요건" 등) 제거 후 prefix 매칭
    3) 2자 이상 한글 어절 단위로 분리 후 모두 등장하면 매칭
    """
    if key in text:
        return True
    # 접미사 제거
    stripped = key
    for suf in _KEY_SUFFIXES:
        if stripped.endswith(suf) and len(stripped) > len(suf) + 1:
            stripped = stripped[: -len(suf)]
            break
    if stripped != key and stripped in text:
        return True
    tokens = re.findall(r"[가-힣]{2,}", stripped)
    if not tokens:
        return False
    return all(t in text for t in tokens)


# ── 답변 manifest (intent별 required/optional/excluded) ───────────────
# 각 항목은 답변 텍스트에 등장해야 하는 키워드 (정규식 또는 부분 문자열).
# required: 모두 충족해야 완전 답변
# optional: 충족 시 가산점, 미충족 시 경고만
# excluded_unless_asked: 질문에 명시 안 됐다면 답변에 등장하면 안 됨 (오버블로팅 방지)
ANSWER_MANIFEST: dict[str, dict[str, list[str]]] = {
    "GRADUATION_REQ": {
        "required": ["졸업학점", "교양"],
        "optional": ["전공", "균형교양", "취업커뮤니티", "졸업시험", "사회봉사", "외국어"],
        "excluded_unless_asked": ["OCU"],
    },
    "EARLY_GRADUATION": {
        "required": ["조기졸업", "평점", "학기"],
        "optional": ["신청", "기간", "자격"],
        "excluded_unless_asked": [],
    },
    "REGISTRATION": {
        "required": ["수강신청"],
        "optional": ["기간", "방법", "시간", "URL", "포털"],
        "excluded_unless_asked": [],
    },
    "SCHEDULE": {
        "required": ["일정"],
        "optional": ["날짜", "기간", "시작", "종료"],
        "excluded_unless_asked": [],
    },
    "LEAVE_OF_ABSENCE": {
        "required": ["휴학"],
        "optional": ["일반", "병역", "기간", "신청"],
        "excluded_unless_asked": [],
    },
    "SCHOLARSHIP": {
        "required": ["장학"],
        "optional": ["신청", "자격", "금액", "기간"],
        "excluded_unless_asked": [],
    },
    "MAJOR_CHANGE": {
        "required": ["전공"],
        "optional": ["복수", "부전공", "변경", "신청"],
        "excluded_unless_asked": [],
    },
    "COURSE_INFO": {
        "required": [],
        "optional": ["학점", "교과목", "이수"],
        "excluded_unless_asked": [],
    },
    "CONTACT": {
        "required": [],
        "optional": ["전화", "사무실", "호실", "051"],
        "excluded_unless_asked": [],
    },
}


@dataclass
class ManifestReport:
    intent: str
    required_total: int
    required_present: int
    required_missing: list[str]
    optional_total: int
    optional_present: int
    optional_missing: list[str]
    excluded_violations: list[str]
    completeness: float        # required_present / required_total (필수 충족률)
    coverage: float            # (req+opt)_present / (req+opt)_total (전체 커버)

    def summary(self) -> str:
        return (
            f"intent={self.intent} "
            f"required={self.required_present}/{self.required_total} "
            f"optional={self.optional_present}/{self.optional_total} "
            f"excluded_violations={len(self.excluded_violations)} "
            f"completeness={self.completeness:.2f} coverage={self.coverage:.2f}"
        )


def check_answer_manifest(answer: str, intent: str, question: str = "") -> ManifestReport:
    """답변이 intent의 manifest를 충족하는지 검사."""
    a = answer or ""
    q = question or ""
    manifest = ANSWER_MANIFEST.get(intent.upper() if intent else "", {})

    required = manifest.get("required", [])
    optional = manifest.get("optional", [])
    excluded = manifest.get("excluded_unless_asked", [])

    req_missing = [r for r in required if r not in a]
    opt_missing = [o for o in optional if o not in a]

    # excluded_unless_asked: 질문에 키워드 명시 안 됐는데 답변에 등장하면 violation
    excl_viol = []
    for e in excluded:
        if e in a and e not in q:
            excl_viol.append(e)

    req_total = len(required)
    req_present = req_total - len(req_missing)
    opt_total = len(optional)
    opt_present = opt_total - len(opt_missing)

    completeness = (req_present / req_total) if req_total else 1.0
    total = req_total + opt_total
    coverage = ((req_present + opt_present) / total) if total else 1.0

    return ManifestReport(
        intent=intent or "?",
        required_total=req_total,
        required_present=req_present,
        required_missing=req_missing,
        optional_total=opt_total,
        optional_present=opt_present,
        optional_missing=opt_missing,
        excluded_violations=excl_viol,
        completeness=completeness,
        coverage=coverage,
    )
