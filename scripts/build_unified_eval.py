"""
평가 데이터셋 통합 빌더 (v1.0)

두 언어 평가 데이터의 장점을 합쳐 통일된 스키마로 재생성한다.

입력:
  data/eval_multilingual/eval_ko.jsonl  (50건)
  data/eval_multilingual/eval_en.jsonl  (52건)

출력:
  data/eval_multilingual/eval_ko_unified.jsonl
  data/eval_multilingual/eval_en_unified.jsonl

공통 통일 스키마:
  id            : 고유 식별자
  lang          : "ko" | "en"
  question      : 질문 텍스트
  ground_truth  : 정답 텍스트
  key_facts     : 정답 핵심 사실 토큰 목록 (Contains-F1 기준)
  intent        : SCHEDULE / REGISTRATION / GRADUATION_REQ / GRADE /
                  SCHOLARSHIP / MAJOR_CHANGE / COURSE_INFO / GENERAL
  category      : 세부 카테고리 (영문, 소문자)
  difficulty    : easy | medium | hard
  answerable    : true → 답변 가능, false → fallback
  source        : 출처 문서명 (없으면 "")
  evidence_page : 근거 페이지 번호 목록 [int, ...]
  check_language: 언어 일관성 검사 항목 여부 (bool)
  note          : 선택적 주석 (없으면 생략)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "eval_multilingual"

# ─── 카테고리 → intent 매핑 ──────────────────────────────────────────────────

_CATEGORY_TO_INTENT: dict[str, str] = {
    "academic_schedule": "SCHEDULE",
    "course_registration": "REGISTRATION",
    "graduation": "GRADUATION_REQ",
    "grade": "GRADE",
    "scholarship": "SCHOLARSHIP",
    "secondary_major": "MAJOR_CHANGE",
    "ocu": "COURSE_INFO",
    "community_service": "COURSE_INFO",
    "freshman_seminar": "COURSE_INFO",
    "career_courses": "COURSE_INFO",
    "global_communication": "COURSE_INFO",
    "international_student": "GENERAL",
    "transfer_student": "GENERAL",
    "fallback": "GENERAL",
    "language_consistency": "GENERAL",
}

# Not-Answerable 카테고리 (answerable=false)
_FALLBACK_CATEGORIES: frozenset[str] = frozenset({"fallback", "language_consistency"})

# 카테고리 → 출처 문서 (대략적 매핑, 없으면 빈 문자열)
_KO_SOURCE_MAP: dict[str, str] = {
    "academic_schedule": "학사안내",
    "course_registration": "학사안내",
    "graduation": "졸업요건 안내",
    "grade": "학사안내",
    "ocu": "OCU 안내",
    "scholarship": "장학 안내",
}
_EN_SOURCE_MAP: dict[str, str] = {
    "academic_schedule": "Academic Guide",
    "course_registration": "Academic Guide",
    "graduation": "Academic Guide",
    "grade": "Academic Guide",
    "scholarship": "Academic Guide",
    "secondary_major": "Academic Guide",
    "community_service": "Academic Guide",
    "freshman_seminar": "Academic Guide",
    "career_courses": "Academic Guide",
    "global_communication": "Academic Guide",
    "international_student": "Academic Guide",
    "transfer_student": "Academic Guide",
    "fallback": "",
    "language_consistency": "",
}


# ─── 유틸 ────────────────────────────────────────────────────────────────────

def _is_not_answerable(item: dict) -> bool:
    """category 기준으로 Not-Answerable 판정.

    fallback / language_consistency 카테고리만 Not-Answerable로 처리한다.
    ground_truth가 "No." 또는 "not available"로 시작해도
    실제로 정보를 제공하는 답변이면 answerable=true이므로
    텍스트 패턴 검사는 사용하지 않는다.
    """
    return item.get("category", "") in _FALLBACK_CATEGORIES


def _source_from_pages(source_doc: str, pages: list[int]) -> str:
    """출처 문서 + 페이지 번호 → 'Academic Guide p.6' 형태 문자열."""
    if not source_doc:
        return ""
    if pages:
        page_str = ", ".join(f"p.{p}" for p in pages)
        return f"{source_doc} {page_str}"
    return source_doc


# ─── KO 변환 ─────────────────────────────────────────────────────────────────

def _transform_ko(item: dict) -> dict:
    """eval_ko.jsonl 단일 항목 → 통일 스키마."""
    cat = item.get("category", "")
    pages = item.get("evidence_page", [])
    source_doc = _KO_SOURCE_MAP.get(cat, "")

    out: dict = {
        "id":            item["id"],
        "lang":          item.get("lang", "ko"),
        "question":      item["question"],
        "ground_truth":  item.get("ground_truth", ""),
        "key_facts":     item.get("key_facts") or [],
        "intent":        _CATEGORY_TO_INTENT.get(cat, "GENERAL"),
        "category":      cat,
        "difficulty":    item.get("difficulty", "medium"),
        "answerable":    not _is_not_answerable(item),
        "source":        _source_from_pages(source_doc, pages),
        "evidence_page": pages,
        "check_language": False,
    }
    if item.get("note"):
        out["note"] = item["note"]
    return out


# ─── EN 변환 ─────────────────────────────────────────────────────────────────

def _transform_en(item: dict) -> dict:
    """eval_en.jsonl 단일 항목 → 통일 스키마."""
    cat = item.get("category", "")
    pages = item.get("evidence_page", [])
    source_doc = _EN_SOURCE_MAP.get(cat, "Academic Guide")

    out: dict = {
        "id":            item["id"],
        "lang":          item.get("lang", "en"),
        "question":      item["question"],
        "ground_truth":  item.get("ground_truth", ""),
        "key_facts":     item.get("key_facts") or [],
        "intent":        _CATEGORY_TO_INTENT.get(cat, "GENERAL"),
        "category":      cat,
        "difficulty":    item.get("difficulty", "medium"),
        "answerable":    not _is_not_answerable(item),
        "source":        _source_from_pages(source_doc, pages),
        "evidence_page": pages,
        "check_language": bool(item.get("check_language", False)),
    }
    if item.get("note"):
        out["note"] = item["note"]
    return out


# ─── 통계 출력 ────────────────────────────────────────────────────────────────

def _stats(items: list[dict], lang: str) -> None:
    total = len(items)
    answerable = sum(1 for i in items if i["answerable"])
    key_facts_coverage = sum(1 for i in items if i["key_facts"])
    ev_page_coverage = sum(1 for i in items if i["evidence_page"])
    intents = sorted(set(i["intent"] for i in items))
    cats = sorted(set(i["category"] for i in items))

    print(f"\n  [{lang.upper()}] 총 {total}건")
    print(f"    answerable:       {answerable} / {total}")
    print(f"    key_facts 존재:   {key_facts_coverage} / {total}")
    print(f"    evidence_page:    {ev_page_coverage} / {total}")
    print(f"    intent 종류:      {intents}")
    print(f"    category 종류:    {cats}")


# ─── 메인 ────────────────────────────────────────────────────────────────────

def main() -> None:
    ko_in  = DATA_DIR / "eval_ko.jsonl"
    en_in  = DATA_DIR / "eval_en.jsonl"
    ko_out = DATA_DIR / "eval_ko_unified.jsonl"
    en_out = DATA_DIR / "eval_en_unified.jsonl"

    for path in (ko_in, en_in):
        if not path.exists():
            print(f"[ERROR] 파일 없음: {path}", file=sys.stderr)
            sys.exit(1)

    # ── KO 변환 ──────────────────────────────────────────────────────────────
    ko_items = [json.loads(l) for l in ko_in.read_text(encoding="utf-8").splitlines() if l.strip()]
    ko_unified = [_transform_ko(item) for item in ko_items]

    with ko_out.open("w", encoding="utf-8") as f:
        for item in ko_unified:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"✓ KO 통일 데이터 저장: {ko_out}")

    # ── EN 변환 ──────────────────────────────────────────────────────────────
    en_items = [json.loads(l) for l in en_in.read_text(encoding="utf-8").splitlines() if l.strip()]
    en_unified = [_transform_en(item) for item in en_items]

    with en_out.open("w", encoding="utf-8") as f:
        for item in en_unified:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"✓ EN 통일 데이터 저장: {en_out}")

    # ── 통계 ─────────────────────────────────────────────────────────────────
    print("\n=== 통일 스키마 적용 후 통계 ===")
    _stats(ko_unified, "ko")
    _stats(en_unified, "en")

    # 스키마 필드 확인
    unified_fields = set(ko_unified[0].keys())
    en_fields = set(en_unified[0].keys())
    shared = unified_fields & en_fields
    ko_only = unified_fields - en_fields
    en_only = en_fields - unified_fields
    print(f"\n  공통 필드 ({len(shared)}): {sorted(shared)}")
    if ko_only:
        print(f"  KO 전용: {sorted(ko_only)}")
    if en_only:
        print(f"  EN 전용: {sorted(en_only)}")


if __name__ == "__main__":
    main()
