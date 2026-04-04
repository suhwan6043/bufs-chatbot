"""
그래프 스키마 레지스트리 (원칙 1: 스키마 자동 진화)

노드 타입별 필수/선택 속성을 정의합니다.
미등록 필드가 감지되면 자동으로 optional에 추가하고 경고를 로그합니다.
→ PDF 포맷 변경 시 새 필드가 자동으로 스키마에 반영됩니다.

원칙 1 강화: 발견된 필드를 JSON 파일로 영속화하여 재시작 후에도 유지.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

# 영속화 경로
_DISCOVERED_FIELDS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "schema_discovered_fields.json"
)

# 노드 타입별 속성 정의
# required: 노드 생성 시 반드시 존재해야 하는 속성
# optional: 있을 수 있는 속성 (없어도 경고 안 함)
# _ 접두사 속성 (메타데이터)은 검증에서 제외됨

SCHEMA: Dict[str, Dict[str, List[str]]] = {
    "학사일정": {
        "required": ["이벤트명"],
        "optional": ["학기", "시작일", "종료일", "비고"],
    },
    "수강신청규칙": {
        "required": ["적용학번그룹"],
        "optional": [
            "최대신청학점", "장바구니최대학점", "평점4이상최대학점",
            "교직복수전공최대학점", "예외조건", "재수강제한", "재수강최고성적",
            "학점이월여부", "학점이월최대학점", "학점이월조건",
            "OCU초과학점", "수강취소마감일시",
        ],
    },
    "졸업요건": {
        "required": ["적용학번그룹", "학생유형"],
        "optional": [
            "졸업학점", "교양이수학점", "글로벌소통역량학점",
            "진로탐색학점", "전공탐색학점", "취업커뮤니티요건",
            "NOMAD비교과지수", "졸업시험여부", "졸업시험비고",
            "기업가정신의무", "복수전공이수학점", "융합전공이수학점",
            "마이크로전공이수학점", "부전공이수학점", "졸업인증",
            "제2전공방법", "교양세부",
        ],
    },
    "OCU": {
        "required": [],
        "optional": [
            "정규학기_최대학점", "정규학기_최대과목",
            "졸업까지_최대학점", "졸업까지_최대과목",
            "시스템사용료_원", "초과수강료_원",
            "납부시작", "납부종료", "ID형식", "출석요건",
            "수강신청방법", "수강방법", "OCU홈페이지", "이수구분", "문의",
        ],
    },
    "성적처리": {
        "required": [],
        "optional": [
            "평가방식", "성적등급", "설명", "수강대상", "분류태그",
            "신청기간", "학기당최대", "재학중최대", "성적처리", "신청불가",
            "학기당최대", "졸업까지최대", "대상", "포기가능성적", "포기불가",
        ],
    },
    "계절학기": {
        "required": [],
        "optional": [
            "학기당최대학점", "졸업까지최대학점", "성적평가선택제",
            "수강신청사이트", "수강신청방법",
        ],
    },
    "장학금": {
        "required": ["장학금명"],
        "optional": [
            "종류", "지급액", "선발기준", "신청방법",
            "신청기간", "신청처", "필요서류", "문의처",
        ],
    },
    "조기졸업": {
        "required": [],
        "optional": [
            "신청학기", "편입생_신청불가", "평점기준_2005이전",
            "평점기준_2006", "평점기준_2007이후", "적용대상",
            "기준학점", "이수조건", "비고",
        ],
    },
    "학과전공": {
        "required": ["전공명"],
        "optional": [
            "단과대학", "전공유형", "제1전공_이수학점",
            "전화번호", "사무실위치", "졸업시험_요건",
        ],
    },
}


# ── 원칙 1 강화: 발견 필드 영속화 ────────────────────────────────

def _load_discovered() -> Dict[str, Set[str]]:
    """디스크에서 이전에 발견된 필드를 로드합니다."""
    if _DISCOVERED_FIELDS_PATH.exists():
        try:
            raw = json.loads(_DISCOVERED_FIELDS_PATH.read_text(encoding="utf-8"))
            loaded = {k: set(v) for k, v in raw.items()}
            logger.debug("스키마 발견 필드 로드: %d개 타입", len(loaded))
            return loaded
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("스키마 발견 필드 로드 실패: %s", e)
    return {}


def _save_discovered():
    """발견된 필드를 디스크에 영속화합니다."""
    try:
        _DISCOVERED_FIELDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: sorted(v) for k, v in _discovered_fields.items()}
        _DISCOVERED_FIELDS_PATH.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("스키마 발견 필드 저장 실패: %s", e)


# 로드 시 이전 발견 필드를 SCHEMA에 반영
_discovered_fields: Dict[str, Set[str]] = _load_discovered()

for _nt, _fields in _discovered_fields.items():
    if _nt in SCHEMA:
        existing = set(SCHEMA[_nt]["optional"])
        for _f in _fields:
            if _f not in existing:
                SCHEMA[_nt]["optional"].append(_f)
    else:
        SCHEMA[_nt] = {"required": [], "optional": sorted(_fields)}


def validate_node(node_type: str, attrs: dict) -> List[str]:
    """노드 속성을 스키마와 대조하여 검증합니다.

    미등록 필드가 발견되면:
    1. 경고 로그 출력
    2. optional 목록에 자동 추가 (스키마 자동 진화)
    3. 디스크에 영속화 (재시작 후에도 유지)

    Args:
        node_type: 노드 타입 (예: "학사일정")
        attrs: 노드 속성 dict

    Returns:
        경고 메시지 리스트 (빈 리스트 = 이상 없음)
    """
    schema = SCHEMA.get(node_type)
    if schema is None:
        # 미등록 노드 타입 → 자동 등록
        SCHEMA[node_type] = {"required": [], "optional": list(attrs.keys())}
        _discovered_fields[node_type] = set(attrs.keys())
        _save_discovered()
        logger.info("[스키마 자동 확장] 새 노드 타입 등록: %s (필드: %s)",
                    node_type, list(attrs.keys()))
        return []

    known = set(schema["required"]) | set(schema["optional"])
    skip_prefixes = ("type", "구분", "name")
    warnings = []
    new_fields_found = False

    for key in attrs:
        if key.startswith("_"):
            continue
        if key in skip_prefixes:
            continue
        if key not in known:
            # 자동 확장: optional에 추가
            schema["optional"].append(key)
            warnings.append(f"[스키마 자동 확장] {node_type}.{key} 필드 발견 → optional 추가")

            # 추적 + 영속화 플래그
            if node_type not in _discovered_fields:
                _discovered_fields[node_type] = set()
            _discovered_fields[node_type].add(key)
            new_fields_found = True

    if new_fields_found:
        _save_discovered()

    if warnings:
        for w in warnings:
            logger.info(w)

    # required 필드 누락 확인
    for req in schema["required"]:
        if req not in attrs:
            msg = f"[스키마 경고] {node_type} 노드에 필수 필드 '{req}' 누락"
            warnings.append(msg)
            logger.warning(msg)

    return warnings


def get_discovered_fields() -> Dict[str, Set[str]]:
    """런타임에 자동 발견된 미등록 필드 목록을 반환합니다."""
    return dict(_discovered_fields)
