# Step 1 — 구조·LOC·복잡도 매트릭스

**측정일**: 2026-05-13
**대상**: `.claude/`, `__pycache__`, `node_modules`, `data/`, `reports/` 제외 199 Python 파일.

## 1. 디렉터리별 책임 + LOC 매트릭스 (23개)

| 디렉터리 | 파일 | 총 LOC | 코드 | 주석 | 빈 줄 | 평균 LOC | 책임 |
|---|---:|---:|---:|---:|---:|---:|---|
| `scripts` | 48 | 17,090 | 12,836 | 1,977 | 2,277 | 356 | 배치: 인제스트·평가·리포트·PDF→그래프 변환 |
| `app/pipeline` | 20 | **7,011** | 4,514 | 1,589 | 908 | 351 | 핵심 RAG: 분석·라우팅·병합·생성·검증 |
| `tests` | 25 | 5,961 | 4,021 | 800 | 1,140 | 238 | 단위·통합 테스트 |
| `backend/routers` | 17 | 4,252 | 3,181 | 474 | 597 | 250 | FastAPI 라우터 (chat·session·admin·user 등) |
| `app/graphdb` | 5 | 4,086 | 3,062 | 584 | 440 | **817** | NetworkX 학사 그래프 (대형 모놀리식) |
| `app/transcript` | 7 | 2,537 | 1,753 | 427 | 357 | 362 | 학사 성적표 파싱·분석 |
| `app/ui` | 3 | 2,368 | 1,888 | 208 | 272 | **789** | Streamlit UI (모놀리식) |
| `pages` | 2 | 1,986 | 1,511 | 234 | 241 | **993** | admin·streamlit 페이지 |
| `app/ingestion` | 7 | 1,958 | 1,255 | 396 | 307 | 280 | PDF/공지 청킹·임베딩 |
| `app/pdf` | 11 | 1,930 | 1,167 | 416 | 347 | 175 | PDF OCR + page_router + section_stack + vlm |
| `app/crawler` | 9 | 1,603 | 1,056 | 297 | 250 | 178 | gnuboard5 공지 크롤러 + 변경감지 |
| `evaluation` | 6 | 1,411 | 562 | 664 | 185 | 235 | 평가셋 (합성/실측) |
| `backend` | 6 | 960 | 500 | 316 | 144 | 160 | FastAPI 코어 (main, database, dependencies) |
| `app` | 4 | 698 | 419 | 176 | 103 | 174 | models, config, shared_resources |
| `backend/schemas` | 7 | 477 | 325 | 39 | 113 | 68 | Pydantic 스키마 |
| `app/vectordb` | 3 | 451 | 288 | 88 | 75 | 150 | ChromaDB 래퍼 |
| `tests/test_api` | 8 | 375 | 240 | 42 | 93 | 47 | API 테스트 |
| `app/contacts` | 2 | 249 | 156 | 53 | 40 | 124 | 부서 연락처 매핑 |
| `app/scheduler` | 2 | 249 | 144 | 54 | 51 | 124 | APScheduler 크롤링 잡 |
| `app/logging` | 2 | 168 | 122 | 23 | 23 | 84 | ChatLogger JSONL |
| `backend/utils` | 2 | 127 | 79 | 28 | 20 | 64 | i18n 등 유틸 |
| `app/embedding` | 2 | 67 | 40 | 14 | 13 | 34 | BGE-M3 임베더 래퍼 |
| (root) | 1 | 18 | 6 | 7 | 5 | 18 | main.py 진입점 stub |
| **합계** | **199** | **56,032** | **41,243** | **8,940** | **5,949** | — | |

## 2. 평균 파일 크기로 본 모놀리식 risk

| 디렉터리 | 평균 LOC | risk |
|---|---:|---|
| `pages` | 993 | ★★★ admin.py 1,571줄 god module |
| `app/graphdb` | 817 | ★★★ academic_graph.py 3,459줄 — 5 파일 중 1개가 전체의 84% |
| `app/ui` | 789 | ★★ Streamlit chat_app.py 2,002줄 |
| `app/transcript` | 362 | ★ parser.py 1,060줄 |
| `scripts` | 356 | ★ pdf_to_graph.py 2,327줄 |
| `app/pipeline` | 351 | ★ chat.py(backend)·query_analyzer/answer_generator/context_merger 1,000+ |

## 3. Cyclomatic Complexity 통계

| 지표 | 값 |
|---|---:|
| 총 함수 수 | **1,677** |
| 평균 CC | 4.8 |
| CC ≥ 10 (복잡) | **192건 (11.4%)** |
| CC ≥ 20 (매우 복잡) | **67건 (4.0%)** |
| CC ≥ 50 (극단치) | **13건** |
| CC ≥ 100 (분해 필수) | **1건** (`build_graph_from_pdf` CC 123) |

## 4. Cyclomatic Complexity Top 20 (실측)

