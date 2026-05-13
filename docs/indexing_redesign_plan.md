# 인덱싱 재설계 계획 — Raw PDFs 우선

**작성일**: 2026-04-25
**범위**: PDF 추출·청킹·메타데이터 전면 재설계 → 이후 크롤 데이터 처리
**목표**: 메타데이터 90%+ 커버리지, 표/이미지 VLM 폴백, 계층 경로 stack 보존, 슬라이딩 윈도우 맥락 유지

---

## 1. 현재 코드 자산 점검

### ✅ 있는 것

| 컴포넌트 | 위치 | 상태 |
|----------|------|------|
| 디지털 PDF 추출 | [app/pdf/digital_extractor.py](app/pdf/digital_extractor.py) | ✅ PyMuPDF + pdfplumber 정상 |
| 표 파싱 | [app/pdf/table_parser.py](app/pdf/table_parser.py) | ✅ pdfplumber 기반 |
| 시간표 전용 파서 | [app/pdf/timetable_parser.py](app/pdf/timetable_parser.py) | ✅ |
| OCR (스캔 PDF) | [app/pdf/ocr_extractor.py](app/pdf/ocr_extractor.py) | ✅ Surya 사용 |
| **섹션 추적기 (폰트 기반)** | [app/pdf/section_tracker.py](app/pdf/section_tracker.py) | ⚠️ **구현됨, ChromaDB 저장 0%** |
| 슬라이딩 윈도우 | [app/ingestion/chunking.py:89](app/ingestion/chunking.py#L89) | ⚠️ overlap 있음, 헤더 경계 무시 |
| 청크 ID 생성 | [chunking.py:83](app/ingestion/chunking.py#L83) | ⚠️ text[:50] 해시 충돌 |
| 학번 감지 | [chunking.py:45](app/ingestion/chunking.py#L45) | ⚠️ regex 96% 폴백 |

### ❌ 없는 것

| 항목 | 필요성 |
|------|--------|
| **VLM 폴백** (표·이미지 추출 실패 시) | Claude Vision API 활용 |
| **헤더 Stack 깊이 3+ 지원** | 현재 L1>L2만, L3 (가/나/다) 누락 |
| **헤더 경계 청킹** | 현재 슬라이딩이 섹션 가로지름 |
| **메타데이터 검증 게이트** | 빈 메타 저장 차단 로직 없음 |
| **content hash dedup** | 동일 내용 중복 색인 방지 |
| **HWP 대체 추출** (LibreOffice 변환 등) | pyhwp 출력 깨짐 (목차 영역 21%+) |

---

## 2. 재설계 — PDF 처리 파이프라인 (단계별)

### Stage A — 페이지 분류 (Detector 강화)

```
PDF → DigitalDetector → {
    digital: 텍스트 추출 정상 페이지 (95%+)
    scanned: OCR 필요 페이지
    table_heavy: 표 비중 큰 페이지 (별도 처리)
    image_only: 이미지만 있는 페이지
}
```

기존 [detector.py](app/pdf/detector.py)에 **표/이미지 비중 분류** 추가.

### Stage B — 텍스트·구조 추출 (현재 + 보강)

| 페이지 유형 | 추출 방법 | 보완 |
|------------|---------|------|
| Digital | PyMuPDF `get_text("dict")` (블록·라인 단위) | ✅ 유지 |
| 표 포함 | pdfplumber `extract_tables()` | ⚠️ **실패 시 VLM 크롭** |
| 스캔 | Surya OCR | ✅ 유지 |
| 아이콘/기호 (PUA ``) | ToUnicode CMap 미지원 폰트 처리 | **PUA → 본문 영향 없는 안전한 치환 (예: `■`)** |

### Stage C — VLM 폴백 (NEW)

**언제 호출**:
- 표 추출 결과가 **빈 셀 50%+** 또는 **행/열 수 비정상** (1×1 단일 셀)
- 페이지에 **이미지+캡션** 패턴인데 텍스트 추출량 < 50자
- HWP에서 추출했는데 깨진 문자 비율 > 20%

**파이프라인**:
```python
# 의사 코드
def extract_with_vlm_fallback(page):
    text = digital_extract(page)
    tables = pdfplumber_extract_tables(page)
    
    # 표 품질 검증
    for table in tables:
        if is_extraction_broken(table):
            bbox = table.bbox
            cropped_img = page.to_image(resolution=200).crop(bbox)
            markdown_table = call_vlm(
                image=cropped_img,
                prompt="이 표를 마크다운 표로 정확히 변환해줘. 헤더와 셀을 보존."
            )
            replace_in_chunks(table, markdown_table)
    
    # 페이지에 이미지만 있고 텍스트 부족
    if len(text) < 50 and has_significant_images(page):
        full_page_img = page.to_image(resolution=150)
        page_summary = call_vlm(
            image=full_page_img,
            prompt="이 페이지의 정보를 텍스트로 정확히 추출. 도표는 마크다운으로."
        )
        text = page_summary
    
    return text, tables
```

**VLM 선택**:
- **Claude Vision API** (Haiku 4.5 또는 Sonnet 4.6) — `.env`에 ANTHROPIC_API_KEY 보유
- 비용 추정: 표 한 개 추출 ~ $0.001 (Haiku 기준), 학사안내 96페이지 전체 약 $0.5 ~ $1
- 한 번 인덱싱하면 변경 시까지 재호출 안 함 → 비용 작음

**캐시 전략**:
- 페이지 SHA256 + bbox 해시를 키로 VLM 응답 디스크 캐시
- `data/vlm_cache/{hash}.json` 저장
- 재인덱싱 시 동일 페이지·영역 재호출 안 함

### Stage D — 헤더 Stack 기반 계층 추출 (재설계 핵심)

**현 코드 한계**:
- [section_tracker.py:62-79](app/pdf/section_tracker.py#L62-L79) — L1, L2만 누적, L3 이하 무시
- 폰트 크기 임계값 하드코딩 (`_LEVEL1_MIN=14.5`, `_LEVEL2_MIN=13.5`)
- 같은 페이지 내 L2 변경 추적 안 함 (페이지 단위로만 누적)

**재설계 — `SectionStack` 클래스 도입**:

```python
class SectionStack:
    """헤더 출현 순으로 계층 경로를 stack으로 관리.

    push(level, title): 새 헤더 만나면 같거나 깊은 level 모두 pop 후 push.
    path: 현재 활성 경로 (" > " 조인)
    titles: 현재 활성 제목 리스트
    depth: 현재 깊이
    """

    def __init__(self):
        self._stack: list[tuple[int, str]] = []

    def push(self, level: int, title: str) -> None:
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title))

    @property
    def path(self) -> str:
        return " > ".join(t for _, t in self._stack)

    @property
    def titles(self) -> list[str]:
        return [t for _, t in self._stack]

    @property
    def depth(self) -> int:
        return len(self._stack)
```

**헤더 레벨 분류 규칙** (하드코딩 → 폰트 크기 분포 기반 자동 결정):

```
1. PDF 전체에서 폰트 크기 분포 수집
2. 본문 폰트(최빈값) 식별 — 보통 10~11pt
3. 본문보다 큰 폰트들을 클러스터링 (예: 11.5/13/14.5/16)
4. 각 클러스터를 L1, L2, L3, ... 순서 부여
5. 패턴 보조: "Ⅰ./Ⅱ." → L1, "1./2." → L2, "가./나." → L3, "(1)/(2)" → L4
```

**페이지 단위 → 블록 단위 추적**:
```python
# 기존: 페이지마다 1개 L1, 1개 L2 (페이지 끝에 청크 기록)
# 신규: 페이지 내 텍스트 블록 순회하며 헤더 만날 때마다 stack 갱신
for page in pdf:
    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"])
            max_size = max(s["size"] for s in line["spans"])
            level = classify_header_level(text, max_size)  # None or 1~5
            
            if level is not None:
                section_stack.push(level, text)
                continue  # 헤더 자체는 본문 아님
            
            # 본문 라인 → 청크 누적, stack snapshot 저장
            current_chunk.append(text, section_stack.titles, section_stack.path)
```

### Stage E — 청킹 (헤더 경계 + 슬라이딩 윈도우 결합)

**원칙**:
1. **섹션 경계에서 강제 분할** — L1/L2 헤더 만나면 현 청크 마감
2. **섹션 내에서만 슬라이딩** — overlap이 다른 섹션 내용 섞지 않음
3. **min/max 강제** — 150~700자 hard range

**의사 코드**:
```python
def chunk_page_with_sections(page, section_stack):
    chunks = []
    current = ChunkBuilder(section_path=section_stack.path)
    
    for line in page_lines(page):
        if line.is_header:
            # 1) 현 청크 마감 (max 검증 후 sliding 분할)
            if current.length >= MIN_CHUNK_LEN:
                chunks.extend(_finalize(current))
            
            # 2) stack 갱신
            section_stack.push(line.level, line.text)
            
            # 3) 새 청크 시작 (헤더는 청크 메타에만, 본문엔 미포함)
            current = ChunkBuilder(section_path=section_stack.path)
        else:
            current.append(line.text)
            
            # 4) 단일 청크가 너무 길면 슬라이딩 분할 (단, 같은 섹션 내에서만)
            if current.length >= CHUNK_MAX:
                chunks.extend(_finalize_with_sliding(current, overlap=100))
                current = current.continue_with_overlap()
    
    # 페이지 끝 — 남은 부분 마감
    if current.length >= MIN_CHUNK_LEN:
        chunks.extend(_finalize(current))
    
    return chunks
```

**파라미터 (.env로 노출)**:
```
CHUNK_MIN_LEN=150
CHUNK_MAX_LEN=700  # 현 500 → 700 (헤더 단위 보존 우선)
CHUNK_OVERLAP=100  # 슬라이딩 시 맥락 보존
CHUNK_HARD_CAP=1000  # 절대 상한
TABLE_MAX_LEN=2500  # 표는 별도 (현 1500 → 2500, 학번별 표 보존)
```

### Stage F — 메타데이터 강제 주입

**모든 청크에 강제 보장 필드**:

| 필드 | 출처 | 폴백 |
|------|------|------|
| `source_file` | 인제스트 인자 | (필수, 누락 시 인제스트 거부) |
| `source_hash` | 파일 SHA256 (앞 1KB) | (필수) |
| `doc_type` | 인제스트 인자 | "unknown" |
| `page_number` | PDF 페이지 (1-base) | (PDF면 필수, JSON 등은 0) |
| `page_total` | PDF 총 페이지 | (필수) |
| **`section_path`** | SectionStack.path | "" 허용 (헤더 없는 페이지) |
| **`section_titles`** | SectionStack.titles (JSON 직렬화) | "[]" |
| **`section_depth`** | SectionStack.depth | 0 |
| `cohort_from`/`cohort_to` | detect_cohort 강화 | 폴백 시 명시 (`cohort_inferred=true`) |
| `student_types` | 본문에서 추출 | "[]" (빈 배열, NULL 아님) |
| `department` | 학사안내·시간표·공지 헤더에서 추출 | "" |
| `category` | doc_type 또는 키워드 매칭 | "" |
| `extraction_method` | "digital" / "ocr" / "vlm_table" / "vlm_page" | (필수) |
| `chunk_position` | 페이지 내 N번째 청크 | (필수, ID 충돌 방지용) |

**chunk_id 재설계**:
```
{source_hash[:8]}_{page_number:03d}_{chunk_position:02d}_{text_sha[:8]}
예: a3f2b1c0_018_03_d5e9f1a2

→ source/page/position 명시로 ID 충돌 0%
```

### Stage G — 검증 게이트 (인제스트 직전)

```python
def validate_chunk(chunk) -> bool:
    """필수 메타 누락 시 거부, 오염 청크 차단."""
    REQUIRED = ["source_file", "source_hash", "doc_type", "page_number", 
                "section_path", "section_depth", "extraction_method", "chunk_position"]
    for k in REQUIRED:
        if k not in chunk.metadata or chunk.metadata[k] is None:
            log.warning("필수 메타 누락 거부: %s missing %s", chunk.id, k)
            return False
    
    # 길이 검증
    if not (MIN_CHUNK_LEN <= len(chunk.text) <= HARD_CAP):
        log.warning("길이 위반 거부: %s len=%d", chunk.id, len(chunk.text))
        return False
    
    # 깨짐 비율 검증 (PUA·CJK Ext-A 비율 > 30%)
    bad_ratio = count_garbage_chars(chunk.text) / len(chunk.text)
    if bad_ratio > 0.30:
        log.warning("깨짐 거부: %s bad=%.0f%%", chunk.id, bad_ratio*100)
        return False
    
    return True
```

---

## 3. 구현 순서 (PDF 우선 → 크롤 후순위)

### Week 1 — 기반 작성

| 일 | 작업 | 산출물 |
|----|------|--------|
| 1 | `SectionStack` 클래스 + 폰트 분포 자동 분석 | `app/pdf/section_stack.py` (NEW) |
| 1-2 | 헤더 경계 청킹 로직 — `chunk_page_with_sections()` | `app/ingestion/chunking_v2.py` (병행) |
| 2 | `chunk_id` 재설계 + 검증 게이트 | chunking_v2 내 |
| 2-3 | 메타 필드 강제 주입 — `extraction_method`, `chunk_position` 등 | digital_extractor 수정 |
| 3 | `detect_cohort` regex 강화 + 본문 외 추출 (파일명/제목) | chunking.py 수정 |
| 4 | 학사안내 PDF 단일 파일로 검증 (메타 90%+ 커버 확인) | 단위 테스트 |
| 5 | **VLM 폴백 모듈** — `app/pdf/vlm_extractor.py` (NEW) — Claude Vision API + 캐시 | 표·이미지 추출 검증 |

### Week 2 — PDF 전체 재색인 + 메타 검증

| 일 | 작업 |
|----|------|
| 6 | content hash dedup — `data/pdfs/*.pdf` 중복 검출·정리 |
| 6-7 | PDF 전체 재색인 (`scripts/ingest_pdf.py`만 호출) |
| 7-8 | 재색인 결과 메타 커버리지 측정 + 깨짐 비율 검증 |
| 8 | 평가 — Contains-F1 측정 (재현성 ±0.02 확인) |
| 9 | 수동 샘플링 — 학번별·학과별 응답 정확성 20건 |
| 10 | PDF 단계 회고 + 크롤 단계 진입 결정 |

### Week 3 — 크롤 데이터 처리

| 일 | 작업 |
|----|------|
| 11 | HWP 추출 대안 검증 — LibreOffice headless 변환 vs `hwp5txt` 비교 |
| 11-12 | HWP 추출 파이프라인 교체 |
| 12 | 크롤러 본문 정제 — `<nav>`, `<aside>` 제거, sidebar 패턴 필터 |
| 13 | 첨부 PDF/HWP dedup (같은 공지의 중복) |
| 13-14 | 공지·첨부 재색인 + 메타 검증 |
| 14-15 | 통합 평가 + 회귀 테스트 + 베이스라인 갱신 |

---

## 4. VLM 폴백 — 구체 설계

### 호출 시점 결정 트리

```
PDF 페이지 처리
  ├─ 텍스트 추출 정상 (>200자) ?
  │   ├─ Yes → 표 있나?
  │   │       ├─ Yes → 표 추출 정상?
  │   │       │       ├─ Yes → 디지털만 사용 (VLM 호출 X)
  │   │       │       └─ No  → 표 영역 크롭 → VLM 표 추출
  │   │       └─ No  → 디지털만 사용
  │   └─ No  → 페이지 전체 이미지 → VLM 페이지 추출
  └─ 스캔 페이지 → Surya OCR (기존)
```

### 비용·성능 추정 (Claude Haiku 4.5 기준)

| 시나리오 | 학사안내 96p (예상) | 비용 |
|---------|------------------|------|
| 디지털 추출만 (현재) | ~10분 | $0 |
| + VLM 표 폴백 (표 페이지 ~20p) | +2분 | ~$0.05 |
| + 페이지 전체 (스캔 가이드북 ~30p) | +5분 | ~$0.10 |
| **합** | ~17분 | ~$0.15 |

전체 인덱싱 (학사안내 + 시간표 + 공지 + 첨부)도 **$5 미만 예상**.

### 캐시 키 설계

```python
cache_key = f"{file_sha[:16]}_{page_num:03d}_{bbox_hash[:8]}_{prompt_version}"
# 파일·페이지·영역·프롬프트 버전 모두 동일 시 재사용
# 프롬프트 버전 변경하면 자동 무효화
```

---

## 5. 슬라이딩 윈도우 — 맥락 보존 강화

### 현재 문제

- `chunking.py:89` `sliding_window()` — 단순 글자 수 기준
- 청크 경계가 **문장·단락 가운데에서 잘림**
- overlap 없거나 minimal — 다음 청크에서 맥락 단절

### 재설계

```python
def sliding_chunks(text, max_len=700, overlap=100, prefer_boundary=True):
    """
    문단·문장 경계를 우선 존중하면서 슬라이딩.
    
    Args:
        prefer_boundary: True면 max_len 근처에서 단락(\n\n) → 문장(. ?!)
                        → 절(,) 순으로 자연스러운 경계 탐색
    """
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        if end < len(text) and prefer_boundary:
            # max_len 80% 지점부터 boundary 탐색
            search_start = start + int(max_len * 0.8)
            boundary = _find_boundary(text, search_start, end)
            if boundary > start:
                end = boundary
        
        chunk = text[start:end].strip()
        if len(chunk) >= MIN_CHUNK_LEN:
            chunks.append(chunk)
        
        # overlap 보존
        start = max(start + 1, end - overlap)
    
    return chunks


def _find_boundary(text, start, end):
    """단락 → 문장 → 절 순으로 boundary 탐색."""
    # 1) 단락 (\n\n)
    pos = text.rfind("\n\n", start, end)
    if pos > 0:
        return pos + 2
    # 2) 문장 종료 (다., 까.,. ?!)
    for p in (".", "?", "!", "다."):
        pos = text.rfind(p, start, end)
        if pos > 0:
            return pos + len(p)
    # 3) 절 (,)
    pos = text.rfind(",", start, end)
    if pos > 0:
        return pos + 1
    return end
```

### overlap 효과 — 맥락 누수 방지

청크 1 끝: `"...이수학점은 학번에 따라 다음과 같이 다르며"`
청크 2 시작 (overlap 0): `"2024학번은 18학점, 2023학번은 19학점..."` → 맥락 끊김
청크 2 시작 (overlap 100): `"...학번에 따라 다음과 같이 다르며 2024학번은 18학점..."` → 맥락 보존

---

## 6. 과청킹 검증

### 검증 지표

```python
def chunk_health_metrics(chunks):
    return {
        "total": len(chunks),
        "avg_len": mean(len(c.text) for c in chunks),
        "p50_len": median(len(c.text) for c in chunks),
        "min_len": min(len(c.text) for c in chunks),
        "max_len": max(len(c.text) for c in chunks),
        # 과청킹 지표
        "tiny_chunks": sum(1 for c in chunks if len(c.text) < 100),  # 너무 짧음
        "single_sentence": sum(1 for c in chunks if c.text.count(".") <= 1),
        "header_only": sum(1 for c in chunks if is_header_only(c.text)),
        "duplicate_first_50": dup_count_by_prefix(chunks, n=50),
    }
```

**경고 임계**:
- `tiny_chunks` > 5% → 과청킹 의심
- `single_sentence` > 30% → 문장 단위로 너무 잘게
- `duplicate_first_50` > 10% → 중복 인덱싱 (헤더 반복)

---

## 7. 진행 전 확인 요청

### 결정 필요

1. **VLM 모델 선택**: Claude Haiku 4.5 (저렴, 빠름) vs Sonnet 4.6 (정확) vs Opus 4.7 (최고). **Haiku로 시작 + 표 추출 정확도 보고 Sonnet 옵션** 추천
2. **캐시 위치**: `data/vlm_cache/` (gitignore 추가) — 동의?
3. **재색인 시 기존 ChromaDB**: `data/chromadb_new/` 백업 후 빌드 → 검증 후 swap. 다운타임 약 4-5h. 새벽 진행 OK?
4. **청크 크기 변경 (500→700)**: 재현성 회귀 가능. 평가셋으로 필수 검증
5. **HWP 처리**: Week 1엔 HWP 일단 제외 (PDF만 우선). Week 3에 LibreOffice 변환 도입. OK?
6. **팀원 합의**: 청크 ID·메타 스키마 변경 → KO 파이프라인에도 영향. 사전 공유 필요

### 산출물 (Week 1 종료 시)

- `app/pdf/section_stack.py` (NEW)
- `app/pdf/vlm_extractor.py` (NEW)
- `app/ingestion/chunking_v2.py` (NEW, 병행 운용)
- `scripts/ingest_pdf_v2.py` (NEW, 단일 PDF 재색인 검증용)
- 학사안내 PDF 1개로 검증 결과 보고서

---

## 8. 의존성·도구 추가

| 패키지 | 용도 | 추가 |
|--------|------|------|
| `anthropic` | Claude Vision API | `requirements.txt`에 추가 |
| `Pillow` | 이미지 크롭·인코딩 | 이미 있음 (PyMuPDF 의존) |
| (옵션) `lxml`, `readability-lxml` | 크롤 본문 정제 (Week 3) | Week 3에 |

---

## 9. 진행 시작 전 마지막 점검

이 문서는 **PDF 처리 우선**의 큰 그림. 시작 시 다음 순서:

1. 위 결정 6가지 답변 받기
2. `SectionStack` 클래스 작성 + 단위 테스트
3. 학사안내 PDF로 폰트 분포 분석 → 헤더 임계값 자동 결정 검증
4. `chunking_v2.py` 작성 — 헤더 경계 청킹
5. 학사안내 PDF 단일 재색인 → 메타 커버리지 90%+ 확인
6. VLM 폴백 도입 → 표 추출 정확성 비교

진행 OK하면 (1)부터 시작합니다.
