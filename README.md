# BUFS Academic Chatbot (CAMCHAT / 캠챗)

부산외국어대학교 학사 안내 RAG 챗봇. FastAPI 백엔드 + Next.js 프론트엔드 + 로컬 GPU 기반 하이브리드 검색(Dense + BM25 + Graph + Reranker)으로 학사·장학·수강신청·졸업요건 질문에 답변합니다.

> **4대 원칙 (필수 준수)** — 세부 내용은 `CLAUDE.md` 참조.
> 1. 유연한 스키마 진화  2. 비용·지연 최적화  3. 지식 생애주기 관리  4. 하드코딩 금지

---

## 아키텍처 개요

```
Next.js 프론트엔드  (frontend/, 포트 3000)
      │  ko/en i18n · /admin · SSE 스트리밍
      ▼
NGINX  (reverse proxy, 포트 80) — 프로덕션 전용
      │
      ▼
FastAPI 백엔드  (backend/, 포트 8000, uvicorn 단일 워커)
      │
      ├─ lifespan: 파이프라인 싱글톤 초기화 (Embedder / Reranker / Graph / ChromaDB)
      │
      └─ RAG 파이프라인 (app/pipeline/)
           LanguageDetector   (ko/en <1ms)
           QueryAnalyzer      (Intent · 엔티티 · 학번/학과/학기)
           FollowUpDetector   (단턴 vs follow-up 분기)
           QueryRewriter      (gemma3:4b Ollama 경량 재작성, follow-up 시)
           QueryRouter        (Vector + Graph 병렬 라우팅, department 필터)
              ├─ ChromaDB  (BGE-M3 임베딩 + BM25 하이브리드)
              └─ AcademicGraph  (NetworkX + FAQ 역인덱스 · direct_answer 경로)
           CommunitySelector  (동적 커뮤니티 선택으로 후보 축소)
           Reranker           (BGE-Reranker-v2-m3, GPU, Top-5)
           ContextMerger      (병합 + 토큰 예산 + evidence slicing 토글)
           AnswerGenerator    (LLM — OpenAI 호환 or Ollama 네이티브)
           ResponseValidator  (refusal · 환각 · 자기검증)
           ChatLogger         (JSONL + SQLite chat_messages)
```

**LLM 모델 SSOT**: 모델명·엔드포인트는 `.env` 또는 `app/config.py`가 단일 진실원천. README·코드에 모델명을 박아 넣지 않는다 (원칙 4).

**디렉터리 SSOT**:

| 위치 | 역할 |
|------|------|
| `app/pipeline/` | 검색·답변 파이프라인 (전처리 → 검색·병합 → 생성·검증) |
| `app/crawler/` | gnuboard5 공지 크롤러 + 해시 기반 변경감지 |
| `app/graphdb/` | 학사 규정 그래프 (NetworkX, FAQ 역인덱스) |
| `app/vectordb/` | ChromaDB 래퍼 |
| `app/ingestion/` | PDF/공지 청킹·임베딩·증분 업데이트 |
| `app/scheduler/` | APScheduler 백그라운드 크롤링 잡 |
| `backend/` | FastAPI (chat, admin, transcript, source, health, feedback, user) |
| `frontend/` | Next.js 16 (App Router, ko/en i18n, `/admin`) |
| `scripts/` | 인제스트·평가·빌드 (`ingest_all.py`가 마스터 진입점) |
| `data/crawl_meta/` | `content_hashes.json` (크롤 변경감지 상태) |
| `docs/archive/` | 시점성 스냅샷 (진단 리포트) — 일반 문서 아님 |

---

## 기술 스택

| 구성 | 기술 |
|------|------|
| 프론트엔드 | Next.js 16 (App Router, React 19, TypeScript, Tailwind v4), lucide-react, react-markdown + remark-gfm |
| 백엔드 | FastAPI 0.109+, uvicorn (workers=1), SSE Streaming (sse-starlette), Pydantic v2 |
| LLM | Ollama 네이티브 `/api/chat` 또는 OpenAI 호환 `/v1/chat/completions` (환경변수 `LLM_API_TYPE`로 선택) |
| 쿼리 재작성 | 경량 LLM (기본: 별도 Ollama URL의 `gemma3:4b`, 타임아웃 5s) |
| 임베딩 | BAAI/bge-m3 (multilingual, 1024d, GPU) |
| 리랭킹 | BAAI/bge-reranker-v2-m3 (Cross-Encoder, GPU) |
| 벡터 DB | ChromaDB 0.6.x (SQLite 기반, pre-Rust 안정판 고정) |
| 희소 검색 | rank-bm25 (Dense + BM25 하이브리드) |
| 지식 그래프 | NetworkX (pkl 직렬화) + FAQ 역인덱스 캐시 |
| 크롤러 | requests + BeautifulSoup + lxml, APScheduler |
| PDF | PyMuPDF + pdfplumber (디지털) · surya-ocr (선택, 스캔) |
| 첨부 파싱 | python-docx (DOCX), openpyxl (XLSX), olefile (HWP5) |
| EN/KO 매핑 | FlashText (Aho-Corasick) + `config/en_glossary.yaml` |
| 번역 (EN path) | Meta `facebook/m2m100_418M` 기본 · Ollama fallback |
| 평가 | rule-based Contains-F1 / RAGAS / LLM-as-a-Judge |
| 배포 | Docker Compose (backend + frontend + nginx), GPU 패스스루 |