| 순위 | 파일 | line | LOC | CC | 함수 | 진단 |
|---:|---|---:|---:|---:|---|---|
| 1 | `scripts/pdf_to_graph.py` | 1137 | 487 | **123** | `build_graph_from_pdf` | 극단 god function — 분해 필수 |
| 2 | `app/graphdb/academic_graph.py` | 1992 | 330 | 95 | `_query_registration` | 그래프 query 분기 폭주 |
| 3 | `app/graphdb/academic_graph.py` | 2323 | 232 | 86 | `_query_schedule` | 학사일정 분기 |
| **4** | `backend/routers/chat.py` | **469** | **511** | **76** | **`chat_stream`** | **★ stream 진입점** |
| 5 | `app/pipeline/context_merger.py` | 236 | 374 | 75 | `merge` | RRF + adaptive cutoff + budget 일괄 |
| **6** | `backend/routers/chat.py` | **514** | **464** | **73** | **`_inner_generator`** | **★ stream 내부 god** |
| 7 | `app/graphdb/academic_graph.py` | 1128 | 169 | 73 | `query_to_search_results` | 그래프 검색 통합 |
| 8 | `app/pipeline/query_analyzer.py` | 511 | 237 | 67 | `_analyze_en` | EN 쿼리 분석 god |
| 9 | `scripts/pdf_to_graph.py` | 288 | 172 | 57 | `parse_registration_rules` | PDF 파싱 분기 |
| 10 | `app/graphdb/academic_graph.py` | 1717 | 174 | 55 | `_query_graduation` | 학번별 졸업요건 |
| 11 | `app/graphdb/academic_graph.py` | 745 | 210 | 53 | `search_faq` | FAQ 검색 |
| 12 | `app/transcript/parser.py` | 640 | 129 | 53 | `_extract_credits_summary` | 성적표 파싱 |
| 13 | `scripts/ingest_all.py` | 117 | 343 | 52 | `main` | 인제스트 god |
| 14 | `app/pipeline/answer_generator.py` | 300 | 300 | 50 | `_build_prompt` | 프롬프트 구축 |
| 15 | `backend/routers/chat.py` | 233 | 112 | 49 | `_enrich_analysis` | 분석 보강 |
| **16** | `backend/routers/chat.py` | **1002** | **338** | **47** | **`chat_sync`** | **★ sync 진입점** |
| 17 | `scripts/pdf_to_graph.py` | 859 | 94 | 47 | `parse_graduation_reqs` | PDF 졸업요건 |
| 18 | `scripts/make_report.py` | 146 | 496 | 45 | `main` | 리포트 god |
| 19 | `scripts/pdf_to_graph.py` | 748 | 90 | 43 | `parse_second_major_credits` | PDF 복수전공 |
| 20 | `app/pipeline/query_analyzer.py` | 843 | 126 | 42 | `_classify_intent` | Intent 분류 분기 |

## 5. 핵심 발견

1. **chat.py에 god function 3개** — Top 20 안에 4건 (CC 47/49/73/76)
   - `chat_stream` (라인 469, CC 76, 511 LOC) + `_inner_generator` (514, CC 73, 464 LOC) + `chat_sync` (1002, CC 47, 338 LOC) + `_enrich_analysis` (233, CC 49, 112 LOC)
   - 합계 1,425 LOC, 평균 CC 61 — 단일 라우터 파일에 god 4개
2. **app/graphdb/academic_graph.py 3,459줄에 god 4개** — `_query_registration` CC 95, `_query_schedule` CC 86, `query_to_search_results` CC 73, `_query_graduation` CC 55
3. **scripts/pdf_to_graph.py** CC 123 `build_graph_from_pdf` — Top 1, 극단치. PDF 파싱 전체 로직이 한 함수
4. **app/pipeline 5대 모듈**의 god function 모두 Top 20 안: `context_merger.merge`(CC 75), `query_analyzer._analyze_en`(67), `_classify_intent`(42), `answer_generator._build_prompt`(50)

## 6. Step 1 산출물 검증

| 산출물 | 상태 | 위치 |
|---|---|---|
| `loc_by_dir.csv` 23행 (목표 12+) | ✓ 23행 | `01_structure/loc_by_dir.csv` |
| `cc.json` Top 20 | ✓ 1,677 함수 전체 + Top 20 텍스트 | `01_structure/cc.json` + `cc_top20.txt` |
| `dependency_matrix.md` 매트릭스 | ✓ 이 문서 | `01_structure/dependency_matrix.md` |
| `loc_summary.txt` 요약 | ✓ | `01_structure/loc_summary.txt` |

## 7. 다음 Step (Step 2) 진입 조건

- chat.py 두 god function (stream/sync) 식별 ✓ — 진입점 흐름 추적 baseline 확보
- 호출 트리 작성 위한 ast.FunctionDef 좌표 추출 명령 준비
