# 캠챗 운영 매뉴얼 — 내부 팀용 (개발자 버전)

> **대상**: 수환(제품·인프라) · 임성원 박사(최적화·영어) · 백민서(문서)
> **목적**: 일상 운영 · 장애 대응 · 데이터 관리 · 배포 전반을 단독 수행 가능한 수준까지 문서화
> **버전**: 1.0 — 2026-04-22 (파이널 미팅 4/27 직전 초판)
> **학사지원팀용 간략 버전**: `docs/OPS_학사지원팀.md`

이 문서는 학사지원팀 매뉴얼의 상위 버전입니다. 학사지원팀이 자력으로 가능한 작업에는 **🟢**, 개발자 개입이 필요한 작업에는 **🔴** 태그를 달아, 업무 분장을 빠르게 판단할 수 있게 했습니다.

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [실행·기동 절차](#2-실행기동-절차)
3. [환경변수 전체 레퍼런스](#3-환경변수-전체-레퍼런스)
4. [일상 운영 런북](#4-일상-운영-런북)
5. [데이터 관리 (지식 추가·수정·삭제)](#5-데이터-관리-지식-추가수정삭제)
6. [장애 대응 런북](#6-장애-대응-런북)
7. [평가·정답률 관리](#7-평가정답률-관리)
8. [배포·외부 노출](#8-배포외부-노출)
9. [백업·복구](#9-백업복구)
10. [보안·개인정보](#10-보안개인정보)
11. [비상 연락망](#11-비상-연락망)
12. [부록](#12-부록)

---

## 1. 시스템 개요

**운영 형태: Docker Compose 기반 3 컨테이너** (`docker/docker-compose.yml`)

### 1.1 프로덕션 아키텍처

```
  [ 외부 사용자 · Cloudflare Tunnel ]
               │
               ▼ (HTTPS / :80)
     ┌───────────────────┐
     │  nginx  (:80)     │   docker/nginx/default.conf
     │  - /api/* → backend
     │  - /      → frontend
     │  - /_next/static/ 365d 캐시
     │  - SSE 스트리밍 설정 (chat 응답)
     └─────────┬─────────┘
               │
       ┌───────┴────────┐
       │                │
       ▼                ▼
  ┌─────────┐    ┌──────────┐
  │frontend │    │ backend  │
  │ Next.js │    │ FastAPI  │
  │  :3000  │    │  :8000   │
  │ (node20)│    │ (py3.12) │
  │         │    │ GPU·CUDA │
  │ src/app │    │ uvicorn  │
  │ /admin/*│    │ --workers 1
  │ /chat   │    │          │
  └─────────┘    └────┬─────┘
                      │
            ┌─────────┼──────────────────┐
            │         │                  │
            ▼         ▼                  ▼
      ChromaDB    Graph(pkl)       Ollama (외부)
   (/app/data/   (/app/data/   ┌──────────────────┐
    chromadb_new) graphs/)     │ 메인: 192.168.0.4│
                               │ :11434 (qwen3.5) │
                               │ rewrite:         │
                               │ host.docker      │
                               │   .internal      │
                               │ :11434 (gemma3)  │
                               └──────────────────┘

  [ 호스트 볼륨 마운트 ]
    ../data    → /app/data    (ChromaDB·graph·logs·FAQ·PDF 전부)
    ../config  → /app/config  (academic_terms.yaml·en_glossary.yaml)
    ~/.cache/huggingface → /root/.cache/huggingface  (모델 캐시 공유)
    ~/.cache/chroma      → /root/.cache/chroma
```

**파이프라인 공통 흐름** (nginx → backend 경유, 브라우저·외부 API 동일 싱글톤 사용)

```
사용자 질문
    │
    ▼
LanguageDetector        언어 감지 (ko / en), <1ms 휴리스틱
    │
    ▼
QueryAnalyzer           의도(Intent) + 엔티티 + 학번/학과/학기 파싱
    │  [EN] FlashText(aliases_en→ko) + en_glossary
    │        미검출 시 GENERAL + BGE-M3 크로스링구얼 fallback
    │
    ├──▶ ChromaDB (Vector)  ──┐
    │    BGE-M3 임베딩        │ ← 병렬 실행
    │    department 필터       │
    │                         │
    ├──▶ AcademicGraph        │
    │    FAQ 역인덱스 캐시    │
    │    direct_answer / 고정공지 경로
    │                         │
    └──▶ CommunitySelector ◀──┘   동적 커뮤니티 선택으로 후보 축소
                │
                ▼
           Reranker             BGE-Reranker-v2-m3 (Top K)
                │
                ▼
         ContextMerger          Vector + Graph 병합, 토큰 예산 관리
                │
                ▼
        AnswerGenerator         Ollama LLM (환경변수 LLM_MODEL)
                │              [EN] One-Pass 스트리밍: KO 초안 → 목표 언어 번역
                ▼
      ResponseValidator         답변 품질 검증
                │
                ▼
          ChatLogger            data/logs/chat_YYYY-MM-DD.jsonl
```

### 1.2 포트·컨테이너 맵

| 컨테이너 | 공개 포트 | 이미지 / 빌드 | 역할 |
|---|---|---|---|
| `nginx` | **`80`** | `nginx:alpine` (`docker/nginx/default.conf`) | 리버스 프록시. `/api/*` → backend, 나머지 → frontend, `/_next/static/` 365일 캐시. SSE 스트리밍(5분 타임아웃) |
| `frontend` | `3000` (내부) | `docker/Dockerfile.frontend` (node:20-alpine, Next.js standalone build) | 주 UI. `src/app/admin/*` 관리자 페이지 + 사용자 채팅. 클라이언트는 **상대경로로 API 호출** → nginx가 `/api/*`를 backend로 프록시 |
| `backend` | `8000` (외부 노출) | `docker/Dockerfile.backend` (python:3.12-slim + CUDA torch) | FastAPI. `/api/health`·`/api/chat`·`/api/admin/*`·`/api/session`·`/api/feedback`·`/api/source`·`/api/transcript`·`/api/user`. **APScheduler 크롤러 잡이 이 컨테이너 lifespan에서 기동** (`backend/dependencies.py:init_all()`) — **이 컨테이너가 꺼지면 크롤러도 멈춤** |
| Ollama (외부·LAN) | `11434` | — | `LLM_BASE_URL=http://192.168.0.4:11434`. 현재 모델 `qwen3.5:9b` |
| Ollama (호스트·경량) | `11434` | — | `CONV_REWRITE_BASE_URL=http://host.docker.internal:11434`. 모델 `gemma3:4b`. follow-up 쿼리 재작성용. `host.docker.internal`은 **Docker 컨테이너에서 호스트 머신**을 가리키는 정식 DNS — **정상 설정** |

**프로세스 의존성 요약 (Docker 환경 기준)**

- **사용자 채팅 (웹)**: nginx + frontend + backend 3개 모두 필수
- **관리자 UI (`/admin/*`)**: nginx + frontend + backend 3개 모두 필수 (프론트는 Next.js 페이지, API는 FastAPI `/api/admin/*`)
- **자동 크롤러**: backend 컨테이너에서 기동 — backend 재시작/중단 시 크롤러도 함께 재시작/중단됨
- **외부 API 호출자**: backend 컨테이너의 `/api/*` 엔드포인트 사용 (nginx 경유 또는 backend:8000 직접)

**레거시(운영 외)**
- `main.py` + `app/ui/chat_app.py` + `pages/admin.py` (Streamlit) — **Docker 이미지에 포함되지 않음**. 개발 중 호스트에서 `.venv` 활성 후 별도 실행 가능하지만 프로덕션 UI 아님. 호스트에서 돌려도 **같은 `data/` 볼륨을 건드려 충돌 위험** — 병행 지양.

### 1.3 디렉터리 SSOT

| 경로 | 역할 | 컨테이너 매핑 |
|---|---|---|
| `docker/docker-compose.yml` | 프로덕션 compose 파일 | — |
| `docker/docker-compose.dev.yml` | 개발 compose (핫 리로드) | — |
| `docker/Dockerfile.backend` | backend 이미지 빌드 | — |
| `docker/Dockerfile.frontend` | frontend 이미지 빌드 | — |
| `docker/nginx/default.conf` | nginx 라우팅 설정 | nginx 컨테이너가 읽음 |
| `app/pipeline/` | 검색·답변 파이프라인 (query_analyzer, query_router, reranker, context_merger, answer_generator, response_validator, glossary) | backend 이미지에 복사 |
| `app/crawler/` | gnuboard5 공지 크롤러 + 변경감지 | backend 이미지 |
| `app/graphdb/` | NetworkX 그래프 + FAQ 역인덱스 | backend 이미지 |
| `app/vectordb/` | ChromaDB 래퍼 | backend 이미지 |
| `app/ingestion/` | PDF/공지 청킹·임베딩 | backend 이미지 |
| `app/scheduler/crawl_scheduler.py` | APScheduler 잡 | backend lifespan에서 기동 |
| `app/pdf/` | PDF 파서 (detector, digital_extractor, ocr_extractor, timetable_parser) | backend 이미지 |
| `backend/` | FastAPI 앱 + 라우터 | backend 이미지 |
| `backend/routers/admin/` | 관리자 API (auth, faq, crawler, logs, graph, dashboard, graduation, contacts) prefix `/api/admin/*` | backend 이미지 |
| `frontend/src/app/` | Next.js App Router | frontend 이미지 |
| `frontend/src/app/admin/` | 관리자 UI 페이지 (faq·crawler·dashboard·graduation·graph·logs·contacts·schedule·early-grad) | frontend 이미지 |
| `frontend/src/components/`, `hooks/`, `lib/` | React 컴포넌트·훅·라이브러리 | frontend 이미지 |
| `config/` | `academic_terms.yaml`, `en_glossary.yaml` | **호스트 볼륨** `/app/config` |
| `scripts/` | 인제스트·평가·유지보수 (마스터 진입점: `ingest_all.py`) | backend 이미지에 복사 |
| `data/chromadb_new/` | **운영 활성 벡터 DB** (compose가 `CHROMA_PERSIST_DIR=/app/data/chromadb_new`로 override) | 호스트 볼륨 `/app/data` |
| `data/chromadb/` | 이전 또는 실험용 DB — **현 운영은 이 경로를 쓰지 않음** | 호스트 볼륨 |
| `data/graphs/academic_graph.pkl` | NetworkX 그래프 | 호스트 볼륨 |
| `data/logs/chat_YYYY-MM-DD.jsonl` | 대화 로그 (일일 파일) | 호스트 볼륨 |
| `data/logs/admin_audit.log` | 관리자 감사 로그 | 호스트 볼륨 |
| `data/crawl_meta/content_hashes.json` | 크롤 변경감지 상태 | 호스트 볼륨 |
| `data/crawl_meta/crawl_history.jsonl` | 크롤 이력 | 호스트 볼륨 |
| `data/crawl_meta/pdf_versions.json` | PDF 버전 추적 | 호스트 볼륨 |
| `data/crawl_meta/blacklist.json` | 크롤 제외 패턴 | 호스트 볼륨 |
| `data/faq_academic.json` | 정식 FAQ (큐레이션 완료) | 호스트 볼륨 |
| `data/faq_admin.json` | 관리자 UI에서 추가한 FAQ | 호스트 볼륨 |
| `data/faq_combined.json` | 머지 인덱스 (자동 생성) | 호스트 볼륨 |
| `data/contacts/departments.json` | 학과 연락처 SSOT | 호스트 볼륨 |
| `data/pdfs/` | 원본 PDF | 호스트 볼륨 |
| `data/extracted/`, `data/attachments/` | PDF 추출·첨부 결과 | 호스트 볼륨 |
| `data/eval/`, `reports/eval_contains_f1/` | 평가셋·결과 | 호스트 볼륨 (reports는 backend 이미지 밖 — `.dockerignore`에서 제외) |
| `tests/` | 단위/통합 테스트 | — |
| `docs/archive/` | 시점성 스냅샷 | — |
| `main.py`, `app/ui/`, `pages/` | **레거시 Streamlit** — Docker 이미지 미포함 | — |

### 1.4 검색 우선순위 (CLAUDE.md)

| 순위 | 소스 | `doc_type` |
|---|---|---|
| 1 | 공식 PDF / 학생포털 | `domestic`, `guide` |
| 2 | 그래프 직접답변 / FAQ / 고정공지 | graph direct, `faq`, `notice`(📌) — RRF 동등 경쟁 |
| 3 | 일반 공지 / 장학 | `notice`(일반), `scholarship` |

---

## 2. 실행·기동 절차

### 2.1 사전 조건

- **Docker Engine** 및 **docker compose v2** (Linux: `docker compose` 플러그인, Windows/macOS: Docker Desktop)
- **NVIDIA Container Toolkit** (Reranker GPU 가속용 — `deploy.resources.reservations.devices`)
- **원격 Ollama 머신** (`192.168.0.4:11434`) 가동 중, 모델 `qwen3.5:9b` pull 완료
- **호스트 Ollama** (옵션, rewrite 모델) — `host.docker.internal:11434`로 노출, 모델 `gemma3:4b` pull 완료
- 프로젝트 루트의 **`.env` 파일** (compose가 `env_file: ../.env`로 로드, §3)
- 최초 빌드·모델 다운로드 소요: 10~15분

### 2.2 기동 순서

**첫 기동 (빌드 포함)**

```bash
# 1) 원격 Ollama 상태 확인 (호스트에서)
curl http://192.168.0.4:11434/api/tags
#    200 + "qwen3.5:9b" 포함 필요

curl http://localhost:11434/api/tags   # 호스트 Ollama (rewrite용)
#    200 + "gemma3:4b" 포함 필요

# 2) compose 디렉터리로 이동 후 빌드·기동 (백그라운드)
cd docker
docker compose up -d --build
#    backend·frontend 이미지 빌드 후 3개 컨테이너(backend/frontend/nginx) 기동
#    backend lifespan에서 파이프라인 싱글톤 초기화 + APScheduler 크롤 잡 시작

# 3) 컨테이너 상태 확인
docker compose ps
#    STATE: running, HEALTH: healthy(2분 내) 기대

# 4) 헬스체크 (nginx 경유 또는 backend 직접)
curl http://localhost/api/health            # nginx 경유 (권장)
curl http://localhost:8000/api/health       # backend 직접

#    200 + {"status":"ok","version":"0.3.0","pipeline_ready":true}
#    pipeline_ready=false 이면 1~2분 더 대기 (최초 구동 시 모델 로드에 시간 소요)
#    Dockerfile HEALTHCHECK: start_period=120s, interval=30s

# 5) 스모크 테스트
#    브라우저 → http://localhost/         → Next.js 챗봇 홈 렌더 확인
#    브라우저 → http://localhost/admin    → 로그인 화면 → 관리자 UI 확인
#    채팅창에 질문 1개 전송 → 응답 수신 (SSE 스트리밍)
```

**재기동 (이미지 변경 없을 때)**

```bash
cd docker
docker compose up -d       # 기존 이미지 유지, 멈춘 컨테이너 재기동
# 또는 개별 재시작
docker compose restart backend    # backend만 (스케줄러 재기동됨)
docker compose restart frontend   # Next.js만
docker compose restart nginx      # 라우팅만
```

**빌드 강제 (코드 변경 반영)**

```bash
cd docker
docker compose up -d --build --force-recreate
# backend 코드(app/, backend/, scripts/) 또는 frontend 코드(frontend/) 수정 후 필수
```

### 2.3 헬스체크·핵심 의존성 체크

| 확인 대상 | 방법 | 실패 시 |
|---|---|---|
| 전체 상태 | `docker compose ps` (`healthy` 확인) | `docker logs <container>`로 원인 확인 |
| backend 파이프라인 | `curl http://localhost/api/health` → `pipeline_ready:true` | `docker logs backend --tail 200` — 모델 로드·포트·GPU 문제 확인 |
| 크롤러 가동 | `cat data/crawl_meta/crawl_history.jsonl | tail -5` 시각이 30분 이내 | backend 재시작 또는 수동 크롤(§4.1) |
| 관리자 UI | `/admin` 접근 + FAQ 목록 렌더 | nginx 라우팅·backend `/api/admin/*` 응답 확인 |
| 사용자 채팅 | `/` 접근 후 질문 전송, SSE 응답 수신 | nginx 타임아웃·backend 로그 확인 |
| 원격 LLM | `docker exec backend curl http://192.168.0.4:11434/api/tags` | 컨테이너→호스트 네트워크·Ollama 머신 상태 확인 |

### 2.4 종료 순서

```bash
cd docker
docker compose down           # 컨테이너·네트워크 제거 (볼륨은 유지)
# 데이터 보존이 중요하므로 --volumes 플래그는 절대 사용 금지
```

원격 Ollama는 건드리지 않음 (다른 프로젝트와 공유 가능성).

### 2.5 개발 모드 (핫 리로드)

`docker-compose.dev.yml`은 `--reload` + 소스 마운트로 즉시 반영:

```bash
cd docker
docker compose -f docker-compose.dev.yml up
#    backend만 올라옴 (frontend는 호스트에서 npm run dev 권장 — 빠른 HMR)
#    환경변수 OLLAMA_BASE_URL=host.docker.internal:11434 자동 설정
```

프론트엔드는 호스트에서 별도 실행:
```bash
cd frontend
npm ci              # 최초
npm run dev         # http://localhost:3000 — 핫 리로드
```

### 2.6 로그 실시간 보기

```bash
docker compose logs -f backend           # backend 스트리밍
docker compose logs -f --tail 200 nginx  # nginx 최근 200줄 + 신규
docker compose logs -f                   # 전체 컨테이너
```

---

## 3. 환경변수 전체 레퍼런스

**소스 오브 트루스 3단계 (우선순위 높음 → 낮음)**:

1. `docker/docker-compose.yml`의 `environment:` 블록 — **프로덕션에서 강제되는 override 값**
2. 프로젝트 루트 `.env` — `env_file: ../.env`로 compose가 로드
3. `app/config.py`의 `dataclass` 기본값 — 위 둘 다 없을 때 폴백

**중요 Docker override (compose에서 고정)**

| 변수 | compose 고정값 | 의미 |
|---|---|---|
| `CHROMA_PERSIST_DIR` | `/app/data/chromadb_new` | 컨테이너 내부 경로 — 호스트의 `data/chromadb_new/`에 영속 |
| `QUERY_ROUTER_SEQUENTIAL` | `1` | ChromaDB 병렬 쿼리 segfault 방지 |
| `CORS_ORIGINS` | `http://localhost,...,https://maruvis.co.kr` | frontend·외부 도메인 허용 |
| `EVIDENCE_SLICING_ENABLED` | `${... :-0}` (기본 OFF) | A/B 결과 기준 기본 OFF |

시크릿은 이 문서에 원문 저장 금지(§10.1).

### 3.1 시크릿 (절대 커밋 금지)

| 변수 | 의미 | 변경 영향 |
|---|---|---|
| `HF_TOKEN` | Hugging Face 토큰 (BGE-M3·Reranker 최초 다운로드) | 캐시 생성 후엔 무관 |
| `ADMIN_PASSWORD` | 관리자 페이지 비밀번호 | 변경 시 기존 세션 무효. 프로덕션은 강력 암호 필수 |
| `GOOGLE_API_KEY` | 사용처 추적 TODO (§12.2) | — |

### 3.2 LLM (`app/config.py` `LLMConfig`)

| 변수 | 기본값 | 현재 (.env) | 설명 |
|---|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434` | `http://192.168.0.4:11434` | Ollama 엔드포인트 |
| `LLM_MODEL` | `gemma4:26b` | `qwen3.5:9b` | 답변 생성 모델 |
| `LLM_API_TYPE` | `openai` | `ollama` | `ollama`=네이티브 `/api/chat` (`think:false` 실제 동작). `openai`=`/v1/chat/completions` |
| `LLM_MAX_TOKENS` | `2048` | `3072` | 최대 생성 토큰 |
| `LLM_TEMPERATURE` | `0.1` | `0.1` | 생성 온도 |
| `LLM_TOP_P` | `0.9` | `0.9` | top-p |
| `LLM_REPEAT_PENALTY` | `1.0` | `1.0` | 반복 페널티 |
| `LLM_TIMEOUT` | `60` | `120` | 요청 타임아웃 (초) |
| `LLM_RESPONSE_CACHE_ENABLED` | `true` | — | 응답 캐시 전체 on/off (평가 공정성 필요 시 `false`) |
| `LLM_RESPONSE_CACHE_TTL` | `1800` | — | 응답 캐시 TTL (초). refusal/short/저신뢰 답변은 180s 단축 적용 |
| `LLM_RESPONSE_CACHE_MAX_SIZE` | `256` | — | 캐시 최대 엔트리 수 |

OLLAMA_* 네임스페이스로도 폴백 설정 가능. `_env_llm()` 헬퍼 참조.

### 3.3 임베딩·리랭킹

| 변수 | 기본값 (config) | 현재 (.env) | 비고 |
|---|---|---|---|
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 동일 | **변경 시 전체 재인제스트 필요** |
| `EMBEDDING_DEVICE` | `cpu` | `cuda` | GPU 사용 중 |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | 동일 | |
| `RERANKER_DEVICE` | `cpu` | `cuda` | |
| `RERANKER_ENABLED` | `true` | `true` | |
| `RERANKER_TOP_K` | `10` | `5` | 리랭킹 후 선택 수 |
| `RERANKER_CANDIDATE_K` | `30` | `15` | 리랭킹 전 후보 수. S2 실험(=50)에서 지연 2~16초 발생해 15로 복원 |

### 3.4 ChromaDB

| 변수 | config 기본값 | 실제 운영 값 | 비고 |
|---|---|---|---|
| `CHROMA_PERSIST_DIR` | `data/chromadb` | **`/app/data/chromadb_new`** (compose override) | **호스트 실제 경로는 `data/chromadb_new/`**. 변경 시 DB 이관 필요 |
| `CHROMA_COLLECTION` | `bufs_academic` | 동일 | 컬렉션 이름 |
| `CHROMA_N_RESULTS` | `15` | 동일 | 1차 검색 결과 수 |
| (설정) `distance_metric` | `cosine` | — | config 내부 하드코딩 |

### 3.5 앱 서버·CORS

| 변수 | 기본값 | 현재 | 비고 |
|---|---|---|---|
| `APP_HOST` | `0.0.0.0` | 동일 | 컨테이너 내부 바인딩. 호스트에는 compose `ports:` 매핑으로 노출 |
| `APP_PORT` | `8000` | 동일 | FastAPI 포트. nginx `/api/*`가 이 포트로 프록시 |
| `APP_DEBUG` | `false` | `false` | 프로덕션은 반드시 false |
| `LOG_LEVEL` | `INFO` | 동일 | |
| `CORS_ORIGINS` | compose에서만 주입 | `http://localhost,http://localhost:80,http://localhost:3000,https://maruvis.co.kr` | 허용 출처. 외부 도메인 추가 시 compose 수정 후 `docker compose up -d --force-recreate backend` |

### 3.6 크롤러

| 변수 | 기본값 | 현재 (.env) | 설명 |
|---|---|---|---|
| `CRAWLER_ENABLED` | `false` | **`true`** | APScheduler 잡 활성 |
| `CRAWLER_NOTICE_INTERVAL` | `30` (분) | 동일 | 공지 크롤 주기 |
| `CRAWLER_GUIDE_HOUR` | `2` (시) | 기본 | 학사안내 PDF 체크 시각 |
| `CRAWLER_TIMETABLE_HOUR` | `3` (시) | 기본 | 수업시간표 PDF 체크 시각 |
| `CRAWLER_MAX_PAGES` | `5` | 동일 | 게시판 최대 순회 페이지 |
| `CRAWLER_TIMEOUT` | `30` (초) | 동일 | HTTP 요청 타임아웃 |
| `CRAWLER_USER_AGENT` | `BUFS-CamChat-Bot/1.0` | 기본 | |

### 3.7 관리자

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ADMIN_MAX_ATTEMPTS` | `5` | 연속 로그인 실패 허용 |
| `ADMIN_LOCKOUT_MINUTES` | `15` | 잠금 유지 시간 |
| `ADMIN_SESSION_TIMEOUT` | `30` (분) | 비활동 후 자동 로그아웃 |

### 3.8 관리자 FAQ 큐레이션

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ADMIN_FAQ_PATH` | `data/faq_admin.json` | 관리자 UI 추가 FAQ |
| `ACADEMIC_FAQ_PATH` | `data/faq_academic.json` | 정식 FAQ |
| `ADMIN_FAQ_REFUSAL_KO` | `"관련 정보를 찾을 수 없습니다"` | 미답변 시그널. **변경 시 탐지 로직 영향** |
| `ADMIN_FAQ_REFUSAL_EN` | `"couldn't find relevant information"` | 영어 미답변 시그널 |
| `ADMIN_FAQ_RATING_THRESHOLD` | `2` | 이하 별점을 미답변 후보로 |
| `ADMIN_FAQ_CLUSTER_SIM` | `0.6` | 미답변 자동 그룹핑 자카드 임계치 |
| `ADMIN_FAQ_DEDUP_SIM` | `0.75` | 기존 FAQ 중복 판정 stem coverage |
| `ADMIN_FAQ_SCAN_DAYS` | `7` | 기본 스캔 일수 |
| `ADMIN_FAQ_MAX_RETURN` | `100` | 반환 상한 |
| `ADMIN_FAQ_INCLUDE_SOURCE_Q` | `true` | 원 질의를 검색면에 포함 |
| `ADMIN_FAQ_STYPE_FILTER` | `true` | student_type 필터 전역 스위치 |
| `CURRENT_ACADEMIC_YEAR` | `2026` | 학년 자동계산 기준 |

### 3.9 알림 (`NotificationConfig`)

| 변수 | 기본값 | 용도 |
|---|---|---|
| `NOTIF_LIST_LIMIT` | `50` | 목록 최대 건수 |
| `NOTIF_RETENTION_DAYS` | `30` | 보관 일수 |
| `NOTIF_BODY_MAX_CHARS` | `200` | 본문 최대 글자 |
| `NOTIF_TITLE_ANSWERED_KO` | `"학사지원팀이 답변을 정정했습니다"` | 답변 정정 알림 제목(ko) |
| `NOTIF_TITLE_ANSWERED_EN` | `"Your question has been answered by the Academic Team"` | 동일(en) |
| `NOTIF_TITLE_UPDATED_KO` | `"답변이 업데이트되었습니다"` | 업데이트 알림(ko) |
| `NOTIF_TITLE_UPDATED_EN` | `"Answer has been updated"` | 업데이트 알림(en) |

### 3.10 대화 컨텍스트 (Multi-turn)

| 변수 | 기본값 | 현재 (.env) | 비고 |
|---|---|---|---|
| `CONV_HISTORY_ENABLED` | `true` | 동일 | history 주입 활성 |
| `CONV_MAX_HISTORY_TURNS` | `2` | 동일 | 최대 주입 턴 수 |
| `CONV_HISTORY_TOKEN_BUDGET` | `500` | 동일 | 토큰 예산 |
| `CONV_REWRITE_ENABLED` | `true` | 동일 | follow-up 조건부 재작성 |
| `CONV_REWRITE_MODEL` | `gemma3:4b` | 동일 | 경량 모델 |
| `CONV_REWRITE_BASE_URL` | `""` (메인 LLM 폴백) | `http://host.docker.internal:11434` | **Docker 환경 정상 설정**. 컨테이너에서 호스트 머신의 Ollama(rewrite용 `gemma3:4b`)로 라우팅. 호스트에 Ollama가 없으면 타임아웃 후 메인 LLM 폴백 — 이 경우 빈 값으로 두어 불필요한 5초 대기 제거 권장 |
| `CONV_REWRITE_TIMEOUT_SEC` | `0.8` | `5.0` | 타임아웃(초) |
| `CONV_REWRITE_MAX_TOKENS` | `80` | 동일 | |
| `CONV_FOLLOW_UP_MAX_WORDS` | `5` | 동일 | follow-up 감지 단어 수 |

### 3.11 파이프라인 플래그 (**A/B 확정, 변경 주의**)

| 변수 | 현재 | 비고 |
|---|---|---|
| `EVIDENCE_SLICING_ENABLED` | `0` (OFF) | 2026-04-16 A/B: OFF가 Contains-F1 최선 (+5.1pp, 퇴행 0). **그냥 켜지 말 것 — `eval_contains_f1.py` 회귀 필수** |
| `EVIDENCE_SLICING_MIN_TEXT_LEN` | `1400` | slicing 조건 임계치 |
| `EVIDENCE_SLICING_MIN_SLICED_LEN` | `500` | |
| `EVIDENCE_SLICING_CONTEXT_LINES` | `2` | |
| `QUERY_ROUTER_SEQUENTIAL` | `1` (.env) | ChromaDB 초기 빌드 시 병렬 쿼리 segfault 방지. **프로덕션에서 유지** |
| `SINGLE_TURN_REWRITE_ENABLED` | `false` | 실험용 (recall@5 개선 연구) |

### 3.12 Transcript·리포트 Fallback (`TranscriptRulesConfig`)

graph 동적 조회 실패 시 쓰이는 안전망. 실제 값은 graph 재인제스트로 반영되는 것이 원칙.

| 변수 | 기본값 | 용도 |
|---|---|---|
| `TR_SHORTAGE_WARN_MIN` | `0.5` | 부족 학점 경고 분기 |
| `TR_SHORTAGE_ERROR_MIN` | `10` | 부족 학점 오류 분기 |
| `TR_RETAKE_GRADE` | `B0` | 재수강 후보 기준 |
| `TR_EARLY_GRAD_GPA` | `3.7` | 조기졸업 GPA |
| `TR_GRAD_CREDITS_FALLBACK` | `130` | 졸업 총 학점 |
| `TR_REG_MAX_FALLBACK` | `18` | 한 학기 수강 최대 |
| `TR_REG_MAX_EXTENDED` | `24` | 우수 평점 시 확장 최대 |
| `TR_EXCELLENT_GPA` | `4.0` | 우수 평점 기준 |
| `TR_NORMAL_SEMESTERS` | `8` | 정규 졸업 학기 수 |
| `TR_EARLY_GRAD_MIN_SEMS` | `6` | 조기졸업 최소 학기 |

### 3.13 PDF·OCR

| 변수 | 기본값 | |
|---|---|---|
| `OCR_BATCH_SIZE` | `4` | |
| `OCR_DPI` | `200` | |
| `ocr_languages` | `["ko","en"]` (config 하드) | Tesseract 언어팩 의존 |

### 3.14 변경 시 필수 조치 요약

| 변경한 변수 | 필요 조치 |
|---|---|
| `EMBEDDING_MODEL` | 전체 재인제스트 (`docker exec backend python scripts/ingest_all.py`) |
| `CHROMA_COLLECTION` / `CHROMA_PERSIST_DIR` | 기존 DB 이관 또는 재빌드. 경로 변경 시 compose `volumes` 또는 `environment` 양쪽 정합성 확인 |
| `LLM_MODEL` | Ollama에 모델 pull 선행 + 회귀 평가 |
| `LLM_API_TYPE` | `ollama` ↔ `openai` 전환 시 스모크 테스트 |
| `ADMIN_PASSWORD` | `.env` 수정 후 `docker compose up -d --force-recreate backend` (세션 무효화) |
| `ADMIN_FAQ_REFUSAL_KO/EN` | 미답변 탐지 규칙 동반 검토 |
| `EVIDENCE_SLICING_ENABLED` | 회귀 평가 필수 |
| `CORS_ORIGINS` | compose 수정 → `--force-recreate backend` |
| compose override 값 | `docker compose up -d --build` (빌드 캐시 유효 시 빠름) |
| 기타 임계치(`*_THRESHOLD`, `*_SIM`) | 회귀 평가 권장 |

> **.env만 수정하는 경우**: `docker compose restart backend`로는 env_file이 다시 로드되지 않을 수 있음 — `docker compose up -d --force-recreate backend`가 확실.

---

## 4. 일상 운영 런북

### 4.1 크롤러

**자동**: `CRAWLER_ENABLED=true` + APScheduler 잡 (`app/scheduler/crawl_scheduler.py`) — backend 컨테이너 lifespan에서 기동

| 잡 | 주기 | 대상 |
|---|---|---|
| 공지 크롤 | `CRAWLER_NOTICE_INTERVAL=30`분 | gnuboard5 공지사항 (학사·장학 게시판) |
| 학사안내 PDF | 매일 `CRAWLER_GUIDE_HOUR=02`시 | 학생포털 PDF |
| 수업시간표 PDF | 매일 `CRAWLER_TIMETABLE_HOUR=03`시 | 학생포털 PDF |

**수동** (🟢 학사지원팀 자력): `/admin/crawler` 페이지 → "지금 실행" 버튼 (Next.js가 `POST /api/admin/crawler/trigger` 호출)

**크롤러 API 엔드포인트** (디버깅·자동화용)

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/admin/crawler` | 크롤러 상태·최근 실행 정보 조회 |
| POST | `/api/admin/crawler/trigger` | 수동 크롤 즉시 실행 |
| POST | `/api/admin/crawler/reset-hashes` | 변경감지 해시 전체 초기화 (다음 크롤 시 전체 재수집) |
| POST | `/api/admin/crawler/reingest` | 수집된 원본으로 벡터DB·그래프 재인제스트 |
| GET | `/api/admin/crawler/history` | 크롤 이력 반환 (페이지네이션 지원 가능성) |

인증은 관리자 세션 기반. curl 테스트 시 `/api/admin/auth/login`로 먼저 쿠키/토큰 받아서 동반 전송.

**상태 확인 (호스트에서 볼륨 직접 접근)**

```bash
# 최근 크롤 이력 tail (JSONL) — 호스트 볼륨이라 backend 컨테이너 없이도 접근 가능
tail -n 20 data/crawl_meta/crawl_history.jsonl

# 변경감지 상태 (source_id → content hash)
head data/crawl_meta/content_hashes.json
```

**상태 확인 (컨테이너 내부)**

```bash
# backend 로그에서 APScheduler 동작 확인
docker compose logs backend --tail 300 | grep -iE "scheduler|crawl"

# 컨테이너 내부에서 직접 조회
docker exec backend tail -n 20 /app/data/crawl_meta/crawl_history.jsonl
```

**문제 신호**

- `crawl_history.jsonl`의 최신 엔트리가 30분 이상 갱신 없음 → 스케줄러 사망 또는 backend 재시작 누락 (§6.4)
- 404/403 급증 → gnuboard 구조 변경 가능 → `docker compose logs backend --tail 500 | grep -iE "error|404|403"` 로 구체 에러 확인

### 4.2 인제스트 파이프라인

**실행 원칙**: 모든 스크립트는 **backend 컨테이너 내부에서 실행** (파이썬 환경·모델 캐시·데이터 볼륨이 컨테이너에 있음). 호스트 `.venv`에서 실행 금지.

**마스터 진입점** (🔴):

```bash
docker exec -it backend python scripts/ingest_all.py
# 내부 순서: PDF 그래프 빌드 → PDF 벡터DB → 정적 페이지 크롤링 → 고정공지
```

**개별 스크립트** (모두 `docker exec backend` prefix 필요)

| 스크립트 | 용도 | 자력 |
|---|---|---|
| `python scripts/ingest_pdf.py --pdf <file> [--student-id 2023(기본)] [--doc-type domestic|foreign|transfer|schedule|timetable]` | 단일 PDF 인제스트 | 🔴 |
| `python scripts/ingest_pdf.py --status` | DB 현황 확인 | 🔴 (명령 실행 권한 필요) |
| `python scripts/ingest_pdf.py --pdf ... --save-json` | 디버깅: 추출 결과 JSON | 🔴 |
| `python scripts/ingest_pinned_notices.py` | 고정공지 → 벡터DB+그래프 | 🔴 |
| `python scripts/ingest_faq.py` | FAQ → 벡터DB + 역인덱스 | 🔴 (UI 사용 권장) |
| `python scripts/ingest_static_page.py` | 학생포털 정적 페이지 | 🔴 |
| `python scripts/ingest_all_notices.py` (**2026-04-22 추가**) | pinned + 일반 공지 전수 크롤 · 첨부(HWP/PDF/XLSX) 복원 | 🔴 |
| `python scripts/build_graph.py` | PDF → 지식 그래프 재빌드 | 🔴 |
| `python scripts/pdf_to_graph.py` | PDF → 그래프 데이터 자동 파싱 | 🔴 |
| `python scripts/update_dept_grad_exam.py` | 학과별 졸업인증 업데이트 | 🔴 (UI 일부 가능) |
| `python scripts/rebuild_chromadb.py` | ChromaDB 전체 재빌드 | 🔴 |
| `python scripts/cleanup_duplicate_chunks.py` | 중복 청크 정리 | 🔴 |
| `python scripts/diagnose_failures.py --tag <eval_tag>` (**2026-04-22 추가**) | 164문항 eval 실패 자동 분류 (R/G/P) | 🔴 |

**경로 정규화 (2026-04-22 개선, `app/ingestion/chunking.py::_normalize_source_path`)**

동일 PDF 파일이 Windows 절대 경로 / Docker 절대 경로 / 상대 경로로 여러 번 인제스트될 때 중복 청크가 생성되던 버그 수정. 모든 `source_file` 이 프로젝트 루트 기준 상대 경로 (`data/pdfs/...`, `data/attachments/hwp/...`) 로 정규화됨. 기존 DB 에 중복이 있으면 `scripts/cleanup_duplicate_chunks.py` 또는 clean rebuild 권장.

**컨테이너 내부 경로**: 스크립트가 참조하는 데이터 경로는 **컨테이너 내 `/app/data/...`** (호스트의 `./data/...`와 볼륨 마운트로 동일 파일).

**실행 예시**

```bash
# PDF를 호스트 data/pdfs/에 올린 뒤
cp ~/Downloads/2026학년도2학기학사안내.pdf data/pdfs/

# 컨테이너 내부에서 인제스트 (경로는 /app/data/pdfs/)
docker exec -it backend python scripts/ingest_pdf.py \
  --pdf /app/data/pdfs/2026학년도2학기학사안내.pdf \
  --student-id 2024

# 상태 확인
docker exec backend python scripts/ingest_pdf.py --status
```

**실행 순서 원칙**

1. `build_graph.py` **먼저** (PDF → 그래프)
2. `ingest_pinned_notices.py` 나중 (공지 노드가 그래프와 엣지로 연결됨)
3. FAQ 재인제스트는 순서 무관
4. 대량 재인제스트 후 backend 재시작 권장 (`docker compose restart backend`) — 싱글톤 캐시 리프레시

### 4.3 피드백 → 캐시 무효화

**경로**: `backend/routers/feedback.py`

- 별점 저장: `data/logs/chat_*.jsonl`에 rating 필드 append
- 👎 피드백: 해당 쿼리의 응답 캐시 무효화 → 다음 유사 질의 시 재생성
- LLM 응답 캐시 TTL: `LLM_RESPONSE_CACHE_TTL=3600`초 / 최대 엔트리 `LLM_RESPONSE_CACHE_MAX_SIZE=256`
- 미답변 탐지: `ADMIN_FAQ_RATING_THRESHOLD=2` 이하 → 관리자 인박스 자동 집계

**수동 전체 캐시 플러시** (🔴): `docker compose restart backend` (메모리 LRU 초기화)

### 4.4 관리자 FAQ 큐레이션 백엔드 흐름

1. 학생 질의 → `ChatLogger`가 컨테이너 내부 `/app/data/logs/chat_*.jsonl`에 저장 (호스트 `data/logs/`에 영속)
2. 응답에 refusal 문구 포함 OR rating ≤ `ADMIN_FAQ_RATING_THRESHOLD=2` → 미답변 후보 풀 편입
3. 자카드 유사도 `ADMIN_FAQ_CLUSTER_SIM=0.6`으로 클러스터링
4. 관리자가 Next.js `/admin/faq` 페이지에서 그룹 확인 (프론트엔드 → `GET /api/admin/faq/*` 호출)
5. "FAQ로 추가" 클릭 → `POST /api/admin/faq` → `data/faq_admin.json`에 append
6. 기존 FAQ와 `ADMIN_FAQ_DEDUP_SIM=0.75` 이상 유사 → API가 409 또는 경고 반환 → UI에서 기존 수정 유도
7. 저장 시 질문을 과거 발화한 학생 세션에 알림 전송 (`NotificationConfig`)
8. FAQ 역인덱스 자동 재빌드 (backend 프로세스 내, 재시작 불필요)

### 4.5 로그

| 파일 (호스트 경로) | 용도 |
|---|---|
| `data/logs/chat_YYYY-MM-DD.jsonl` | 대화 로그. 필드: `timestamp, session_id, student_id, intent, question, answer, duration_ms, rating, context_confidence` |
| `data/logs/admin_audit.log` | 관리자 작업 감사 (로그인, FAQ 수정, 데이터 변경). **삭제 금지** |
| `docker compose logs <container>` | 컨테이너 런타임 로그 (stdout/stderr) — backend 에러·스케줄러 실행 기록 등 |

**실시간 조회**

```bash
# 호스트에서 파일 직접 tail
tail -f data/logs/chat_$(date +%Y-%m-%d).jsonl

# 컨테이너 stdout 스트리밍
docker compose logs -f backend | grep -vE "^backend  \| $"
```

로그 용량 관리 TODO: 현재 순환 미구현. 6개월+ 누적 시 아카이브 수동 필요. `docker logs` 자체도 `/var/lib/docker/containers/*/`에 누적되므로 주기적 정리 필요 (docker 데몬 `log-opts`에 `max-size`·`max-file` 설정 권장).

---

## 5. 데이터 관리 (지식 추가·수정·삭제)

### 5.1 새 학사안내 PDF (학기 교체)

🔴 증분 불가, 전체 재빌드 권장

```bash
# 1) 신규 PDF 배치 (기존은 백업) — 호스트에서
mkdir -p data/pdfs/backup_$(date +%Y%m%d)
cp data/pdfs/2026학년도1학기학사안내.pdf data/pdfs/backup_$(date +%Y%m%d)/
cp ~/Downloads/2026학년도2학기학사안내.pdf data/pdfs/

# 2) 그래프 재빌드 (컨테이너 내부)
docker exec -it backend python scripts/build_graph.py

# 3) 벡터DB 인제스트 (학번 지정 가능, 기본 2023)
docker exec -it backend python scripts/ingest_pdf.py \
  --pdf /app/data/pdfs/2026학년도2학기학사안내.pdf \
  --student-id 2024

# 4) 고정공지 재연결 (그래프와 엣지)
docker exec -it backend python scripts/ingest_pinned_notices.py

# 5) backend 재시작 (싱글톤 캐시 갱신)
docker compose restart backend

# 6) 회귀 평가 (§7) — backend가 healthy 상태여야 함
docker exec -it backend python scripts/eval_contains_f1.py --datasets \
  /app/data/eval/rag_eval_dataset_2026_1.jsonl \
  /app/data/eval/user_eval_dataset_50.jsonl \
  /app/data/eval/balanced_test_set.jsonl

# 7) 기준선 대비 회귀 확인 — -1pp 이상 회귀 시 rollback
```

**백업 권장 대상** (호스트에서 직접, 컨테이너 영향 없음):
- `data/chromadb_new/` → `data/chromadb_new.bak_YYYYMMDD/`
- `data/graphs/academic_graph.pkl` → `.bak_YYYYMMDD` 복사

### 5.2 고정공지 추가/해제

🟢 학사지원팀 자력 가능: 홈페이지 측에서 공지 고정 → 자동 크롤(30분) 또는 `/admin/crawler` → "지금 실행" (Next.js → `POST /api/admin/crawler/trigger`)

🔴 블랙리스트 기반 제외: `data/crawl_meta/blacklist.json`에 URL·제목 패턴 추가 (호스트에서 직접 수정 가능) → 다음 크롤에서 반영. 변경 즉시 반영하려면 `docker compose restart backend`.

### 5.3 FAQ CRUD

**UI 경로** (🟢): Next.js `/admin/faq` → CRUD 버튼 (프론트엔드 → `GET/POST/PUT/DELETE /api/admin/faq/*`)

**파일 직접 편집** (🔴, 예외적)

| 파일 | 용도 | 관리 주체 |
|---|---|---|
| `data/faq_academic.json` | 정식 FAQ (큐레이션 완료) | 개발자 |
| `data/faq_admin.json` | 관리자 UI 추가 FAQ (런타임 생성) | 자동 관리 (UI 경유) |
| `data/faq_combined.json` | 머지 인덱스 | 자동 생성 (재인제스트 시) |

실제 `faq_academic.json` 스키마:
```json
[
  {
    "id": "FAQ-0001",
    "category": "수강신청",
    "question": "수강신청 정정 기간은 언제인가요?",
    "answer": "..."
  },
  ...
]
```

필드:
- `id` — 고유 식별자 (예: `FAQ-0001`). 신규 추가 시 마지막 번호+1
- `category` — 분류 (수강신청·졸업·증명서·장학 등)
- `question` — 질문 원문
- `answer` — 답변 본문
- (관리자 UI 경유 추가 시 `faq_admin.json`에 저장, 추가 메타데이터 가능)

파일 직접 편집 후 역인덱스 재빌드 필요:
```bash
docker exec -it backend python scripts/ingest_faq.py
docker compose restart backend   # 메모리 캐시도 교체하려면
```

### 5.4 학과 연락처 (`data/contacts/departments.json`)

🟢 Next.js `/admin/contacts` 페이지에서 수정 → `PUT /api/admin/contacts/*` → 즉시 반영
🔴 파일 직접 편집 시 `docker compose restart backend` 권장 (메모리 캐시)

### 5.5 그래프 노드·엣지 수정

🔴 개발자 필수

- 원천: `data/pdfs/` + PDF 파서 결과
- 재빌드: `docker exec -it backend python scripts/build_graph.py` → `/app/data/graphs/academic_graph.pkl`
- pickle 직접 편집 금지 (무결성 위험)
- 재빌드 후 `docker compose restart backend`

### 5.6 학생포털 정적 페이지

🔴 `docker exec -it backend python scripts/ingest_static_page.py` (URL 기반)

### 5.7 지식 "삭제" 절차

| 대상 | 절차 | 태그 |
|---|---|---|
| FAQ | `/admin/faq`에서 삭제 → 즉시 검색 제외 | 🟢 |
| 공지 | `blacklist.json`에 패턴 추가 후 다음 크롤에서 반영 | 🔴 |
| PDF | `data/pdfs/`에서 제거 + `docker exec backend python scripts/rebuild_chromadb.py` | 🔴 |
| 그래프 노드 | 유발 PDF 내용 제거 + `build_graph.py` 재실행 + backend 재시작 | 🔴 |

---

## 6. 장애 대응 런북

각 증상은 **원인 후보 → 확인 명령 → 복구 절차 → 에스컬레이션** 구조. 모든 확인·복구 명령은 **호스트 셸**에서 실행 (프로젝트 루트 또는 `docker/` 하위).

### 6.0 장애 공통 체크리스트 (증상 진단 전 30초 스캔)

```bash
# 1) 컨테이너 상태 — 모두 healthy이어야 정상
docker compose ps

# 2) 헬스체크 (nginx 경유)
curl http://localhost/api/health
# 200 + {"status":"ok","pipeline_ready":true} 기대

# 3) 최근 backend 로그 — ERROR·WARNING 확인
docker compose logs --tail 200 backend | grep -iE "error|warning|traceback"

# 4) 디스크 여유 (데이터 볼륨)
df -h ./data

# 5) GPU 가용 (backend Reranker)
docker exec backend nvidia-smi 2>/dev/null | head -10 || echo "GPU 없음 또는 NVIDIA toolkit 미설치"
```

### 6.1 "답변을 생성할 수 없습니다" / 답변 빈 상태

**원인 후보**
- 원격 Ollama 다운 (가장 흔함)
- 네트워크 단절 (컨테이너→`192.168.0.4` 도달 불가)
- 모델 미로드 (`qwen3.5:9b` pull 누락)

**확인**

```bash
# 호스트에서 직접
curl -v http://192.168.0.4:11434/api/tags

# backend 컨테이너 내부에서도 접근 가능한지 (Docker 브리지 네트워크 확인)
docker exec backend curl -v http://192.168.0.4:11434/api/tags
```

**복구**
1. 원격 머신 접속 → `ollama serve` 프로세스 확인
2. 죽었으면 재기동
3. 모델 없으면 원격 머신에서 `ollama pull qwen3.5:9b`
4. 네트워크 문제면 라우터·방화벽·Docker 브리지 점검

**에스컬레이션**: 복구 15분 내 미해결 → 수환 + 임성원 박사

### 6.2 검색 10초+ 지연

**원인 후보**
- ChromaDB 잠금 (동시 재인제스트 중)
- 리랭커 OOM (`RERANKER_CANDIDATE_K` 과다)
- 임베딩 모델 미캐시 (최초 구동 직후, ~2분 소요)
- 원격 Ollama 네트워크 지연
- rewrite 모델(`gemma3:4b`) 미기동 상태에서 5초 타임아웃 누적

**확인**

```bash
# ChromaDB 잠금 파일 확인
ls -la data/chromadb_new/ | grep -iE "lock|wal|shm"

# backend 로그에서 지연 패턴
docker compose logs --tail 500 backend | grep -iE "slow|timeout|oom"

# GPU 메모리
docker exec backend nvidia-smi
```

**복구**
1. 잠금 파일 잔존 → `docker compose down && docker compose up -d` (볼륨은 유지됨)
2. OOM → `.env`에서 `RERANKER_CANDIDATE_K=15` 확인 (S2 실험: 50 → 지연 2~16초)
3. 최초 구동 직후면 5분 대기 (모델 캐시 워밍)
4. rewrite 타임아웃 누적 → `CONV_REWRITE_BASE_URL=""` 로 변경 (§3.10)

### 6.3 그래프 로드 실패 (backend 기동 실패)

**원인**: `data/graphs/academic_graph.pkl` 파손·누락·NetworkX 버전 불일치

**확인**

```bash
# backend 재기동 시도 후 로그
docker compose logs --tail 100 backend | grep -iE "graph|pickle|networkx"

# 그래프 파일 검증 (컨테이너 내부)
docker exec backend python -c "
import pickle
with open('/app/data/graphs/academic_graph.pkl', 'rb') as f:
    g = pickle.load(f)
print(g.number_of_nodes(), g.number_of_edges())
"
```

**복구**
1. `*.bak_*` 백업 복원 (호스트에서 파일 교체)
2. 없으면 재빌드:
   ```bash
   docker exec -it backend python scripts/build_graph.py
   docker exec -it backend python scripts/ingest_pinned_notices.py
   docker compose restart backend
   ```

### 6.4 크롤러 정지

**원인**: APScheduler 잡 실패 · `CRAWLER_ENABLED=false` · backend 재시작 시 잡 재등록 실패

**확인**

```bash
# 호스트에서 크롤 이력
tail -n 20 data/crawl_meta/crawl_history.jsonl

# backend 로그에서 스케줄러 관련
docker compose logs --tail 500 backend | grep -iE "scheduler|apscheduler|crawl"

# 환경변수 확인
docker exec backend printenv | grep -E "CRAWLER_"
```

**복구**
1. `.env` 확인 → `CRAWLER_ENABLED=true` → 필요 시 `docker compose up -d --force-recreate backend`
2. 수동 실행: Next.js `/admin/crawler` → "지금 실행"
3. 파싱 실패·타임아웃 → gnuboard 구조 변경 가능. 로그에서 구체 에러 확인 후 `app/crawler/` 수정 + 이미지 rebuild

### 6.5 관리자 계정 잠김

**원인**: 5회 연속 로그인 실패 → 15분 잠금 (backend 메모리 보관)

**복구**
- 15분 대기 OR
- 🔴 `docker compose restart backend` (잠금 메모리 초기화) OR
- `.env`의 `ADMIN_PASSWORD` 재설정 후 `docker compose up -d --force-recreate backend`

### 6.6 답변이 이전 정보만 반환 (캐시 고착)

**원인**: LLM 응답 캐시 TTL(`3600`초) 미경과 + 데이터 업데이트 직후

**복구**
1. 질문 표현 변경 (캐시 키 달라짐)
2. 30분+ 대기 (TTL 만료)
3. 🔴 `docker compose restart backend` (메모리 LRU 전체 초기화)

### 6.7 OCR 결과 깨짐 / PDF 인제스트 실패

**원인**: 컨테이너에 Tesseract 미설치·언어팩 누락·PDF 손상

**확인·복구**

```bash
docker exec backend tesseract --version
docker exec backend tesseract --list-langs
# kor·eng 없으면 Dockerfile.backend에 apt-get install tesseract-ocr-kor tesseract-ocr-eng 추가 후 rebuild
```

### 6.8 nginx·frontend 장애

**증상**: 브라우저에서 Bad Gateway(502), 응답 없음, 정적 파일 미로드

**확인**

```bash
docker compose ps                 # nginx·frontend 상태
docker compose logs --tail 100 nginx
docker compose logs --tail 100 frontend

# backend 도달성 (frontend·nginx 관점)
docker exec nginx wget -qO- http://backend:8000/api/health
docker exec frontend wget -qO- http://backend:8000/api/health
```

**복구**

```bash
docker compose restart nginx       # 라우팅만 리프레시
docker compose restart frontend    # Next.js 재기동
docker compose up -d --force-recreate   # 전체 (순차 재기동)
```

### 6.9 이미지·빌드 장애

**증상**: `docker compose up --build`가 실패 / ImportError / 모듈 없음

**확인**
```bash
docker compose build --no-cache backend    # 캐시 무효화 빌드
docker compose build --no-cache frontend
```

**복구**
1. 실패 로그에서 구체 단계 확인 (apt/pip/npm 중 어디)
2. `requirements.txt` 또는 `frontend/package.json` 의존성 변동 확인
3. 극단적인 경우 이미지 전체 제거 후 재빌드:
   ```bash
   docker compose down
   docker image prune -f
   docker compose up -d --build
   ```

---

## 7. 평가·정답률 관리

### 7.1 실행

`--datasets`는 **필수** 인자 (여러 개 공백 나열 가능). **backend 컨테이너 내부에서 실행** — 파이프라인 싱글톤·모델 캐시를 재사용.

```bash
# 기본 164문항 (3개 데이터셋 조합)
docker exec -it backend python scripts/eval_contains_f1.py --datasets \
  /app/data/eval/rag_eval_dataset_2026_1.jsonl \
  /app/data/eval/user_eval_dataset_50.jsonl \
  /app/data/eval/balanced_test_set.jsonl

# 결과 파일 (호스트): reports/eval_contains_f1/combined_YYYYMMDD_HHMMSS.json
# (reports/는 .dockerignore로 이미지 제외, 호스트에서만 관리)
#
# ⚠️ 주의: Dockerfile.backend가 COPY 대상에 scripts/는 포함하지만
#         reports/는 제외하므로, 출력 디렉터리를 컨테이너에서 만들려면
#         볼륨 마운트 필요. 현재는 컨테이너 내부 `/app/reports/`에 저장되고
#         컨테이너 라이프사이클과 함께 사라지므로, 재빌드 전 호스트로 복사:
docker cp backend:/app/reports ./
```

추가 옵션:
- `--base-url` — 평가할 서버 엔드포인트 (기본: 내부 파이프라인 직접 호출)
- `--limit N` — 각 데이터셋 상위 N문항만
- `--tag STR` — 출력 파일명 suffix (A/B 테스트 구분용)
- `--output DIR` — 출력 디렉터리 오버라이드 (호스트 볼륨에 쓰려면 `/app/data/eval_runs/` 등 마운트된 경로 지정 권장)

### 7.2 기준선 (2026-04-22 시점)

| 기준 | 값 | 파일 |
|---|---|---|
| **현재 기준선 (2026-04-21)** | **83.54%** Contains-F1 | `combined_20260421_153203.json` · commit `99a01df` (학사지원팀 FAQ 피드백 반영 +6.71pp) |
| 참고 (2026-04-22, ChromaDB 재인제스트 후) | 81.10% | `combined_full_crawl_rebuild_20260422_*.json` — 신입생가이드북 OCR 미설치 구간으로 -2.44pp (허용 범위) |
| 구 기준선 (2026-04-18) | 76.8% | `combined_no_tier1_boost_20260418_094724.json` — FAQ 피드백 반영 전 |
| 초기 기준선 (2026-04-16) | 81.7% | `combined_slicing_off_20260416_002907.json` — 모델·Tier 부스트 구조 변경 이전 |

### 7.3 NO-GO 기준 (CLAUDE.md 준수)

아래 중 하나라도 해당 시 커밋·배포 보류:

- 전체 정답률 **-1pp 이상** 회귀
- 단일 데이터셋 **-3pp 이상** 회귀
- 거부율 **-10pp 이상** 폭락

회귀 발생 시 커밋 보류 → 원인 분석 → 수환에게 보고.

### 7.4 정답률 체크 생략 가능한 변경

- UI 텍스트·주석·로그 메시지·문서 수정
- 단, 사유를 커밋 메시지 한 줄로 명시

### 7.5 기타 평가 스크립트

`scripts/eval_*`, `scripts/evaluate.py`, `scripts/eval_ragas.py`, `scripts/eval_llm_judge.py`, `scripts/eval_full.py` 등 — 모두 `docker exec -it backend python scripts/<name>.py ...` 형태로 실행. 용도·인자는 각 스크립트 상단 docstring 또는 `--help` 참조.

---

## 8. 배포·외부 노출

### 8.1 현재 상태 (2026-04-22)

- **운영 머신**: 수환의 개발 노트북 (LAN IP)
- **컨테이너**: `docker compose up -d` (백그라운드 구동)
- **재시작 정책**: `restart: unless-stopped` (compose 파일에 설정됨 → Docker 데몬 재시작 시 컨테이너 자동 복구)
- **원격 Ollama**: `192.168.0.4:11434` (별도 머신)
- **호스트 Ollama** (rewrite용): `host.docker.internal:11434` (수환 노트북 내 경량 `gemma3:4b`)
- **외부 노출**: Cloudflare Tunnel **수동 실행** (영구 서비스 미등록 — 4/23 D-1 할 일)

### 8.2 Cloudflare Tunnel → nginx:80 연결

**구조**

```
외부 도메인(ex: chatbot.maruvis.co.kr)
    │ (HTTPS)
    ▼
Cloudflare Edge
    │ (Tunnel)
    ▼
Cloudflared 프로세스 (호스트)
    │ (http://localhost:80)
    ▼
nginx 컨테이너 (80) → frontend/backend
```

**설정 파일 예시** (`~/.cloudflared/config.yml`)

```yaml
tunnel: <TUNNEL-ID>
credentials-file: ~/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: chatbot.maruvis.co.kr
    service: http://localhost:80
  - service: http_status:404
```

> `credentials-file`·토큰 값은 **절대 Git 커밋 금지**.

**수동 실행** (현재 방식)

```bash
cloudflared tunnel --config ~/.cloudflared/config.yml run
```

### 8.3 Cloudflare Tunnel 영구 서비스 등록 (TODO — 4/23 D-1)

**Windows (NSSM)**

```cmd
nssm install cloudflared "C:\cloudflared\cloudflared.exe" "tunnel --config C:\Users\<user>\.cloudflared\config.yml run"
nssm set cloudflared AppDirectory C:\cloudflared
nssm set cloudflared Start SERVICE_AUTO_START
nssm start cloudflared
```

**Linux (systemd)** — `/etc/systemd/system/cloudflared.service`

```ini
[Unit]
Description=Cloudflare Tunnel
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
TimeoutStartSec=0
Type=notify
ExecStart=/usr/local/bin/cloudflared tunnel --config /etc/cloudflared/config.yml run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared
```

### 8.4 Docker 자동 기동 (호스트 재부팅 복구)

**Linux**: Docker 데몬을 systemd로 enable하면, `restart: unless-stopped` 정책에 따라 compose 컨테이너가 부팅 후 자동 복구됨.

```bash
sudo systemctl enable docker
```

**Windows**: Docker Desktop 설정 → "Start Docker Desktop when you log in" 체크 → 추가로 compose 재적용이 필요하면 Windows Task Scheduler에 `docker compose -f docker/docker-compose.yml up -d` 로그인 시 실행 등록.

### 8.5 권장 개선 (파이널 이후)

- Cloudflare Tunnel NSSM·systemd 등록 완료 검증 (재부팅 테스트)
- 로그 순환 정책 구현 (현재 수동 아카이브)
- 외부 헬스체크 모니터링 (UptimeRobot 등으로 `https://chatbot.maruvis.co.kr/api/health` 주기 호출)
- 이미지 레지스트리 도입 (빌드 머신·운영 머신 분리 시)

---

## 9. 백업·복구

**원칙**: 모든 영속 데이터는 **호스트의 `data/`·`.env`**에 있음. 컨테이너는 Stateless(재생성 가능) — 백업은 호스트 파일 시스템만 대상.

### 9.1 백업 대상

| 경로 (호스트) | 권장 주기 | 복구 가능성 | 비고 |
|---|---|---|---|
| **`data/chromadb_new/`** | 주 1회 | 재빌드 가능 (시간 소요) | 수백 MB. **운영 활성 DB** (compose override) |
| `data/chromadb/` | 필요 시 | — | 이전·실험용 — 운영엔 사용 안 함 |
| `data/graphs/academic_graph.pkl` | 주 1회 | 재빌드 가능 | 수 MB |
| `data/logs/chat_*.jsonl` | 매일 | **복구 불가** (대화 이력) | 증분 |
| `data/logs/admin_audit.log` | 매일 | **복구 불가** | 삭제 금지 |
| `data/faq_academic.json` · `faq_admin.json` | 변경 시 즉시 | **복구 불가** | 수십 KB |
| `data/faq_combined.json` | 자동 생성 | 재생성 가능 | 인제스트 시 자동 |
| `data/contacts/departments.json` | 변경 시 즉시 | **복구 불가** | KB |
| `data/crawl_meta/*` | 매일 | 재크롤 가능(부담) | KB~MB |
| `data/pdfs/` | 학기 시작 시 | 홈페이지 재다운로드 가능 | 수백 MB |
| `config/` | 변경 시 | 저장소에서 복원 가능 | `academic_terms.yaml`, `en_glossary.yaml` |
| `.env` | 변경 시 즉시 (암호관리자 보관) | **복구 불가** | 절대 Git 금지 |
| `docker/docker-compose.yml`·`docker/nginx/default.conf` | Git으로 관리 | Git 복원 | — |

> **컨테이너 이미지**: 재빌드 가능하므로 백업 불필요. 필요 시 `docker save backend > backend.tar` 가능하지만 크기 크고 의미 작음.

### 9.2 백업 절차 (호스트에서, 컨테이너 영향 없음)

```bash
# 리눅스/macOS
STAMP=$(date +%Y%m%d)
tar czf ../backup_$STAMP.tar.gz \
  --exclude="data/chromadb.bak-*" \
  --exclude="data/chromadb.corrupted-*" \
  data/ config/ .env docker/
```

```powershell
# Windows PowerShell
$STAMP = Get-Date -Format "yyyyMMdd"
& "C:\Program Files\7-Zip\7z.exe" a "..\backup_$STAMP.7z" `
  data\ config\ .env docker\ `
  -xr!chromadb.bak-* -xr!chromadb.corrupted-*
```

**핫 백업 주의**: 컨테이너 구동 중 `data/chromadb_new/` 복사 시 WAL/SHM 파일이 일관성 깨질 수 있음. 안전하게 하려면:

```bash
docker compose stop backend       # 크롤러·쓰기 중단
# 백업 실행
docker compose start backend
```

### 9.3 복구 절차

```bash
# 1) 서비스 중단
cd docker && docker compose down

# 2) 현 data/를 보존 (롤백 대비)
mv data data_broken_$(date +%Y%m%d)
mv .env .env.broken_$(date +%Y%m%d)

# 3) 백업에서 복원
tar xzf ../backup_YYYYMMDD.tar.gz      # data/·config/·.env 재현

# 4) 서비스 재기동
cd docker && docker compose up -d

# 5) 헬스체크 + 스모크 테스트
curl http://localhost/api/health       # pipeline_ready:true 대기 (최대 2~3분)
#    브라우저 / 접속 후 질문 1개, /admin 접속 확인

# 6) 회귀 평가
docker exec -it backend python scripts/eval_contains_f1.py --datasets \
  /app/data/eval/rag_eval_dataset_2026_1.jsonl \
  /app/data/eval/user_eval_dataset_50.jsonl \
  /app/data/eval/balanced_test_set.jsonl
#    기준선 대비 회귀 없으면 운영 재개
```

### 9.4 복구 리허설

분기 1회 권장 (실제 실행, 20~40분 — 모델 로드 시간 포함).

---

## 10. 보안·개인정보

### 10.1 `.env` 관리

- 평문 저장 — **절대 Git·Slack·메신저·이메일 공유 금지**
- 최초 세팅 시 대면 전달 또는 암호관리자(1Password·Bitwarden 등)
- 로테이션: 분기 1회 이상 (특히 `ADMIN_PASSWORD`)
- TODO: `.env.example` 템플릿 생성 (시크릿 제외, 변수 목록만)

### 10.2 비밀번호 로테이션 절차

1. 새 비밀번호 선정 (12자 이상, 영·숫자·특수 혼합)
2. `.env`의 `ADMIN_PASSWORD` 수정
3. FastAPI 재시작 → 기존 세션 무효화 확인
4. 학사지원팀에 대면 전달
5. `admin_audit.log`에 변경 기록 남김

### 10.3 대화 로그 보관 정책

- 저장: `data/logs/chat_YYYY-MM-DD.jsonl`
- 필드: `timestamp, session_id, student_id, intent, question, answer` (+ rating·feedback)
- **학번 포함** → 개인정보 범주
- 권장 보관: 6개월 (이후 익명화 또는 삭제)
- **TODO**: 부산외대 개인정보 보관 정책 공식 확인 후 확정
- 접근 권한: 관리자 + 개발자

### 10.4 감사 로그

- `data/logs/admin_audit.log` — 관리자 로그인·FAQ 수정·데이터 변경
- **삭제 금지**. 6개월 이상 보존 권장

### 10.5 네트워크

- 학내 LAN + Cloudflare Tunnel(`~/.cloudflared/config.yml` → `maruvis.co.kr`) 로만 외부 노출
- 방화벽: 8000(backend) · 3000(frontend) · 80(nginx) 포트 외부 직접 노출 금지 (Tunnel 경유만)
- 원격 Ollama(`192.168.0.4`) 인증 없음 → LAN 격리 필수

---

## 11. 비상 연락망

### 증상별 연락 흐름

| 증상 | 1차 연락 | 2차 연락 | 에스컬레이션 기준 |
|---|---|---|---|
| 서버 다운 · Ollama 이슈 | 수환 | 임성원 박사 | 15분 내 미해결 |
| 영어 버전·번역 이슈 | 임성원 박사 | 수환 | 1시간 내 미해결 |
| 데이터·FAQ 이슈 | 학사지원팀 자력 시도 | 수환 | 30분 내 |
| 문서·회의록·공문 | 백민서 | — | — |
| 학술·정책 판단 | 조영민 교수 | 수환 | 정책 방향 결정 필요 시 |
| 복수 증상 동시 발생 | 수환 (지휘) | 3인 단톡 동시 공지 | 서비스 다운 + 학생 민원 폭주 |

### 연락처 테이블 (※ 파이널 전 확정)

| 이름 | 역할 | 전화 | 이메일 | 메신저 | 가용 시간 |
|---|---|---|---|---|---|
| 수환 | 대표·제품·인프라 | TBD | TBD | TBD | TBD |
| 임성원 박사 | 최적화·영어·자문 | TBD | TBD | TBD | TBD |
| 백민서 | 문서·회의록 | TBD | TBD | TBD | TBD |
| 조영민 교수 | 학술 자문 | TBD | TBD | — | TBD |
| 학사지원팀 담당자 | 운영 담당 | TBD | TBD | — | TBD |

### 11.1 에스컬레이션 흐름

1. 증상 확인 → 1차 연락 → 15~30분 내 미응답/미해결 → 2차 연락
2. 1차·2차 모두 미응답 → 수환에게 문자+전화 조합
3. 긴급(서비스 다운 + 학생 민원 폭주) → 즉시 3인 단톡 동시 공지

---

## 12. 부록

### 12.1 알려진 이슈 (2026-04-22)

1. **Recall@5 36~54%** — 검색 품질 개선 여지. 임성원 박사 실험 진행 중
2. **영어 버전 불안정** — 일부 용어 미매핑. `config/academic_terms.yaml` 확장 필요 (FlashText aliases_en). Ollama 네이티브 `/api/chat` 로 전환 완료 (`cb825f4`) 이후 think:false 정상 동작
3. **관리자 대시보드 UI 통합 진행 중** — Next.js `/admin` 하위에 기능별 페이지 11개(faq·crawler·dashboard·graduation·graph·logs·contacts·schedule·early-grad·root·layout) 존재, 종합 대시보드(`/admin/dashboard`)는 있으나 지표 통합 미완
4. **Cloudflare Tunnel 영구 서비스 미등록** — 재부팅 시 수동 실행 필요 (4/23 D-1 할 일 / §8.3)
5. **운영 매뉴얼 초판** — 이 문서. 1~2주 운영 후 피드백 반영 필요
6. **영어 평가셋 부재** — 한국어 164문항만 운영 중
7. **ChromaDB 초기 빌드 segfault** — `QUERY_ROUTER_SEQUENTIAL=1`로 회피 중, 근본 원인 미파악
8. **`.env`에 `GOOGLE_API_KEY` 존재** — 사용처 추적 TODO (제거 후보)
9. **로그 순환 미구현** — 호스트 `data/logs/` 및 `docker logs` 양쪽 모두. 6개월+ 시 수동 아카이브 필요. docker 데몬에 `log-opts` 설정 권장
10. **`backend` 컨테이너 ↔ 크롤러 결합** — APScheduler가 backend lifespan에서만 기동. `docker compose restart backend` 시 스케줄러도 재기동 됨 → 재기동 직후 첫 잡 발사 시점 확인 필요
11. **레거시 Streamlit 코드 잔존** — `main.py`, `app/ui/chat_app.py`, `pages/admin.py`·`logs.py`는 Docker 이미지 미포함이지만 리포지토리에 남아있음. 개발자가 호스트에서 우연히 실행해 **같은 `data/` 볼륨에 쓰기 충돌** 위험. 파이널 후 삭제 또는 명시적 "dev-only" 디렉터리로 격리 검토
12. **`reports/eval_contains_f1/`은 `.dockerignore`로 이미지 제외** — backend 컨테이너에서 평가 실행 시 결과가 컨테이너 내부에만 저장되므로 `docker cp` 또는 볼륨 마운트 필요 (§7.1)
13. **CHROMA_PERSIST_DIR 경로 혼란 가능성** — config 기본값은 `data/chromadb`인데 compose override는 `data/chromadb_new`. 운영자가 기본값 기준으로 착각하면 "빈 DB" 증상 경험 가능. 이 매뉴얼에서는 운영 활성 경로를 **`data/chromadb_new/`로 통일 명시**

### 12.2 TODO 목록

**즉시 수정 가능 (파이널 전에 처리 권장)**

- [ ] `GOOGLE_API_KEY` 사용처 추적 또는 제거 (§12.1-8)
- [ ] 호스트 Ollama(`gemma3:4b`) 상태 확인 — 없으면 `CONV_REWRITE_BASE_URL=""`로 변경해 5s 타임아웃 제거

**파이널 이후 작업**

- [ ] Cloudflare Tunnel NSSM·systemd 서비스 등록 (§8.3)
- [ ] Docker Desktop/데몬 자동 기동 설정 검증 (재부팅 테스트)
- [ ] Recall 개선 실험 재개 (박사님)
- [ ] 영어 평가셋 구축
- [ ] 관리자 종합 대시보드 지표 통합 (`/admin/dashboard`)
- [ ] 백업 자동화 스크립트 (cron 또는 Task Scheduler)
- [ ] 로그 순환 정책 구현 (파일 + docker log-opts)
- [ ] 학생 개인정보 보관 정책 기관 승인
- [ ] `.env.example` 템플릿 분리 (시크릿 제외)
- [ ] 레거시 Streamlit 코드 정리 또는 `legacy/` 디렉터리 격리 (§12.1-11)
- [ ] `reports/` 볼륨 마운트 추가해 컨테이너 내 평가 결과 자동 호스트 동기화 (§7.1)
- [ ] 이미지 레지스트리(로컬 또는 Docker Hub 프라이빗) 도입 검토 (빌드·운영 머신 분리 시)
- [ ] 외부 헬스체크 모니터링(UptimeRobot 등)
- [ ] 본 매뉴얼 1차 피드백 반영 (운영 2주 후)

### 12.3 관련 문서

- `README.md` — 프로젝트 개요·설치·평가
- `CLAUDE.md` — 4대 원칙·디렉터리 SSOT·검색 우선순위·커밋 규칙
- `docs/OPS_학사지원팀.md` — 학사지원팀 간략 매뉴얼
- `docs/archive/` — 과거 진단 리포트 (2026-04-05)

### 12.4 용어 (학사지원팀 매뉴얼과 공통)

- **RAG**: Retrieval-Augmented Generation
- **크롤러**: 홈페이지 자동 순회 수집 프로그램
- **임베딩**: 문장 → 수치 벡터 변환
- **ChromaDB**: 벡터 DB (SQLite 기반 로컬)
- **지식 그래프**: 규정 노드·엣지 네트워크 (NetworkX)
- **FAQ**: 자주 묻는 질문의 정답 사전
- **인제스트**: 원문 → 청킹 → 임베딩 → 저장
- **LLM**: 생성형 언어 모델
- **Ollama**: 로컬·LAN LLM 런타임
- **고정공지**: 게시판 상단 고정(📌). 우선순위 2
- **RRF**: Reciprocal Rank Fusion — 검색 결과 병합 기법

---

**문서 버전**: 1.0 (2026-04-22)
**관리자**: 수환
**피드백·오탈자**: 수환에게 전달 또는 `docs/archive/`에 수정 리포트 드롭