---

## 파이프라인 단계별 모델 매핑

사용자 질문이 들어와서 답변이 나갈 때까지, 각 단계에서 **정확히 어떤 모델/알고리즘이 호출되는지** 전수 정리. `app/config.py` `Settings` 클래스가 SSOT이며, 아래 "기본값" 컬럼은 fallback, "현재 `.env` (2026-04-22 기준)" 컬럼은 실운영값.

| # | 단계 | 모듈 | 모델·알고리즘 | 기본값 (config fallback) | 현재 `.env` |
|---|---|---|---|---|---|
| 1 | Language Detection | `app/pipeline/language_detector.py` | 규칙 기반 (한글 문자 비율 ≥ 30% → `ko`) | — | hardcoded |
| 2 | Follow-up 감지 | `app/pipeline/follow_up_detector.py` | 규칙 기반 — 대명사·생략·부정어 감지 → `is_follow_up=True/False` | `CONV_FOLLOW_UP_MAX_WORDS=5` | 기본 |
| 3 | Query Rewriter | `app/pipeline/query_rewriter.py` | 2-stage: ① 대명사 치환 (규칙) · ② 경량 LLM 재작성 | `CONV_REWRITE_MODEL=gemma3:4b`, `CONV_REWRITE_TIMEOUT_SEC=5.0`, `CONV_REWRITE_MAX_TOKENS=80` | `gemma3:4b` · `CONV_REWRITE_BASE_URL=http://host.docker.internal:11434` |
| 4 | Query Analyzer | `app/pipeline/query_analyzer.py` | FlashText Aho-Corasick (EN→KO 용어 매핑) + 9-Intent 규칙 분류 + QuestionType (의미 유사도, BGE-M3) | — | FlashText 소스: `config/academic_terms.yaml` |
| 5a | Dense 검색 | `app/vectordb/chroma_store.py` + `app/embedding/embedder.py` | **`BAAI/bge-m3`** (1024d, multilingual) | `EMBEDDING_MODEL=BAAI/bge-m3`, `EMBEDDING_DEVICE=cpu` | **`cuda`** (GPU) |
| 5b | BM25 Sparse | `app/vectordb/bm25_index.py` + `app/pipeline/ko_tokenizer.py` | `rank_bm25.BM25Okapi` + 한국어 조사 제거 토크나이저 | 온디맨드 빌드 | 기본 |
| 6 | Graph 직접답변 | `app/graphdb/academic_graph.py` + `app/graphdb/faq_node_builder.py` | NetworkX FAQ 역인덱스. 매칭 점수 1.0 & redirect 마커 없음 → `direct_answer` (LLM 우회) | 그래프 파일: `data/graphs/academic_graph.pkl` | 기본 |
| 7 | Community Selector | `app/pipeline/community_selector.py` | Intent → 그래프 노드 타입 매핑 (하드코딩 없음, config 기반) | `config/intent_communities.json` | 기본 |
| 8 | Reranker | `app/pipeline/reranker.py` | **`BAAI/bge-reranker-v2-m3`** Cross-Encoder. Tier 가중치 + URL-seek 부스트 | `RERANKER_ENABLED=true`, `RERANKER_MODEL=...`, `RERANKER_DEVICE=cpu`, `RERANKER_TOP_K=10`, `RERANKER_CANDIDATE_K=30` | **`cuda`**, **`top_k=5`, `candidate_k=15`** (`.env` 오버라이드 — 지연 최적화, 원칙 2) |
| 9 | Context Merger | `app/pipeline/context_merger.py` | RRF (Reciprocal Rank Fusion) + intent-weighted graph/vector 병합. Evidence slicing 토글 (긴 청크에서 질문 매칭 줄만 유지) | `EVIDENCE_SLICING_ENABLED=0` (4/16 A/B에서 OFF 우수 — balanced +5.1pp) | 기본 (OFF) |
| 10 | Answer Generator | `app/pipeline/answer_generator.py` | 메인 LLM — Ollama 네이티브 `/api/chat` (think:false) 또는 OpenAI 호환 `/v1/chat/completions`. **LRU 응답 캐시** (TTL 1800s, 256 엔트리, cross-session share 지원) | `LLM_MODEL=gemma4:26b` (fallback), `LLM_API_TYPE=openai`, `LLM_TEMPERATURE=0.1`, `LLM_MAX_TOKENS=2048`, `LLM_TIMEOUT=60`, `LLM_RESPONSE_CACHE_TTL=1800`, `LLM_RESPONSE_CACHE_MAX_SIZE=256`, `LLM_RESPONSE_CACHE_ENABLED=true` | **`LLM_MODEL=qwen3.5:9b`** via tabby-api `http://192.168.0.4:11434`, **`LLM_API_TYPE=ollama`**, `max_tokens=3072`, `timeout=120` |
| 11 | Response Validator | `app/pipeline/response_validator.py` + `app/pipeline/answer_units.py` | 규칙 기반 — refusal 문구 감지 (`찾을 수 없`, `문의하시기 바랍니다` 등), 숫자 환각 가드 (`verify_answer_against_context`) | — | 기본 |
| 12 | Translator (EN path) | `app/pipeline/translator.py` | **Meta `facebook/m2m100_418M`** (MIT, 418M 파라미터) 기본. Ollama 백엔드로 전환 가능 | `TRANSLATOR_ENABLED=true`, `TRANSLATOR_BACKEND=m2m100`, `TRANSLATOR_DEVICE=cpu` | 기본 |

