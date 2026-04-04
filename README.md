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
QueryAnalyzer       → 의도(Intent) + 엔티티 추출, 학번 파싱
    │  [EN 쿼리] FlashText(aliases_en→ko) 키워드 매핑
    │            키워드 미검출 시 → GENERAL + BGE-M3 시맨틱 fallback
    │
    ├──▶ ChromaDB (Vector)   → BGE-M3 임베딩 + 상위 15~20개 후보
    │        │                  (수업시간표: department 필터 적용)
    │        └──▶ Reranker   → BGE-Reranker-v2-m3으로 상위 5개 선택
    │
    └──▶ AcademicGraph (NetworkX) → 학사일정, 졸업요건, 학과 구조 탐색
                │
 ContextMerger ◀──┘  → Vector + Graph 결과 병합 (토큰 예산 관리)
    │
    ▼
AnswerGenerator     → Ollama(Qwen3:8B) LLM으로 최종 답변 생성
    │  [EN 쿼리] EN 전용 시스템 프롬프트 + 매칭 용어 주입
    │
    ▼
ResponseValidator   → 답변 품질 검증
    │
    ▼
ChatLogger          → 대화 로그 JSONL 저장 (data/logs/)
```

## 주요 기술 스택

| 구성요소 | 기술 |
|----------|------|
| LLM | Ollama + Qwen3:8B (로컬, think=False) |
| 임베딩 | BAAI/bge-m3 (multilingual, CPU) |
| 리랭킹 | BAAI/bge-reranker-v2-m3 (Cross-Encoder, CPU) |
| 벡터 DB | ChromaDB (SQLite 기반 로컬 파일) |
| 지식 그래프 | NetworkX (pkl 파일) |
| PDF 처리 | PyMuPDF + pdfplumber |
| 수업시간표 파싱 | timetable_parser (교시→시각 변환, 학과명 정규화) |
| EN 키워드 매핑 | FlashText (Aho-Corasick, aliases_en→ko, O(N)) |
| UI | Streamlit (멀티페이지: 챗봇 + 로그 뷰어) |

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

[Ollama](https://ollama.com)를 설치한 후 아래 명령으로 모델을 다운로드합니다.

```bash
ollama pull qwen3:8b
```

### 3. 환경 설정 (선택)

`.env` 파일을 생성하여 설정을 커스터마이즈할 수 있습니다:

```env
# LLM (Ollama OpenAI 호환 API)
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=qwen3:8b
LLM_MAX_TOKENS=2048
LLM_TEMPERATURE=0.1

# 임베딩
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu

# 리랭킹
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_ENABLED=true
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
├── scripts/
│   ├── ingest_all.py           # 전체 재학습 (PDF+그래프+정적페이지+고정공지)
│   ├── ingest_pdf.py           # PDF → ChromaDB 인제스트
│   ├── ingest_pinned_notices.py# 고정공지 → 벡터DB + 그래프DB
│   ├── build_graph.py          # 지식 그래프 빌드
│   ├── update_dept_grad_exam.py# 학과별 졸업인증 데이터 업데이트
│   ├── pdf_to_graph.py         # PDF에서 학사 데이터 자동 파싱
│   ├── evaluate.py             # 자동 평가 + LLM-as-a-Judge
│   └── eval_ragas.py           # RAGAS 기반 RAG 평가
├── data/
│   ├── pdfs/                   # 학사 안내 PDF 파일
│   ├── chromadb/               # ChromaDB 영구 저장소
│   ├── graphs/                 # academic_graph.pkl
│   ├── logs/                   # 대화 로그 (chat_YYYY-MM-DD.jsonl)
│   └── eval/                   # 평가 데이터셋/결과
├── tests/                      # 52개 테스트
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
cd "C:\Users\User\Desktop\챗봇"
.venv/Scripts/pytest tests/ -v  # 52개 테스트
```

## 성능 특성

| 항목 | 설명 |
|------|------|
| 임베딩 | BGE-M3 (1024차원, CPU ~2-5초/배치) |
| 리랭킹 | Cross-Encoder (후보 15-20개 → 상위 5개, CPU ~1-2초) |
| LLM 응답 | Qwen3.5-9B Q4_K_M (GPU 스트리밍, LM Studio) |
| 그래프 탐색 | NetworkX (즉시, <10ms) |

## 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_BASE_URL` | `http://localhost:11434` | Ollama 서버 주소 |
| `LLM_MODEL` | `qwen3:8b` | LLM 모델 (OpenAI 호환 API) |
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

### [2026-04-04 / 임성원] EN 파이프라인 개선 — FlashText 기반 다국어 쿼리 처리

#### 변경 배경

기존 `QueryAnalyzer`는 한국어 키워드만 인식하여, 영어 질문이 들어오면 모두 `Intent.GENERAL`로 분류되고 잘못된 컨텍스트가 검색되는 문제가 있었습니다.

#### 변경 내용

- `app/pipeline/query_analyzer.py` — `EnTermMapper` 싱글톤 추가
  - `config/academic_terms.yaml`의 `aliases_en` → `ko` 매핑을 서버 시작 시 메모리에 로드
  - FlashText (Aho-Corasick, O(N)) 로 영어 쿼리에서 한국어 학술 용어 추출
  - 추출된 KO 용어로 기존 Intent 분류기 재사용
  - 키워드 미검출 시 `Intent.GENERAL` + BGE-M3 시맨틱 검색으로 fallback
- `app/pipeline/answer_generator.py` — EN 전용 시스템 프롬프트(`EN_SYSTEM_PROMPT`) 추가
  - `lang="en"` 일 때 영어로 답변하도록 프롬프트 분기
  - 매칭된 학술 용어(`matched_terms`)를 프롬프트에 주입하여 정확한 영어 용어명 사용 유도
- `app/models.py` — `QueryAnalysis.matched_terms` 필드 추가 (`[{"ko": ..., "en": ...}]`)
- `requirements.txt` — `flashtext>=2.7`, `pyyaml>=6.0` 추가

#### 설치 (팀원 필수)

```bash
pip install -r requirements.txt
# 또는 개별 설치
pip install flashtext pyyaml
```

#### LLM 변경

LM Studio (Qwen3.5-9B) → Ollama (Qwen3:8B, `think=False`)
