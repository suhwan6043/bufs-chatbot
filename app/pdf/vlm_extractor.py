"""
VLM 폴백 추출기 — qwen2.5vl:7b (Ollama).

용도:
    - 표 추출 실패 (병합 셀, 빈 셀 50%+, 1×1 셀, 행/열 불일치)
    - PDF 페이지 텍스트 < 50자 + 이미지 비중 큰 경우
    - HWP 추출 깨짐 비율 > 20%

특징:
    - 디스크 캐시 (`data/vlm_cache/{file_sha}_{page}_{bbox}_{prompt_v}.json`)
    - 검증 게이트 (환각·구조 이상 감지 → 1회 재시도)
    - escalation 경로 (실패 시 더 큰 모델로 재시도 인터페이스 제공)
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# ── 설정 ────────────────────────────────────────────────────────────────────
VLM_MODEL = os.getenv("VLM_MODEL", "qwen2.5vl:7b")
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "http://localhost:11434")
VLM_TIMEOUT_SEC = int(os.getenv("VLM_TIMEOUT_SEC", "120"))
VLM_CACHE_DIR = Path(os.getenv("VLM_CACHE_DIR", "data/vlm_cache"))
VLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 프롬프트 버전 — 변경 시 캐시 자동 무효화
PROMPT_VERSION = "v1.0.0"


# ── 프롬프트 ────────────────────────────────────────────────────────────────
PROMPT_TABLE = """이 이미지는 한국 대학교 학사 안내 PDF의 표입니다.
GitHub-flavored Markdown 표로 정확히 변환하세요.

핵심 규칙 (반드시 지킬 것):
1. **모든 행의 셀(파이프 `|`로 구분된) 개수가 정확히 동일해야 함**
2. **첫 행은 헤더, 두 번째 행은 반드시 `| --- | --- | ... |` 구분선** (열 수만큼 ---)
3. 세로 병합 셀(rowspan): 첫 행에만 내용 쓰고, 이어지는 행 같은 위치는 빈 칸
4. 가로 병합 셀(colspan): 모든 병합된 칸에 같은 내용을 반복하지 말고 첫 칸에만
5. 줄바꿈된 셀: 한 셀로 합쳐서 공백으로 연결
6. 표 외 텍스트(설명·요약·번호 매기기) 추가 금지
7. 표가 아니거나 알아볼 수 없으면 정확히 "NOT_A_TABLE"만 출력

올바른 예:
| 구분 | A | B |
| --- | --- | --- |
| 1행 | a1 | b1 |
| 2행 | a2 | b2 |

잘못된 예 (열 수 다름 — 절대 금지):
| 구분 | A |
| --- | --- |
| 1행 | a1 | b1 |   ← 헤더 2열인데 행 3열

출력은 표만:"""

PROMPT_PAGE = """이 이미지는 한국 대학교 학사 안내 PDF의 한 페이지입니다.
페이지 내 모든 정보를 텍스트로 정확히 추출해주세요.

규칙:
- 본문 텍스트는 단락 구분 유지
- 표는 GitHub-flavored Markdown 표로 변환
- 도식·다이어그램은 핵심 정보를 텍스트로 정리
- 이미지 자체 설명은 [이미지: 설명] 형식으로
- 페이지 번호·머리글·바닥글은 무시

