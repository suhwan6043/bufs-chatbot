# BUFS Academic Chatbot

부산외국어대학교 학사 안내 RAG 챗봇입니다.
로컬 LLM(Ollama)과 하이브리드 검색(Vector DB + Knowledge Graph)을 사용하여 학사 관련 질문에 답변합니다.

## 아키텍처

```
사용자 질문
    │
    ▼
QueryAnalyzer       → 의도(Intent) + 엔티티 추출, 학번 파싱
    │
    ├──▶ ChromaDB (Vector)   → BGE-M3 임베딩 + 상위 15개 후보
    │        │
    │        └──▶ Reranker   → BGE-Reranker-v2-m3으로 상위 5개 선택
    │
    └──▶ AcademicGraph (NetworkX) → 학사일정, 졸업요건, 학과 구조 탐색
                │
ContextMerger ◀──┘  → Vector + Graph 결과 병합 (토큰 예산 관리)
    │
    ▼
AnswerGenerator     → Ollama(exaone3.5) LLM으로 최종 답변 생성
    │
    ▼
ResponseValidator   → 답변 품질 검증
```

## 주요 기술 스택

| 구성요소 | 기술 |
|----------|------|
| LLM | Ollama + exaone3.5:7.8b (로컬) |
| 임베딩 | BAAI/bge-m3 (multilingual, CPU) |
| 리랭킹 | BAAI/bge-reranker-v2-m3 (Cross-Encoder, CPU) |
| 벡터 DB | ChromaDB (SQLite 기반 로컬 파일) |
| 지식 그래프 | NetworkX (pkl 파일) |
| PDF 처리 | PyMuPDF + pdfplumber |
| UI | Streamlit |

## 설치

### 요구사항