**주요 분기 규칙** (하드코딩 없이 코드에 반영):
- EN 쿼리에서 FlashText 매칭이 없으면 Intent=GENERAL → BGE-M3 크로스링구얼 의미검색 fallback
- Follow-up 중 분배 대명사(`각각`, `둘 다`)는 Stage 2 규칙 재작성을 건너뛰고 Stage 3 LLM으로 직행
- URL-seek 질문(`어디서`, `어느 사이트`) 감지 시 `entities["asks_url"]=true` → Reranker 가 URL 청크 부스트 (Tier1 +4%p, Tier3+ +18%p)
- Intent=SCHEDULE 은 context_merger 에서 `graph_weight=2.0` (최고) — 학사일정은 그래프가 권위
- LLM 응답 캐시는 `share_across_sessions` 플래그로 단턴 질문은 세션 간 공유. Follow-up / 개인 컨텍스트 / 학번 특수화가 있으면 세션 격리.

---

## 모델·아키텍처 변경 이력

Git log 기반 주요 마일스톤 (2026-03 이후):

| 커밋 | 날짜 | 변경 |
|---|---|---|
| `345c52a` | 3월 | 초기 — 검색 Recall@5 0.84→0.94 (6단계 최적화) |
| `a880504` | 3월 말 | **LLM 백엔드 전환**: Ollama (EXAONE) → LM Studio (Qwen 3.5 9B 4bit) |
| `d19ed8b` | 4월 초 | 관리자 동시접속 · 졸업인증 · 면책 조항 · 재학습 파이프라인 통합 |
| `90ca120` | 4월 중 | RAG Phase 1~3 최적화 + 신입생 가이드북 OCR (Surya) 인제스트 |
| `2140aa7` | 4월 15일 | **FastAPI 이식** + 어드민 페이지 + **GPU Reranker** 적용 |
| `047f15b` | 4월 15일 | EN 파이프라인 전면 개선 — `gemma4:26b` 도입, skip-translate, 그래프 EN화 |
| `96c6745` | 4월 중 | **멀티턴 대화 컨텍스트** — follow-up 감지 + 쿼리 재작성 (`gemma3:4b`) + history 주입 |
| `1b6eaa9` | 4월 14일 | 인제스트 청크 품질 개선 — `MIN_CHUNK_LEN` 50 → 150, 시간표 그룹 분할 |
| `53e89f8` | 4월 20일 | Reranker Tier 1 고정 부스트 제거 (도메스틱 +22%p 과도) |
| `cb825f4` | 4월 21일 | **LLM**: `LLM_API_TYPE=ollama` 네이티브 `/api/chat` + `think:false` 실동작 |
| `654ce54` | 4월 21일 | Docker compose 의 `LLM_BASE_URL` 하드코딩 제거 (`.env` SSOT 원칙) |
| `99a01df` | 4월 21일 | **현 베이스라인 확정**: 학사지원팀 피드백 반영 후 Contains-F1 = **83.54%** (+6.71pp) |
| (4/22 본 턴) | 4월 22일 | ChromaDB `source_file` 경로 정규화 (중복 청크 방지) + LLM 응답 캐시 세션 간 공유 (8.4× 지연 단축) + admin `/cache/stats`·`/clear` API |

**현재 메인 LLM**: `LLM_MODEL` 환경변수가 SSOT. 2026-04-22 시점 운영값은 `qwen3.5:9b` (tabby-api 원격). 코드·README에 모델명 하드코딩 없음 (원칙 4).

---

## 설치

### 요구사항