출력은 추출된 콘텐츠만:"""


# ── 결과 객체 ───────────────────────────────────────────────────────────────
@dataclass
class VLMResult:
    text: str
    model: str
    prompt_version: str
    cached: bool
    duration_sec: float
    valid: bool = True
    validation_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "cached": self.cached,
            "duration_sec": self.duration_sec,
            "valid": self.valid,
            "validation_reason": self.validation_reason,
        }


# ── 캐시 ────────────────────────────────────────────────────────────────────
def _cache_key(image_bytes: bytes, prompt_kind: str) -> str:
    h = hashlib.sha256(image_bytes).hexdigest()[:16]
    return f"{h}_{prompt_kind}_{PROMPT_VERSION}.json"


def _load_cache(key: str) -> Optional[VLMResult]:
    p = VLM_CACHE_DIR / key
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d["cached"] = True
        return VLMResult(**d)
    except Exception as e:
        logger.warning("캐시 로드 실패 %s: %s", key, e)
        return None


def _save_cache(key: str, result: VLMResult) -> None:
    p = VLM_CACHE_DIR / key
    try:
        d = result.to_dict()
        d["cached"] = False  # 저장 시점은 fresh
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("캐시 저장 실패 %s: %s", key, e)


# ── Ollama 호출 ──────────────────────────────────────────────────────────────
def _call_ollama(image_bytes: bytes, prompt: str, model: str = VLM_MODEL) -> str:
    img_b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
        "options": {
            "temperature": 0.1,  # 표 추출은 결정적이어야
            "num_predict": 2000,
        },
    }
    r = requests.post(
        f"{VLM_BASE_URL}/api/generate",
        json=payload,
        timeout=VLM_TIMEOUT_SEC,
    )
    r.raise_for_status()
    data = r.json()
    return (data.get("response") or "").strip()


# ── 이미지 인코딩 ────────────────────────────────────────────────────────────
def encode_image(image: Image.Image, max_dim: int = 1600, fmt: str = "PNG") -> bytes:
    """PIL Image → bytes. 너무 크면 비율 유지하며 축소."""
    w, h = image.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


# ── 검증 게이트 ─────────────────────────────────────────────────────────────
def _validate_table_output(markdown: str, expected_density: int = 0) -> tuple[bool, str]:
    """VLM 표 출력 검증. (valid, reason) 반환. 엄격 모드."""
    text = markdown.strip()
    if text == "NOT_A_TABLE":
        return True, "VLM이 표 아니라고 판단"
    if not text:
        return False, "빈 응답"
    if not text.startswith("|"):
        return False, "마크다운 표 형식 아님"
    rows = [l for l in text.splitlines() if l.strip().startswith("|")]
    if len(rows) < 2:
        return False, f"행 수 부족 ({len(rows)})"

    # 헤더 + 구분선 검증
    sep_idx = None
    for i, r in enumerate(rows):
        if re.match(r"^\|\s*[-:]+\s*(\|\s*[-:]+\s*)*\|\s*$", r):
            sep_idx = i
            break
    if sep_idx is None or sep_idx > 1:
        return False, "구분선(|---|---|) 없음 또는 위치 이상"

    # 모든 행 열 수 일치 (구분선 제외, 엄격)
    col_counts = []
    for i, r in enumerate(rows):
        if i == sep_idx:
            continue
        # 양 끝 |는 카운트 제외, 셀 개수 = | 개수 - 1
        n = r.count("|") - 1
        col_counts.append(n)
    if len(set(col_counts)) > 1:
        return False, f"행별 열 수 불일치 {col_counts}"

    # 환각 의심
    if expected_density > 0 and len(text) > expected_density * 2.5:
        return False, f"과잉 생성 의심 (out={len(text)} vs ~{expected_density})"
    if expected_density > 0 and len(text) < expected_density * 0.4:
        return False, f"누락 의심 (out={len(text)} vs ~{expected_density})"
    return True, f"OK ({len(rows)}행 {col_counts[0] if col_counts else 0}열)"


def _validate_page_output(text: str, expected_density: int = 0) -> tuple[bool, str]:
    """페이지 추출 결과 검증."""
    if not text.strip():
        return False, "빈 응답"
    if expected_density > 0:
        if len(text) < expected_density * 0.3:
            return False, f"누락 의심 (out={len(text)} vs ~{expected_density})"
        if len(text) > expected_density * 3.0:
            return False, f"과잉 생성 의심 (out={len(text)} vs ~{expected_density})"
    return True, "OK"


# ── 공개 API ────────────────────────────────────────────────────────────────
def extract_table(
    image: Image.Image,
    expected_text_density: int = 0,
    use_cache: bool = True,
    retry_on_fail: bool = True,
) -> VLMResult:
    """이미지에서 표 추출 → 마크다운 반환."""
    img_bytes = encode_image(image)
    cache_key = _cache_key(img_bytes, "table")

    if use_cache:
        cached = _load_cache(cache_key)
        if cached:
            logger.debug("VLM 캐시 hit: %s", cache_key)
            return cached

    t0 = time.monotonic()
    try:
        response = _call_ollama(img_bytes, PROMPT_TABLE)
    except Exception as e:
        logger.warning("VLM 호출 실패: %s", e)
        return VLMResult(
            text="", model=VLM_MODEL, prompt_version=PROMPT_VERSION,
            cached=False, duration_sec=time.monotonic() - t0,
            valid=False, validation_reason=f"호출 실패: {type(e).__name__}",
        )
    duration = time.monotonic() - t0

    valid, reason = _validate_table_output(response, expected_text_density)

    # 1회 재시도 — 더 보수적 프롬프트로
    if not valid and retry_on_fail:
        logger.info("VLM 표 검증 실패(%s) — 재시도", reason)
        try:
            retry_prompt = PROMPT_TABLE + "\n\n주의: 환각 금지. 셀 내용은 원본에 보이는 그대로만."
            response2 = _call_ollama(img_bytes, retry_prompt)
            valid2, reason2 = _validate_table_output(response2, expected_text_density)
            if valid2:
                response = response2
                valid = True
                reason = f"재시도 성공 ({reason})"
        except Exception as e:
            logger.warning("VLM 재시도 실패: %s", e)

    result = VLMResult(
        text=response, model=VLM_MODEL, prompt_version=PROMPT_VERSION,
        cached=False, duration_sec=duration,
        valid=valid, validation_reason=reason,
    )
    if use_cache and valid:
        _save_cache(cache_key, result)
    return result


def extract_page(
    image: Image.Image,
    expected_text_density: int = 0,
    use_cache: bool = True,
) -> VLMResult:
    """페이지 전체 이미지에서 텍스트·구조 추출."""
    img_bytes = encode_image(image)
    cache_key = _cache_key(img_bytes, "page")

    if use_cache:
        cached = _load_cache(cache_key)
        if cached:
            return cached

    t0 = time.monotonic()
    try:
        response = _call_ollama(img_bytes, PROMPT_PAGE)
    except Exception as e:
        return VLMResult(
            text="", model=VLM_MODEL, prompt_version=PROMPT_VERSION,
            cached=False, duration_sec=time.monotonic() - t0,
            valid=False, validation_reason=f"호출 실패: {type(e).__name__}",
        )
    duration = time.monotonic() - t0
    valid, reason = _validate_page_output(response, expected_text_density)

    result = VLMResult(
        text=response, model=VLM_MODEL, prompt_version=PROMPT_VERSION,
        cached=False, duration_sec=duration,
        valid=valid, validation_reason=reason,
    )
    if use_cache and valid:
        _save_cache(cache_key, result)
    return result


# ── 폴백 트리거 판정 ────────────────────────────────────────────────────────
def needs_table_fallback(table_extracted) -> tuple[bool, str]:
    """pdfplumber `table.extract()` 결과를 평가해 VLM 폴백 필요 여부 판단.

    NOTE: 호출 전 `is_real_table()`로 진짜 표인지 사전 검증 권장.
    """
    if not table_extracted:
        return True, "추출 결과 없음"

    rows = [r for r in table_extracted if r]
    if not rows:
        return True, "유효 행 없음"

    flat_cells = [c for r in rows for c in r]
    if not flat_cells:
        return True, "셀 없음"

    # 1. 1×1 단일 셀
    if len(rows) == 1 and len(rows[0]) == 1:
        return True, "1×1 단일 셀 (구조 추출 실패)"

    # 2. 빈 셀 비율 50%+
    empty = sum(1 for c in flat_cells if not (str(c).strip() if c else ""))
    if empty / len(flat_cells) > 0.5:
        return True, f"빈 셀 {empty/len(flat_cells)*100:.0f}% (병합 셀 의심)"

    # 3. 행 길이 불일치 (rowspan/colspan 의심)
    row_lens = [len(r) for r in rows]
    if len(set(row_lens)) > 1 and max(row_lens) - min(row_lens) >= 2:
        return True, f"행 길이 편차 {row_lens} (rowspan/colspan 의심)"

    # 4. 줄바꿈 셀 비율 — pdfplumber가 multi-line cell을 잘못 분리한 경우
    multi_line = sum(1 for c in flat_cells if c and "\n" in str(c))
    if multi_line / len(flat_cells) > 0.3:
        return True, f"줄바꿈 셀 {multi_line/len(flat_cells)*100:.0f}% (셀 분리 이상 의심)"

    # 5. 같은 행 내 같은 값 반복 (병합 셀 펼치기 후유증)
    for r in rows:
        non_empty = [str(c).strip() for c in r if c and str(c).strip()]
        if len(non_empty) >= 3 and len(set(non_empty)) <= len(non_empty) // 2:
            return True, "동일 행 내 셀 값 중복 (병합 셀 펼침 의심)"

    return False, "정상"


def is_real_table(table_extracted, table_bbox: tuple, page_size: tuple) -> tuple[bool, str]:
    """진짜 표인지 사전 검증 — 가짜 표(절차 흐름도, 페이지 전체 박스 등) 제외.

    Args:
        table_extracted: pdfplumber `table.extract()` 결과
        table_bbox: (x0, y0, x1, y1)
        page_size: (page_width, page_height)

    Returns:
        (is_real, reason)
    """
    if not table_extracted:
        return False, "추출 결과 없음"

    rows = [r for r in table_extracted if r]
    if not rows:
        return False, "유효 행 없음"

    n_rows = len(rows)
    max_cols = max(len(r) for r in rows)
    flat = [c for r in rows for c in r]

    # 1. 최소 크기 — 적어도 2×2 이상
    if n_rows < 2 or max_cols < 2:
        return False, f"표 크기 부족 ({n_rows}×{max_cols})"

    # 2. 페이지의 80% 이상 차지 → 본문이 박스로 둘러싸인 경우
    if page_size and table_bbox:
        pw, ph = page_size
        tw = table_bbox[2] - table_bbox[0]
        th = table_bbox[3] - table_bbox[1]
        area_ratio = (tw * th) / (pw * ph) if (pw * ph) > 0 else 0
        if area_ratio > 0.80 and n_rows < 10:
            return False, f"페이지 {area_ratio*100:.0f}% 차지 + 행 적음 — 본문 박스 의심"

    # 3. 빈 셀 90%+ → 표 아님 (단순 박스)
    empty = sum(1 for c in flat if not (str(c).strip() if c else ""))
    if empty / len(flat) > 0.90:
        return False, f"빈 셀 {empty/len(flat)*100:.0f}% — 표 아님"

    # 4. 모든 셀 같은 내용 → 가짜
    non_empty = [str(c).strip() for c in flat if c and str(c).strip()]
    if non_empty and len(set(non_empty)) == 1:
        return False, "모든 셀 동일 내용"

    return True, "진짜 표"


def needs_page_fallback(text: str, has_significant_image: bool = False) -> tuple[bool, str]:
    """페이지 전체 폴백 필요 여부."""
    if not text.strip():
        return True, "텍스트 추출 0자"
    if len(text) < 50 and has_significant_image:
        return True, f"텍스트 부족({len(text)}자) + 이미지 있음"
    # 깨진 문자 비율
    bad = len(re.findall(r"[㐀-䶿--Ȁ-ʯ]", text))
    if bad / max(len(text), 1) > 0.15:
        return True, f"깨짐 비율 {bad/len(text)*100:.0f}%"
    return False, "정상"
