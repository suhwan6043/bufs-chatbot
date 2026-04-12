# BUFS Academic Chatbot (캠챗)

부산외국어대학교 학사 안내 RAG 챗봇입니다.
로컬 LLM(LM Studio)과 하이브리드 검색(Vector DB + Knowledge Graph)을 사용하여 학사 관련 질문에 답변합니다.

## 아키텍처

```
사용자 질문
    │
    ▼
LanguageDetector    → 언어 감지 (ko / en), <1ms 휴리스틱
    │
    ▼
QueryAnalyzer       → 의도(Intent) + 엔티티 + 학번/학과/학기 파싱
    │  [EN 쿼리] FlashText(aliases_en→ko) 키워드 매핑 + en_glossary
    │            키워드 미검출 시 → GENERAL + BGE-M3 크로스링구얼 fallback
    │
    ├──▶ ChromaDB (Vector)  ──┐
    │    BGE-M3 임베딩         │ ← 병렬 실행 (검색 파이프라인 병렬화)
    │    department 필터       │
    │                          │
    ├──▶ AcademicGraph         │
    │    FAQ 역인덱스 캐시     │
    │    direct_answer / 고정공지 경로
    │                          │
    └──▶ CommunitySelector ◀──┘  동적 커뮤니티 선택으로 후보 축소
                │
                ▼
           Reranker            BGE-Reranker-v2-m3 (Top 5)
                │
                ▼
         ContextMerger         Vector + Graph 병합, 토큰 예산 관리
                │
                ▼
        AnswerGenerator        Ollama LLM (환경변수 LLM_MODEL)
                │              [EN] One-Pass 스트리밍: KO 초안 → 목표 언어 번역
                ▼
      ResponseValidator        답변 품질 검증
                │
                ▼
          ChatLogger           대화 로그 JSONL (data/logs/)
```

> **LLM 모델 SSOT**: 실제 사용 모델은 `.env` 또는 `app/config.py`의 `LLM_MODEL` 값을 기준으로 한다. 이 문서에는 모델명을 하드코딩하지 않는다 (CLAUDE.md 원칙 4).

## 주요 기술 스택

| 구성요소 | 기술 |
|----------|------|
| LLM | Ollama 로컬 모델 (환경변수 `LLM_MODEL`, `think=False`) |
| 임베딩 | BAAI/bge-m3 (multilingual, CPU) |
| 리랭킹 | BAAI/bge-reranker-v2-m3 (Cross-Encoder, CPU) |
| 벡터 DB | ChromaDB (SQLite 기반 로컬 파일) |
| 지식 그래프 | NetworkX (pkl 파일) + FAQ 역인덱스 캐시 |
| 검색 | Vector/Graph 병렬 실행 + 동적 커뮤니티 선택 |
| PDF 처리 | PyMuPDF + pdfplumber |
| 수업시간표 파싱 | timetable_parser (교시→시각 변환, 학과명 정규화) |
| 성적표 파싱 | transcript_parser (다중 포맷 성적표 업로드) |
| EN/KO 매핑 | FlashText (Aho-Corasick) + `config/en_glossary.yaml` |
| 다국어 | 언어 선택 랜딩 페이지 (ko / en i18n) |
| UI | Streamlit (멀티페이지: 챗봇 + 관리자 + 로그 뷰어) |

## 설치

### 요구사항