- **NVIDIA GPU** 1개 이상 (권장 8GB+ VRAM — Embedder + Reranker 동시 로드)
- Docker 24+ with NVIDIA Container Toolkit (GPU 패스스루)
- (로컬 개발 시) Python 3.12+ · Node.js 20+ · npm
- LLM 엔드포인트 한 가지:
  - **로컬 Ollama** (`http://localhost:11434`, 권장) — 메인 LLM + 쿼리 재작성(gemma3:4b) 둘 다 한 서버에서
  - **원격 Ollama / tabby-api** (예: `http://192.168.0.4:11434`) — GPU 서버 분리 운영
  - (대안) 과거 지원되던 **LM Studio** (`:1234`) 는 OpenAI 호환 API만 사용 가능. `LLM_API_TYPE=openai` 로 실행할 수 있으나, 권장 모드는 Ollama 네이티브 (`think:false` 등 명령 지원)
- Hugging Face 토큰 (BGE-M3 / BGE-Reranker 다운로드용)

### 1. `.env` 생성

프로젝트 루트에 `.env`를 두고 최소한 다음 항목을 채운다. 전체 목록은 하단 **환경 변수** 섹션 참조.

```env
# LLM (택 1 — 원격 서버 예시)
LLM_BASE_URL=http://192.168.0.4:11434
LLM_MODEL=<ollama/lmstudio 모델명>
LLM_API_TYPE=ollama          # ollama | openai
LLM_MAX_TOKENS=3072
LLM_TEMPERATURE=0.1
LLM_TIMEOUT=120

# 멀티턴 재작성 (경량 모델, 별도 URL 가능)
CONV_REWRITE_MODEL=gemma3:4b
CONV_REWRITE_BASE_URL=http://host.docker.internal:11434
CONV_REWRITE_TIMEOUT_SEC=5.0
CONV_REWRITE_MAX_TOKENS=80

# GPU 모델
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cuda
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_DEVICE=cuda
RERANKER_ENABLED=true
RERANKER_TOP_K=5
RERANKER_CANDIDATE_K=15

# ChromaDB
CHROMA_COLLECTION=bufs_academic
CHROMA_N_RESULTS=15

# Admin / HF
HF_TOKEN=<hf_...>
ADMIN_PASSWORD=<강력한 비밀번호>

# 크롤링 스케줄러
CRAWLER_ENABLED=true
CRAWLER_NOTICE_INTERVAL=30
```

### 2. 데이터 준비 (최초 1회)

크롤링 + PDF → ChromaDB + Graph 까지 한 번에 인제스트하려면:

```bash
# (권장) 호스트에서 바로 실행 — GPU 임베딩 필요
python -X utf8 scripts/ingest_all.py
```

단계별 실행도 가능:

```bash
# 1. PDF → 그래프 및 ChromaDB (학사안내/수업시간표/신입생가이드)
python scripts/ingest_pdf.py --pdf "data/pdfs/2026학년도1학기학사안내.pdf"

# 2. FAQ / 정적 페이지 크롤 + 인제스트
python scripts/ingest_faq.py
python scripts/ingest_static_page.py

# 3. 고정공지 크롤 + 그래프 연결
python scripts/ingest_pinned_notices.py

# 4. 학과별 졸업인증 업데이트
python scripts/update_dept_grad_exam.py
```

> **순서 중요**: `ingest_all.py` 내부 순서는 PDF → graph build → static pages → ChromaDB incremental → graph nodes → pinned notices → FAQ 이다. 개별 실행 시에도 이 순서를 지킨다 (공지 노드가 학사 그래프에 엣지로 연결되려면 그래프가 먼저 있어야 함).

### 3. Docker로 실행 (프로덕션)

```bash
cd docker
docker compose up -d --build
```

- `docker-compose.yml`은 **backend(8000) + frontend(3000) + nginx(80)** 3개 서비스를 띄우고 GPU를 backend에 패스스루한다.
- `../data`·`../config`·HuggingFace 캐시를 볼륨 마운트 — 재빌드 시 모델 재다운로드 없음.
- 헬스체크: `curl http://localhost:8000/api/health` → 200.

접속:
| 페이지 | URL | 설명 |
|---|---|---|
| 챗봇 (한국어) | `http://localhost/ko` | 학생용 학사 질문 |
| 챗봇 (영어) | `http://localhost/en` | EN 파이프라인 (FlashText + 번역) |
| 관리자 | `http://localhost/admin` | FAQ 큐레이션 · 졸업요건 · 크롤러 · 로그 |
| API docs | `http://localhost:8000/docs` | FastAPI Swagger |

### 4. 로컬 개발 (hot-reload)

```bash
# 백엔드만 hot-reload
cd docker
docker compose -f docker-compose.dev.yml up

# 프론트엔드는 npm이 더 빠름
cd frontend
npm install
npm run dev
```

