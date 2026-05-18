# Step 6 — 글로벌 싱글톤 라이프사이클

**측정일**: 2026-05-13
**대상**: 모듈 레벨에서 `_name = None` 또는 `_name: Optional[T] = None` 패턴 — 21건 ast 추출
**production 핵심 (app/backend)**: 14건. 나머지 7건은 scripts/ 평가 도구로 production 무영향.

## 1. production 싱글톤 14건 매트릭스

| # | 파일 | line | 변수 | 초기화 함수 | 락 | warmup |
|---:|---|---:|---|---|---|---|
| 1 | `app/shared_resources.py` | 34 | `_embedder` | `get_embedder()` | `_embedder_lock` | lazy (init_all() L64에서 강제) |
| 2 | `app/shared_resources.py` | 35 | `_chroma_store` | `get_chroma_store()` | `_chroma_lock` | lazy (init_all() L84-88 warm query) |
| 3 | `app/shared_resources.py` | 36 | `_translator` | `get_translator()` | `_translator_lock` | **백그라운드 스레드**(L68-74) — M2M-100 cold start 방지 |
| 4 | `app/shared_resources.py` | 37 | `_bm25_index` | `get_bm25_index()` | `_bm25_lock` | lazy (build() 시 ~1초) |
| 5 | `app/shared_resources.py` | 38 | `_reranker_model` | `get_reranker_model()` | `_reranker_lock` | lazy + init_all() L70-73에서 강제 + warm predict L76-80 |
| 6 | `backend/dependencies.py` | 14 | `_analyzer` | `init_all()` | `_lock`(공통) | sync (lifespan startup) |
| 7 | `backend/dependencies.py` | 15 | `_router` | `init_all()` | `_lock` | sync (lifespan startup) |
| 8 | `backend/dependencies.py` | 16 | `_merger` | `init_all()` | `_lock` | sync |
| 9 | `backend/dependencies.py` | 17 | `_generator` | `init_all()` | `_lock` | sync |
| 10 | `backend/dependencies.py` | 18 | `_validator` | `init_all()` | `_lock` | sync |
| 11 | `backend/dependencies.py` | 19 | `_chat_logger` | `init_all()` | `_lock` | sync |
| 12 | `app/pipeline/query_understanding.py` | 54 | `_analyzer_singleton` | `_get_analyzer()` | (없음) | lazy at first `understand()` rule fallback |
| 13 | `app/contacts/dept_search.py` | 238 | `_searcher` | `get_dept_searcher()` | (없음) | lazy at first contact 쿼리 |
| 14 | `app/pipeline/community_selector.py` | 133 | `_default_selector` | `get_default_selector()` | (없음) | lazy |

**락 없는 lazy 4건**: #12, #13, #14 + `backend/crypto.py:_cipher`. race condition 이론적으로 가능 (multi-worker uvicorn 동시 첫 호출 시).

## 2. 라이프사이클 단계

```
[FastAPI lifespan startup]
  └─ backend/main.py: init_all() 호출
      └─ backend/dependencies.py:init_all()
          ├─ get_chroma_store()  ── shared_resources._chroma_store 생성
          │   └─ get_embedder()  ── shared_resources._embedder 생성 (model property 강제 호출)
          ├─ get_bm25_index()   ── _bm25_index 생성 + build()
          ├─ AcademicGraph()    ── module-level 싱글톤 아님, init_all 내부 변수
          ├─ QueryAnalyzer / QueryRouter / ContextMerger / AnswerGenerator / ResponseValidator / ChatLogger 생성
          ├─ get_reranker_model() ── _reranker_model 생성 (CrossEncoder) + warm predict
          └─ _initialized = True

[runtime 첫 요청]
  ├─ chat.py:get_analyzer() → dependencies._analyzer  ★ 이미 초기화 완료
  ├─ chat.py:_format_contact_answer() → contacts.get_dept_searcher() → _searcher lazy 생성 ★ 첫 요청 시 ~50ms 비용
  ├─ chat.py:query_understanding.understand() rule_fallback → _get_analyzer() → _analyzer_singleton lazy ★ 첫 fallback 시 ~200ms 비용
  └─ shared_resources._translator (EN context translate 첫 호출) ← background warmup이 완료되어 있으면 미체감

[graceful shutdown]
  ├─ FastAPI lifespan shutdown 호출
  ├─ app/scheduler get_scheduler().shutdown() (있다면)
  └─ 나머지 싱글톤은 프로세스 종료로 회수
```