- Python 3.10+
- [Ollama](https://ollama.com) 설치 및 실행
- 약 4GB+ RAM (BGE-M3 모델 포함)

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. Ollama 모델 다운로드

```bash
ollama pull exaone3.5:7.8b
# 저사양 환경
ollama pull exaone3.5:2.4b
```

### 3. 환경 설정 (선택)

`.env` 파일을 생성하여 설정을 커스터마이즈할 수 있습니다:

```env
# LLM
OLLAMA_MODEL=exaone3.5:7.8b
OLLAMA_FALLBACK_MODEL=exaone3.5:2.4b
OLLAMA_NUM_CTX=2048

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

### PDF 인제스트 (Vector DB)

```bash
# PDF를 data/pdfs/ 폴더에 저장 후 실행
python scripts/ingest_pdf.py
```

### 지식 그래프 구축

```bash
# PDF에서 자동으로 학사 데이터 파싱하여 그래프 생성
python scripts/pdf_to_graph.py --pdf data/pdfs/2025학년도2학기학사안내.pdf

# 실제 변경 없이 파싱 결과 확인
python scripts/pdf_to_graph.py --pdf data/pdfs/2025학년도2학기학사안내.pdf --dry-run
```

### 임베딩 모델 변경 후 재인제스트

BGE-M3로 모델을 변경한 경우 기존 ChromaDB 데이터를 삭제 후 재인제스트해야 합니다:

```bash
# 기존 ChromaDB 데이터 삭제
rm -rf data/chromadb/

# 재인제스트
python scripts/ingest_pdf.py
```

## 실행

```bash
streamlit run main.py
```

브라우저에서 `http://localhost:8501` 접속

## 프로젝트 구조

```
챗봇/
├── app/
│   ├── config.py           # 환경 변수 및 설정 관리
│   ├── models.py           # Pydantic 데이터 모델
│   ├── embedding/
│   │   └── embedder.py     # BGE-M3 임베딩 래퍼
│   ├── pipeline/
│   │   ├── query_analyzer.py   # 의도 분류 + 엔티티 추출
│   │   ├── query_router.py     # Vector/Graph 라우팅
│   │   ├── reranker.py         # BGE-Reranker-v2-m3 리랭킹
│   │   ├── context_merger.py   # 검색 결과 병합
│   │   ├── answer_generator.py # LLM 답변 생성
│   │   └── response_validator.py
│   ├── vectordb/
│   │   └── chroma_store.py # ChromaDB CRUD
│   ├── graphdb/
│   │   └── academic_graph.py   # NetworkX 지식 그래프
│   ├── pdf/
│   │   └── pdf_processor.py    # PDF 파싱 + 청킹
│   └── ui/
│       └── chat_app.py     # Streamlit 채팅 UI
├── scripts/
│   ├── ingest_pdf.py       # PDF → ChromaDB 인제스트
│   ├── build_graph.py      # 지식 그래프 빌드
│   ├── pdf_to_graph.py     # PDF에서 학사 데이터 자동 파싱
│   ├── make_eval_dataset.py# 평가용 JSONL 생성
│   └── evaluate.py         # 자동 평가 + LLM-as-a-Judge
├── data/
│   ├── pdfs/               # 학사 안내 PDF 파일
│   ├── chromadb/           # ChromaDB 영구 저장소
│   ├── graphs/             # academic_graph.pkl
│   └── eval/               # 평가 데이터셋/결과
├── tests/
├── requirements.txt
└── main.py
```

## 평가

### 평가 실행

```bash
# 2026학년도 1학기 데이터셋 평가
python scripts/evaluate.py --dataset data/eval/rag_eval_dataset_2026_1.jsonl

# 빠른 확인용 (Judge, Reranker 비활성화)
python scripts/evaluate.py --dataset data/eval/rag_eval_dataset_2026_1.jsonl --no-judge --no-rerank
```

주요 산출물:

- `data/eval/rag_eval_dataset_2026_1.jsonl`: 2026학년도 1학기 평가셋
- `data/eval/eval_results_20260308_002116.json`: 50문항 자동 평가 결과
- `eval_results_20260308_2026_1_judge.json`: 기존 응답에 Judge 점수를 후처리한 요약 결과

### 최신 평가 요약 (2026-03-08, 2026학년도 1학기 50문항)

| 지표 | 값 |
|------|----|
| Hit Rate | 100.0% |
| Contains GT | 54.0% |
| LLM Judge Correctness | 81.6% (`n=49`) |
| LLM Judge Relevance | 4.58 / 5 |
| LLM Judge Faithfulness | 4.04 / 5 |
| 평균 검색 시간 | 12.2초 |
| 평균 생성 시간 | 2.6초 |
| 평균 전체 응답 시간 | 14.8초 |
| 답변 정상률 / 출처 표기율 | 100.0% / 100.0% |

난이도별 Judge Correctness:

- `easy`: 90.3%
- `medium`: 73.3%
- `hard`: 33.3%

해석:

- 쉬운 단일 사실 질의는 안정적이지만, 학번별 규정 비교나 복수 조건을 묻는 질문에서 성능이 크게 떨어집니다.
- `contains_gt`가 낮은 이유는 날짜/시간 표현 차이도 있지만, 실제로 정답 근거가 있는데도 `확인되지 않는 정보입니다`로 응답한 케이스가 포함되어 있기 때문입니다.
- Judge 기준 오답 9건은 `q001`, `q011`, `q018`, `q019`, `q020`, `q031`, `q038`, `q045`, `q050`입니다.

### 오답 9건 기준 수정 우선순위

| 우선순위 | 대상 오답 | 문제 유형 | 수정 방향 |
|----------|-----------|-----------|-----------|
| P0 | `q018`, `q019`, `q020`, `q031`, `q038`, `q050` | PDF 근거가 있는데도 그래프/검색 커버리지 부족으로 `확인되지 않는 정보` 응답 | `scripts/pdf_to_graph.py`에서 GPA 예외학점, 장바구니 최대학점, 전공 신청/변경 기간, OCU 납부기간, 학번별 복수전공 이수학점을 구조화해 그래프에 넣고, `query_router`에서 해당 유형은 그래프 결과를 우선 사용 |
| P1 | `q001`, `q011` | 일정 질문에서 이벤트 의미를 잘못 고름 | `개강`과 `수업시작일`을 분리하고, `언제까지` 질문은 중간 설명보다 최종 마감 시점을 우선 답하도록 일정 답변 템플릿 보정 |
| P1 | `q045`, `q050` | 졸업요건 조합형 질문에서 수치 조합/비교를 LLM이 추론하다가 오류 발생 | 졸업요건/제2전공 질의는 생성 전에 규칙 기반 formatter로 학번별 값을 조합하고, LLM은 문장화만 담당 |

우선순위 판단 근거:

- 오답 9건 중 6건은 "정보가 없다고 잘못 판단"한 커버리지 문제입니다.
- 나머지 3건 중 2건은 일정 의미 구분 실패, 1건은 졸업요건 수치 조합 오류입니다.
- 현재 `확인되지 않는 정보입니다`가 포함된 응답 5건은 모두 Judge 기준 오답이므로, 이 문구는 안전장치보다 파싱/검색 누락 신호로 보는 것이 맞습니다.

## 성능 특성

| 항목 | 설명 |
|------|------|
| 임베딩 | BGE-M3 (1024차원, CPU ~2-5초/배치) |
| 리랭킹 | Cross-Encoder (후보 15개 → 상위 5개, CPU ~1-2초) |
| LLM 응답 | exaone3.5:7.8b (CPU ~20-60초) |
| 그래프 탐색 | NetworkX (즉시, <10ms) |

## 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 서버 주소 |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | 메인 LLM 모델 |
| `OLLAMA_FALLBACK_MODEL` | `exaone3.5:2.4b` | 폴백 모델 |
| `OLLAMA_NUM_CTX` | `2048` | 컨텍스트 길이 |
| `OLLAMA_TEMPERATURE` | `0.1` | 생성 온도 |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 임베딩 모델 |
| `EMBEDDING_DEVICE` | `cpu` | 임베딩 디바이스 |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | 리랭커 모델 |
| `RERANKER_DEVICE` | `cpu` | 리랭커 디바이스 |
| `RERANKER_ENABLED` | `true` | 리랭킹 활성화 |
| `RERANKER_TOP_K` | `5` | 리랭킹 후 선택 수 |
| `RERANKER_CANDIDATE_K` | `15` | 리랭킹 전 후보 수 |
| `CHROMA_N_RESULTS` | `15` | ChromaDB 검색 결과 수 |
| `CHROMA_COLLECTION` | `bufs_academic` | 컬렉션 이름 |