`docker-compose.dev.yml`은 `app/`·`backend/` 를 bind-mount 하고 `uvicorn --reload`로 띄운다. 프론트엔드는 `npm run dev`가 Next.js HMR이 더 빠르다.

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 하이브리드 검색 | Dense(BGE-M3) + BM25 + Graph 병렬 → Reranker 상위 5 |
| FAQ 직접답변 | FAQ 질문 매칭률 ≥ 0.9 시 그래프 direct_answer 경로 (LLM 우회) |
| 멀티턴 컨텍스트 | Follow-up 감지 → 경량 LLM 재작성 → 토큰예산 제한 history 주입 |
| 학번/학과 인식 | 질문 내 학번·학과 자동 파싱 → 맞춤 졸업요건 응답 |
| 수업시간표 | 학과 필터 + `timetable_parser` (교시 → 시각 변환) |
| 성적표 분석 | `transcript_parser` 다중 포맷 업로드 → 부족 학점·재수강·조기졸업 자동 분석 |
| EN 파이프라인 | FlashText aliases → KO 정규화 → BGE-M3 크로스링구얼 + One-Pass 번역 |
| 관리자 FAQ 루프 | 저평점(≤2) 질문 클러스터 → 관리자 받은편지함 → 답변 작성 → 인제스트 |
| 사용자 알림 | FAQ 이송·수정 발생 시 로그인 사용자에게 알림 |
| 별점 피드백 | 1~5점 평가 + 자유 피드백, `data/feedback/` + 로그에 저장 |
| 사용자 인증 | JWT 기반 로그인 (선택, 비로그인도 기본 챗봇 사용 가능) |
| 공지 자동 크롤 | APScheduler — 30분 주기 공지 크롤 + 해시 기반 증분 인제스트 |
| 면책 조항 | 온보딩 시 AI 답변 면책 동의 체크박스 |

---

## 프로젝트 구조

```
bufs-chatbot/
├── app/
│   ├── config.py                # .env 기반 전체 설정 (SSOT)
│   ├── models.py                # Chunk / QueryAnalysis 등 Pydantic 모델
│   ├── shared_resources.py      # 싱글톤 리소스 매니저
│   ├── pipeline/
│   │   ├── query_analyzer.py    # Intent + 엔티티 (FlashText EN→KO 포함)
│   │   ├── query_rewriter.py    # 경량 LLM 재작성
│   │   ├── follow_up_detector.py
│   │   ├── query_router.py      # Vector/Graph 병렬 라우팅
│   │   ├── reranker.py          # BGE-Reranker-v2-m3
│   │   ├── community_selector.py
│   │   ├── context_merger.py    # evidence slicing 토글
│   │   ├── answer_generator.py  # OpenAI/Ollama 듀얼 백엔드 + LRU 응답 캐시
│   │   ├── response_validator.py
│   │   ├── answer_units.py      # 환각 검증 유틸 (verify_answer_against_context)
│   │   ├── glossary.py · ko_tokenizer.py
│   │   ├── language_detector.py · translator.py
│   ├── vectordb/chroma_store.py
│   ├── graphdb/                 # academic_graph · notice_graph_builder · faq_index
│   ├── pdf/                     # detector · digital_extractor · ocr_extractor · timetable_parser
│   ├── ingestion/               # chunking · incremental_update · docx/xlsx/hwp extractor
│   ├── crawler/                 # change_detector · notice_crawler · static_page_crawler
│   ├── scheduler/               # APScheduler 백그라운드 잡
│   ├── transcript/              # 성적표 분석
│   ├── contacts/                # 학과 연락처 SSOT
│   ├── logging/chat_logger.py
│   └── embedding/embedder.py
├── backend/
│   ├── main.py                  # FastAPI 팩토리 + lifespan
│   ├── dependencies.py          # DI 싱글톤 (get_analyzer · get_generator · ...)
│   ├── database.py              # SQLite (users, chat_messages, notifications, ratings)
│   ├── session.py               # 세션 저장소
│   ├── crypto.py                # JWT
│   ├── routers/
│   │   ├── chat.py              # POST / GET stream /api/chat
│   │   ├── session.py           # 세션 생성
│   │   ├── feedback.py          # 피드백 / 별점 / 저평점 캐시 무효화 훅
│   │   ├── source.py            # 출처 조회
│   │   ├── transcript.py        # 성적표 업로드/분석
│   │   ├── health.py
│   │   ├── user.py              # 로그인/알림
│   │   └── admin/               # auth · dashboard · graduation · crawler · logs · contacts · graph · faq
│   └── schemas/
├── frontend/                    # Next.js 16 App Router (ko/en i18n, /admin)
│   └── src/app/[lang]/...
├── docker/
│   ├── Dockerfile.backend · Dockerfile.frontend
│   ├── docker-compose.yml       # 프로덕션 (backend + frontend + nginx)
│   ├── docker-compose.dev.yml   # 개발 핫리로드
│   └── nginx/default.conf
├── scripts/
│   ├── ingest_all.py            # 전체 인제스트 오케스트레이션
│   ├── ingest_pdf.py · ingest_faq.py · ingest_pinned_notices.py · ingest_static_page.py
│   ├── build_graph.py · pdf_to_graph.py
│   ├── rebuild_chromadb.py · cleanup_duplicate_chunks.py
│   ├── update_dept_grad_exam.py
│   ├── eval_contains_f1.py      # 메인 회귀 평가 (rule-based Contains-F1)
│   ├── eval_f1_score.py · eval_full.py · eval_ragas.py · eval_llm_judge.py
│   └── (기타 평가·유틸)
├── data/
│   ├── pdfs/                    # 학사안내 · 시간표 · 포털 PDF
│   ├── chromadb_new/            # 운영 ChromaDB (CHROMA_PERSIST_DIR)
│   ├── graphs/academic_graph.pkl
│   ├── crawl_meta/content_hashes.json   # 크롤 변경감지 상태
│   ├── logs/                    # 대화 로그 JSONL
│   ├── feedback/                # 자유 피드백 JSONL
│   └── eval/                    # 평가 데이터셋 3종
├── tests/                       # pytest (pipeline · graph · parser · crawler)
├── reports/eval_contains_f1/    # 평가 스냅샷 (combined_<tag>_<ts>.json)
├── config/
│   ├── academic_terms.yaml      # EN→KO 용어 매핑
│   ├── en_glossary.yaml
│   └── static_pages.json        # 정적 페이지 크롤 대상
├── CLAUDE.md                    # 4대 원칙 및 프로젝트 지침
├── requirements.txt
├── .env                         # 사용자 설정 (gitignore)
└── README.md
```