## 3. 메모리 footprint 추정 (production)

| 싱글톤 | 추정 RAM | 추정 VRAM | 비고 |
|---|---:|---:|---|
| `_embedder` (BGE-M3) | ~1.5 GB | ~2.3 GB (GPU 시) | sentence-transformers + tokenizer |
| `_chroma_store` (HNSW + payloads) | ~250 MB | 0 | ~1,200 청크 |
| `_reranker_model` (bge-reranker-v2-m3) | ~600 MB | ~600 MB (GPU 시) | CrossEncoder |
| `_translator` (M2M-100) | ~2.5 GB | ~2.5 GB (GPU 시) | EN context 번역 |
| `_bm25_index` | ~50 MB | 0 | in-memory inverted index |
| `_analyzer` + `_router` + `_merger` + `_generator` + `_validator` + `_chat_logger` | ~100 MB | 0 | 코드 객체만, 별도 모델 로드 없음 |
| `_searcher` (dept) | ~5 MB | 0 | departments.json |
| `_analyzer_singleton` (understand fallback) | ~50 MB | 0 | QueryAnalyzer 추가 인스턴스 (중복) |
| **합계** | **~5 GB** | **~5.4 GB** | (CPU only 시 RAM 합계) |

**중복 risk**: `_analyzer` (dependencies.py:14) + `_analyzer_singleton` (query_understanding.py:54)는 **동일 클래스 QueryAnalyzer 2 인스턴스 보유**. ~50 MB + 정규식 컴파일 중복. Step 7 PR 후보 — 통합 가능.

## 4. multi-process risk

uvicorn `--workers 4` 운영 시:
- 14 싱글톤 × 4 worker = **56 인스턴스** (모델 weight 중복)
- RAM 약 5 GB × 4 = 20 GB
- VRAM 5.4 GB × 4 = 21.6 GB (단일 GPU에 불가능)
- **현재 운영은 단일 worker가 사실상 강제** — Step 7 권고: workers=1 명시 또는 모델 weight를 SharedMemory로 분리

## 5. lazy 4건 race condition risk

| 싱글톤 | race window | 영향 |
|---|---|---|
| `_searcher` (contacts) | 첫 contact 쿼리 동시 2건 | DeptSearcher 2회 인스턴스 생성 (idempotent, 결과는 동일) → low |
| `_analyzer_singleton` (query_understanding) | 첫 rule_fallback 동시 2건 | QueryAnalyzer 2회 생성 → low (idempotent) |
| `_default_selector` (community_selector) | 첫 호출 동시 2건 | low |
| `_cipher` (backend/crypto.py) | 첫 암호화 호출 동시 2건 | low (idempotent) |

이론적으로는 다 lock 추가가 권장이지만, 4건 모두 결과 자체는 동일 → idempotent. **실제 위험은 미미**. Step 7에서 P3 (선택적 cleanup)로 분류.

## 6. cleanup 권장 우선순위 (Step 7 PR 후보)

| # | 개선 | ROI | 우선순위 |
|---:|---|---|---|
| 1 | `_analyzer` (dependencies) + `_analyzer_singleton` (query_understanding) 통합 → 1 인스턴스 | RAM 50 MB 회수 + 정규식 컴파일 중복 제거 | P1 |
| 2 | lazy 4건에 `threading.Lock()` 추가 | 거의 미미, idempotent로 안전 | P3 |
| 3 | `_translator` warmup 백그라운드 완료 신호 hook 추가 | 첫 EN 쿼리 cold start 가시화 | P2 |
| 4 | `_chroma_store` warm query를 lifespan에 명시 (현재 init_all() 내부 try/except) | 시작 실패 가시화 | P2 |
| 5 | uvicorn workers=1 명시 또는 SharedMemory 분리 | 다중 worker 운영 시 OOM 방지 | P0 (운영 직전) |

## 7. Step 6 산출물 검증 (singletons.md 부분)

| 항목 | 상태 |
|---|---|
| 8 싱글톤 lifecycle 명시 (요구) | ✓ **14건 (production 핵심) + 7건(scripts)** = 21건 추출 |
| 락 상태 매트릭스 | ✓ (1절) |
| warmup 정책 표기 | ✓ (1절) |
| 메모리 footprint 추정 | ✓ (3절) |
| race condition risk | ✓ (5절) |
| cleanup ROI 정렬 | ✓ (6절) |