- Python 3.10+
- [LM Studio](https://lmstudio.ai) 설치 및 로컬 서버 실행 (포트 1234)
- 약 8GB+ VRAM (Qwen3.5-9B Q4_K_M 기준)
- 약 4GB+ RAM (BGE-M3 임베딩 모델 포함)

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

> **주요 신규 의존성** (`pip install` 후 추가 설치 불필요, requirements.txt에 포함됨)
>
> - `flashtext>=2.7` — EN 키워드 매핑 (Aho-Corasick)
> - `pyyaml>=6.0` — academic_terms.yaml 로드

### 2. Ollama 모델 설치

[Ollama](https://ollama.com)를 설치한 뒤, `.env` / `app/config.py`에서 사용 중인 모델명을 확인하여 다운로드합니다.

```bash
# 예: 현재 설정된 LLM_MODEL 값으로 교체
ollama pull <LLM_MODEL 값>
```

사용 모델은 설정 파일이 단일 진실원천(SSOT)입니다. 이 README에는 모델명을 하드코딩하지 않습니다.

### 3. 환경 설정 (선택)

`.env` 파일을 생성하여 설정을 커스터마이즈할 수 있습니다. 전체 변수는 하단의 **환경 변수 전체 목록** 섹션을 참조하세요.

```env
# LLM (Ollama OpenAI 호환 API)
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=<사용하려는 ollama 모델명>
LLM_MAX_TOKENS=2048
LLM_TEMPERATURE=0.1

# 임베딩 / 리랭킹
EMBEDDING_MODEL=BAAI/bge-m3
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_TOP_K=5
RERANKER_CANDIDATE_K=15

# ChromaDB
CHROMA_N_RESULTS=15
```

## 데이터 준비

### 전체 재학습 (한 번에)

```bash
python scripts/ingest_all.py
```

내부 실행 순서: PDF 그래프 빌드 → PDF 벡터DB 인제스트(학사안내 + 수업시간표) → 정적 페이지 크롤링 → 고정공지 인제스트

### 개별 실행 (단계별)

```bash
# 1단계: PDF → 벡터DB 인제스트
python scripts/ingest_pdf.py --pdf "data/pdfs/2026학년도1학기학사안내.pdf" --student-id 2024
python scripts/ingest_pdf.py --pdf "data/pdfs/2026학년도 1학기 수업시간표.pdf"

# 2단계: 그래프 재빌드 (PDF → 그래프DB) — 반드시 고정공지 전에 실행
python scripts/build_graph.py

# 3단계: 고정공지 인제스트 (홈페이지 공지 → 벡터DB + 그래프DB)
python scripts/ingest_pinned_notices.py

# 4단계: 학과별 졸업인증 데이터 업데이트
python scripts/update_dept_grad_exam.py
```

> **순서 중요**: `build_graph.py` 먼저, `ingest_pinned_notices.py` 나중에 실행해야 공지 노드가 기존 학사 그래프와 엣지로 연결됩니다.

### 기타 인제스트 명령

```bash
# DB 현황 확인
python scripts/ingest_pdf.py --status

# 추출 결과 JSON 저장 (디버깅용)
python scripts/ingest_pdf.py --pdf data/pdfs/파일.pdf --save-json
```

`--doc-type` 선택지: `domestic` (내국인, 기본값) / `foreign` (외국인) / `transfer` (편입생) / `schedule` (학사일정) / `timetable` (수업시간표)

## 실행

```bash
streamlit run main.py
```

| 페이지 | URL | 설명 |
|--------|-----|------|
| 챗봇 (메인) | `http://localhost:8501` | 학사 질문 답변 |
| 관리자 | `http://localhost:8501/admin` | 졸업요건·학사일정·크롤러 관리 |
| 로그 뷰어 | `http://localhost:8501/logs` | 대화 기록 조회·CSV 다운로드 |

## 프로젝트 구조

```
챗봇/
├── app/
│   ├── config.py               # 환경 변수 및 설정 관리
│   ├── models.py               # Pydantic 데이터 모델 (Chunk에 semester 필드 포함)
│   ├── embedding/
│   │   └── embedder.py         # BGE-M3 임베딩 래퍼
│   ├── pipeline/
│   │   ├── query_analyzer.py   # 의도 분류 + 엔티티 추출
│   │   ├── query_router.py     # Vector/Graph 라우팅 (department 필터)
│   │   ├── reranker.py         # BGE-Reranker-v2-m3 리랭킹
│   │   ├── context_merger.py   # 검색 결과 병합
│   │   ├── answer_generator.py # LLM 답변 생성
│   │   ├── glossary.py         # 도메인 용어 정규화
│   │   └── response_validator.py
│   ├── vectordb/
│   │   └── chroma_store.py     # ChromaDB CRUD (department 필터 지원)
│   ├── graphdb/
│   │   ├── academic_graph.py   # NetworkX 지식 그래프 (동시접속 안전)
│   │   └── notice_graph_builder.py  # 공지사항 → 그래프 노드 변환
│   ├── pdf/
│   │   ├── detector.py         # PDF 유형 자동 감지
│   │   ├── digital_extractor.py# 디지털 PDF 파싱 + 청킹
│   │   ├── ocr_extractor.py    # 스캔 PDF OCR
│   │   └── timetable_parser.py # 수업시간표 전용 파서
│   ├── logging/
│   │   └── chat_logger.py      # 대화 로그 저장 (data/logs/ JSONL)
│   └── ui/
│       └── chat_app.py         # Streamlit 채팅 UI
├── pages/
│   ├── admin.py                # 관리자 페이지 (졸업요건·학사일정·크롤러)
│   └── logs.py                 # 로그 뷰어 페이지
├── scripts/                    # 인제스트 / 그래프 / 평가 / 유틸 스크립트 (그룹별 대표 파일)
│   ├── ingest_all.py           # 전체 재학습 오케스트레이션
│   ├── ingest_pdf.py           # PDF → ChromaDB
│   ├── ingest_pinned_notices.py# 고정공지 → 벡터DB + 그래프
│   ├── ingest_faq.py           # FAQ → 벡터DB (역인덱스 캐시 생성)
│   ├── ingest_static_page.py   # 학생포털 정적 페이지 크롤링
│   ├── build_graph.py          # 지식 그래프 빌드
│   ├── pdf_to_graph.py         # PDF → 학사 데이터 자동 파싱
│   ├── update_dept_grad_exam.py# 학과별 졸업인증 업데이트
│   ├── rebuild_chromadb.py     # ChromaDB 재빌드
│   ├── cleanup_duplicate_chunks.py  # 중복 청크 정리
│   ├── evaluate.py             # 자동 평가 + LLM-as-a-Judge
│   ├── eval_ragas.py           # RAGAS 기반 RAG 평가
│   ├── eval_f1_score.py        # F1 / 정답 매칭 기반 평가
│   └── ...                     # 기타 평가/유틸 (eval_benchmark, qualitative_judge 등)
├── data/
│   ├── pdfs/                   # 학사 안내 PDF 파일
│   ├── chromadb/               # ChromaDB 영구 저장소
│   ├── graphs/                 # academic_graph.pkl
│   ├── logs/                   # 대화 로그 (chat_YYYY-MM-DD.jsonl)
│   └── eval/                   # 평가 데이터셋/결과
├── tests/                      # 파이프라인·그래프·파서 단위/통합 테스트 (19개)
├── requirements.txt
└── main.py
```

## 주요 기능

| 기능 | 설명 |
|------|------|
| 하이브리드 검색 | Vector(ChromaDB) + Graph(NetworkX) 결과 병합 |
| 학과별 수업시간표 | `department` 필터로 타 학과 청크 혼입 방지 |
| 관리자 페이지 | 졸업요건·졸업인증·학사일정·크롤러 관리 (동시접속 지원) |
| 면책 조항 동의 | 온보딩 시 AI 답변 면책 동의 체크박스 |
| 별점 피드백 | 답변 하단 1~5점 평가, 로그에 함께 저장 |
| 대화 로그 | 날짜별 JSONL 저장, 웹 UI에서 CSV 다운로드 |
| 학번 인식 | 질문 내 학번 자동 파싱 → 맞춤 졸업요건 답변 |
| 도메인 용어 정규화 | 한국어 약어/별칭 → 정규 명칭 변환 |
| 고정공지 연동 | 학사공지 고정공지 자동 크롤링 → 벡터DB + 그래프 연결 |
| 포털 링크 | 오른쪽 패널에 학사 포털 바로가기 |

## 평가

### 평가 실행

```bash
# 자동 평가 (Hit Rate, Contains GT)
python scripts/evaluate.py --dataset data/eval/rag_eval_dataset_2026_1.jsonl

# 빠른 확인용 (Judge, Reranker 비활성화)
python scripts/evaluate.py --dataset data/eval/rag_eval_dataset_2026_1.jsonl --no-judge --no-rerank

# 정성적 Judge (0-5 척도, 기존 결과 JSON 재활용)
python scripts/qualitative_judge.py data/eval/eval_results_XXXXXXXX_XXXXXX.json
```

### 최신 평가 요약 (2026-03-09, 2026학년도 1학기 50문항)

| 지표 | 값 |
|------|----|
| Hit Rate | 100.0% |
| Contains GT | 74.0% |
| LLM Judge Correctness (정확성) | **94.0%** (47/50 정답) |
| 정성 Judge 평균 정확성 | **4.8 / 5** |
| 답변 정상률 / 출처 표기율 | 100.0% / 100.0% |
| 평균 검색 시간 | 약 17초 |
| 평균 생성 시간 | 약 1.8초 |

난이도별 정확성:

| 난이도 | 정확성 | n |
|--------|--------|---|
| easy | 4.77 / 5 | 31 |
| medium | 4.81 / 5 | 16 |
| hard | 5.0 / 5 | 3 |

오류 유형 (3건):

- `incomplete` (3건): 정답 일부 포함, 조건/예외 누락 수준

### 평가 산출물

- `data/eval/rag_eval_dataset_2026_1.jsonl`: 2026학년도 1학기 평가셋 (50문항)
- `data/eval/rag_eval_dataset_100.jsonl`: 100문항 확장 평가셋
- `data/eval/eval_results_*.json`: 자동 평가 결과
- `data/eval/qualitative_report_*.md`: 정성적 Judge 보고서

## 테스트

```bash
# 프로젝트 루트에서
.venv/Scripts/pytest tests/ -v
```

테스트 범위: 파이프라인(쿼리 분석·컨텍스트 머지·응답 검증·글로서리), 그래프(academic_graph), 추출기(PDF/성적표), Phase1/Phase2 크롤러, 통합 테스트.

## 성능 특성

| 항목 | 설명 |
|------|------|
| 임베딩 | BGE-M3 (1024차원, CPU ~2-5초/배치) |
| 리랭킹 | Cross-Encoder (후보 15-20개 → 상위 5개, CPU ~1-2초) |
| LLM 응답 | Ollama 스트리밍 (모델은 `LLM_MODEL`에 따름) |
| 그래프 탐색 | NetworkX (즉시, <10ms) + FAQ 역인덱스 캐시 |
| 검색 | Vector/Graph 병렬 실행으로 응답 지연 단축 |

## 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_BASE_URL` | `http://localhost:11434` | Ollama 서버 주소 |
| `LLM_MODEL` | (config 기본값 참조) | LLM 모델 (OpenAI 호환 API, `app/config.py`의 기본값이 SSOT) |
| `LLM_MAX_TOKENS` | `2048` | 최대 생성 토큰 |
| `LLM_TEMPERATURE` | `0.1` | 생성 온도 |
| `LLM_TOP_P` | `0.9` | Top-p 샘플링 |
| `LLM_REPEAT_PENALTY` | `1.0` | 반복 페널티 |
| `LLM_TIMEOUT` | `60` | 요청 타임아웃 (초) |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 임베딩 모델 |
| `EMBEDDING_DEVICE` | `cpu` | 임베딩 디바이스 |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | 리랭커 모델 |
| `RERANKER_DEVICE` | `cpu` | 리랭커 디바이스 |
| `RERANKER_ENABLED` | `true` | 리랭킹 활성화 |
| `RERANKER_TOP_K` | `5` | 리랭킹 후 선택 수 |
| `RERANKER_CANDIDATE_K` | `15` | 리랭킹 전 후보 수 |
| `CHROMA_N_RESULTS` | `15` | ChromaDB 검색 결색 수 |
| `CHROMA_COLLECTION` | `bufs_academic` | 컬렉션 이름 |

## 업데이트 이력

### [2026-04 이후] EN/KO 패리티·검색 병렬화·기능 확장

최근 반영된 주요 변경(요약):

- **검색 파이프라인 병렬화** — Vector/Graph 검색을 병렬 실행해 응답 지연 단축
- **FAQ 역인덱스 캐시** — 그래프 FAQ 직접 답변 경로의 Lookup을 O(1)화
- **언어 선택 랜딩 페이지** — ko/en 랜딩 + UI 전체 i18n
- **EN/KO 검색 패리티** — BM25·엔티티 추출·글로서리 전면 보강, `config/en_glossary.yaml` 추가
- **다중 포맷 성적표 업로드** — `transcript_parser` 계열 신설, FAQ 근거문서 수정 플로우
- **로컬 파이프라인 수정사항 커밋** — `answer_generator`, `context_merger`, `query_analyzer` 세부 개선

> 세부 변경 내역은 `git log`를 참조. 본 섹션은 카테고리 수준 요약만 유지.

---

### [2026-04-05 / 임성원] EN 다국어 파이프라인 구축 및 One-Pass 스트리밍 아키텍처 도입

#### 변경 배경

기존 파이프라인은 영어 질문을 처리하지 못했습니다.

- `QueryAnalyzer`가 한국어 키워드만 인식 → 영어 질문 전부 `Intent.GENERAL` 분류 → 무관한 컨텍스트 검색
- LLM이 단일 호출로 KO 문서 검색·추론·EN 번역을 동시 수행 → 인지 부하 과다로 정확도 저하
- LM Studio + Qwen3.5-9B에서 `think=False` 미동작 → 빈 답변 다수 발생

#### 주요 변경 내용

#### 1. FlashText 기반 EN→KO 키워드 매핑 (`app/pipeline/query_analyzer.py`)

- `EnTermMapper` 싱글톤: `config/academic_terms.yaml`의 `aliases_en → ko` 매핑을 서버 시작 시 메모리 로드 (Aho-Corasick, O(N))
- 영어 쿼리 → KO 용어 추출 → 기존 Intent 분류기 재사용
- 키워드 미검출 시 `Intent.GENERAL` + BGE-M3 크로스링구얼 시맨틱 검색 fallback

#### 2. One-Pass Streaming 아키텍처 (`app/pipeline/answer_generator.py`)

단일 LLM 호출로 KO 초안 작성과 EN 번역을 순차 수행하여 TTFT(첫 토큰 응답 시간)를 1초 이내로 유지합니다.

```text
EN 질문 → LLM 단일 호출
  <ko_draft>  : KO로 완벽한 초안 작성 → 화면에 "규정 원문 분석 중..." 표시
  <final_answer>: EN으로 번역 → CLEAR 신호 후 메인 답변 스트리밍
```

Rolling Buffer State Machine으로 `<final_answer>` 태그가 여러 토큰에 쪼개져 오는 현상을 방어합니다. `<final_answer>` 미감지 시 KO 초안을 그대로 표시하는 fallback 포함.

#### 3. LLM 변경

LM Studio (Qwen3.5-9B) → Ollama (Qwen3:14b, `think=False`, `max_tokens=4096`)

#### 4. 기타

- `app/models.py` — `QueryAnalysis`에 `lang`, `matched_terms` 필드 추가
- `requirements.txt` — `flashtext>=2.7`, `pyyaml>=6.0` 추가

#### RAGAS 평가 결과 (Claude Haiku 판정)

| 모델 / 방식 | 언어 | Faithfulness | Answer Relevancy | Context Recall | Answer Correctness | 평균 |
|-------------|------|:------------:|:----------------:|:--------------:|:------------------:|:----:|
| qwen3:8b 직접 생성 | KO | 0.897 | 0.747 | 0.901 | 0.715 | 0.828 |
| qwen3:14b 직접 생성 | KO | 0.971 | 0.765 | 0.950 | 0.893 | **0.904** |
| qwen2.5:7b 직접 생성 | EN (개선 전) | — | — | 0.171 | 0.332 | — |
| qwen3:8b 직접 생성 | EN (FlashText) | 0.798 | 0.758 | 0.730 | 0.624 | 0.721 |
| qwen3:14b One-Pass | EN (FlashText) | 0.714 | 0.714 | 0.738 | 0.630 | 0.698 |

#### 설치 (팀원 필수)

```bash
pip install -r requirements.txt
ollama pull qwen3:14b
```