---

## API

FastAPI Swagger: `http://localhost:8000/docs`

주요 엔드포인트:

| 경로 | 메서드 | 설명 |
|------|--------|------|
| `/api/health` | GET | 헬스체크 (Docker healthcheck용) |
| `/api/session` | POST | 세션 생성 (UUID 반환) |
| `/api/chat` | POST | 논스트리밍 채팅 (평가·테스트용. 쿼리 파라미터: `session_id`, `question`) |
| `/api/chat/stream` | GET | SSE 스트리밍 채팅 (프론트엔드 기본) |
| `/api/chat/history` | GET | 세션 대화 히스토리 |
| `/api/feedback` | POST | 자유 피드백 저장 |
| `/api/rating` | POST | 별점 제출 (저평점 시 자동 캐시 무효화 훅) |
| `/api/source/{id}` | GET | 출처 청크 원문 조회 |
| `/api/transcript/analyze` | POST | 성적표 업로드 + 분석 |
| `/api/user/*` | 다양 | 회원가입·로그인·알림 |
| `/api/admin/*` | 다양 | 관리자 (패스워드 인증). FAQ 큐레이션·졸업요건·크롤러 제어·로그·연락처 |

---

## 평가

### 회귀 평가 (메인): Contains-F1 on 164 questions

```bash
python -X utf8 scripts/eval_contains_f1.py \
  --datasets data/eval/balanced_test_set.jsonl \
             data/eval/rag_eval_dataset_2026_1.jsonl \
             data/eval/user_eval_dataset_50.jsonl \
  --base-url http://localhost:8000 \
  --output reports/eval_contains_f1 \
  --tag <실험태그>
```

3개 데이터셋을 Docker 백엔드(`/api/chat` 경유) 로 돌리고 rule-based Contains-F1·Recall@5·MRR@5·Answerable/Unanswerable F1을 산출한다. 결과는 `reports/eval_contains_f1/combined_<tag>_<ts>.json` 에 저장.

**기준선** (2026-04-21, commit `99a01df`, 학사지원팀 피드백 반영 후):

| 데이터셋 | Contains-F1 | Recall@5 |
|---|---|---|
| balanced_test_set (39문항) | 66.67% | 54.84% |
| rag_eval_dataset_2026_1 (50문항) | 92.00% | — |
| user_eval_dataset_50 (75문항) | 86.67% | — |
| **OVERALL (164문항)** | **83.54%** | — |

**NO-GO 기준** (`CLAUDE.md` 커밋 규칙):
- 전체 -1pp 이상 회귀 → 커밋 보류
- 단일 데이터셋 -3pp 이상 회귀 → 커밋 보류
- 거부율 -10pp 이상 폭락 → 커밋 보류

검색·생성·답변 품질에 영향을 줄 수 있는 변경(파이프라인·그래프·프롬프트·LLM 설정 등)이면 반드시 이 스크립트로 164문항 회귀 평가를 돌린 후 커밋한다. UI 텍스트/주석/로그만 건드리는 명백한 작업은 생략 가능 — 단 사유를 커밋 메시지에 한 줄 명시.

### 기타 평가 도구

```bash
# LLM-as-a-Judge (정확성 4.0~5.0 척도)
python scripts/eval_llm_judge.py --dataset data/eval/rag_eval_dataset_2026_1.jsonl

# RAGAS (Faithfulness · Answer Relevancy · Context Recall · Answer Correctness)
python scripts/eval_ragas.py --dataset data/eval/rag_eval_dataset_2026_1.jsonl

# 검색 단독 (Hit Rate · Recall@k · MRR)
python scripts/eval_full.py --dataset data/eval/rag_eval_dataset_2026_1.jsonl
```

---

## 테스트

```bash
pytest tests/ -v
```

커버 영역: query_analyzer · follow_up_detector · context_merger · response_validator · academic_graph · notice_graph_builder · PDF/transcript 추출기 · 크롤러 · 통합 파이프라인.

---

## 환경 변수 (발췌)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_BASE_URL` | `http://localhost:11434` | LLM 서버 URL |
| `LLM_MODEL` | config 기본값 | LLM 모델명 (SSOT: `.env`) |
| `LLM_API_TYPE` | `openai` | `ollama` 또는 `openai` |
| `LLM_MAX_TOKENS` · `LLM_TEMPERATURE` · `LLM_TOP_P` · `LLM_TIMEOUT` | | LLM 하이퍼파라미터 |
| `LLM_RESPONSE_CACHE_ENABLED` | `true` | LLM 응답 캐시 on/off (평가 공정성 필요 시 false) |
| `LLM_RESPONSE_CACHE_TTL` | `1800` | LLM 응답 LRU 캐시 TTL (초, refusal/short/저신뢰 답변은 1/10 = 180s) |
| `LLM_RESPONSE_CACHE_MAX_SIZE` | `256` | 최대 캐시 엔트리 수 |
| `CONV_REWRITE_MODEL` · `CONV_REWRITE_BASE_URL` · `CONV_REWRITE_TIMEOUT_SEC` | | 멀티턴 재작성 LLM |
| `CONV_HISTORY_ENABLED` · `CONV_MAX_HISTORY_TURNS` · `CONV_HISTORY_TOKEN_BUDGET` | | history 주입 토글 |
| `EMBEDDING_MODEL` · `EMBEDDING_DEVICE` | BGE-M3 / cuda | |
| `RERANKER_MODEL` · `RERANKER_DEVICE` · `RERANKER_ENABLED` · `RERANKER_TOP_K` · `RERANKER_CANDIDATE_K` | BGE-Reranker-v2-m3 / cuda / true / 5 / 15 | |
| `CHROMA_PERSIST_DIR` | `data/chromadb` (Docker: `/app/data/chromadb_new`) | |
| `CHROMA_COLLECTION` · `CHROMA_N_RESULTS` | `bufs_academic` / 15 | |
| `QUERY_ROUTER_SEQUENTIAL` | `1` (Windows 필수) | ChromaDB 병렬 쿼리 segfault 회피 |
| `EVIDENCE_SLICING_ENABLED` | `0` | context_merger evidence slicing (A/B 결과 OFF가 +5.1pp) |
| `CRAWLER_ENABLED` · `CRAWLER_NOTICE_INTERVAL` · `CRAWLER_MAX_PAGES` · `CRAWLER_TIMEOUT` | true / 30 / 5 / 30 | APScheduler 크롤러 |
| `ADMIN_PASSWORD` · `ADMIN_MAX_ATTEMPTS` · `ADMIN_LOCKOUT_MINUTES` · `ADMIN_SESSION_TIMEOUT` | `bufs_admin_2025` / 5 / 15 / 30 | 관리자 인증 |
| `ADMIN_FAQ_*` | | 관리자 FAQ 큐레이션 임계치 (클러스터링·중복판정·저평점 기준) |
| `NOTIF_*` | | 사용자 알림 설정 |
| `TR_*` | | 성적표 분석 fallback 임계치 |
| `HF_TOKEN` | — | Hugging Face 모델 다운로드 토큰 |

전체 목록은 `app/config.py` 참조.

---

## LLM 응답 캐시 (cross-session share)

반복 질문 지연을 줄이기 위한 인메모리 LRU 캐시 — 학교 FAQ 특성상 같은 질문이 여러 세션에서 반복됨을 활용.

**핵심 동작:**
- 단턴 질문 & follow-up 아님 & 개인 컨텍스트 없음 → `share_across_sessions=True` → 다른 세션 간 캐시 히트 허용 (실측 6.3s → 0.75s, **8.4× 가속**)
- Follow-up / 개인 컨텍스트 / 학번 특수화 → 기존대로 세션별 키 격리
- Refusal 답변 / 20자 미만 / `context_confidence < 0.35` → **짧은 TTL (180s)** 로 저장 — 잘못된 답변이 오래 고착되지 않도록

**관리 API** (관리자 인증 필요):
- `GET /api/admin/cache/stats` → `{enabled, size, max_entries, ttl_seconds, hits, misses, hit_rate}`
- `POST /api/admin/cache/clear?scope=all` → 전체 삭제
- `POST /api/admin/cache/clear?scope=question&question=...` → 특정 질문 문자열 포함 엔트리만 삭제

**자동 무효화:**
- 저평점(별점 ≤ 2) 수신 시 `feedback.py` 가 자동으로 해당 질문 엔트리 삭제 (`invalidate_by_question`)
- ChromaDB `context_hash` 변경 시 캐시 키가 자연스럽게 달라져 미스 발생 (재인제스트 자동 무효화)

**환경 변수:**
- `LLM_RESPONSE_CACHE_ENABLED=true|false` (바이패스 스위치 — 평가 공정성 필요 시 false)
- `LLM_RESPONSE_CACHE_TTL=1800` (초)
- `LLM_RESPONSE_CACHE_MAX_SIZE=256` (엔트리)

**회귀 평가 공정성:** `scripts/eval_contains_f1.py` 는 데이터셋당 동일 `session_id` 를 재사용하므로 캐시 히트가 결과에 섞일 수 있음. 엄밀한 비교가 필요하면 `LLM_RESPONSE_CACHE_ENABLED=false` 로 돌릴 것.

---

## 트러블슈팅

| 증상 | 원인 · 해결 |
|------|-------------|
| `ChromaDB segfault` (Windows) | `QUERY_ROUTER_SEQUENTIAL=1` 설정. `chromadb>=0.7` 의 Rust 백엔드는 Windows + Py3.12에서 불안정 → `requirements.txt` 가 `0.6.x`로 고정되어 있음. |
| LLM 빈 응답 | `LLM_API_TYPE=ollama` 네이티브 `/api/chat` 이면 `think:false` 실전달됨 (OpenAI 호환 API는 think 필드 미지원). tabby-api 원격도 Ollama 호환이므로 `ollama` 권장. |
| 프론트엔드 "Failed to fetch" | CORS — `.env` 의 `CORS_ORIGINS` 에 접속 출처 포함 확인. maruvis.co.kr 사용 시 그 도메인도 포함. |
| **nginx 502 Bad Gateway** (백엔드는 200인데) | `docker compose up -d --build backend` 로 컨테이너 재생성되면 내부 IP가 바뀌어 nginx upstream 캐시가 stale. `docker compose restart nginx` 로 해결. |
| **Cloudflare Tunnel Error 1033** (`maruvis.co.kr`) | 호스트의 `cloudflared.exe` 프로세스가 죽음. `cloudflared tunnel run <tunnel-id>` 재실행 또는 Windows 서비스로 등록. 설정: `~/.cloudflared/config.yml` 에서 ingress 확인. |
| 모델 재다운로드 반복 | Docker 재빌드 후에도 `~/.cache/huggingface` 볼륨 마운트되어 있는지 확인. `docker-compose.yml` 의 `volumes:` 섹션. |
| 크롤러 동작 안 함 | `.env` 에 `CRAWLER_ENABLED=true` + 백엔드 재시작. 스케줄러 상태: `/api/admin/crawler/status`. |
| **재인제스트 후 정답률 하락** | `source_file` 경로 정규화 누락 시 같은 PDF가 Windows 절대·Linux 절대·상대 경로로 중복 청크 생성됨 (chunking.py `_normalize_source_path` 적용 필요). 또한 `ingest_all.py` 는 pinned 공지만 처리하므로 일반 공지 HWP 첨부 복원을 위해 **추가로 `scripts/ingest_all_notices.py`** 실행 필요. |
| **성적표 업로드 무반응 (/api/transcript/upload)** | 대부분 nginx upstream stale (위 항목) 원인. `curl http://localhost:8000/api/transcript/status?session_id=XXX` 로 백엔드 직접 확인 → 200이면 nginx 재시작. |
| 세션·전송 데이터 사라짐 | `SessionStore` 는 인메모리 (24h TTL). 백엔드 재시작 시 비로그인 세션 전부 휘발. 로그인 사용자는 DB(`user_transcripts`) 복원됨. |

---

## 실패 진단 도구 (R/G/P)

164문항 eval 실패 사유를 자동 분류:

```bash
python -X utf8 scripts/diagnose_failures.py --tag <실험태그>
```

분류 규칙 (reports/diagnosis/ 출력):
- **R** (Retrieval 실패): 정답 청크가 top-5에 없음 — 검색 계층 튜닝 우선
- **G** (Generation 실패): 정답 청크는 검색됐지만 LLM이 답 실패 — 프롬프트/컨텍스트 머저 튜닝. 세분화: G1 refusal / G2 partial / G3 offtopic
- **P** (Pipeline 우회): graph direct_answer / FAQ direct_answer 로 정답. R@5 미기록 — 실패 아님

---

## 개발·기여 가이드

- **기능 추가 전** 기존 `app/pipeline/`·`app/crawler/` 함수부터 검토. 중복 구현 금지.
- **모델·임계치·경로는 `.env`/`app/config.py` 의 `Settings` 데이터클래스에 추가**. 매직 넘버 금지 (원칙 4).
- **크롤러 source_id 형식 변경**은 `data/crawl_meta/content_hashes.json` 일회성 마이그레이션 비용 발생 — 호환성 확인 필수.
- **새 평가 도입 시** 기존 리포트 키(`overall_contains_f1`, `answerable_contains_f1`, `recall_at_5` 등)와 호환되게.
- 커밋 규칙은 `CLAUDE.md` 참조 — 정답률 회귀 평가는 검색·생성에 영향 있는 변경이면 **필수**.

---

## 라이선스

`LICENSE` 파일 참조.
