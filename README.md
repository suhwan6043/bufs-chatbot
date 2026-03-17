# BUFS Academic Chatbot (캠챗)

부산외국어대학교 학사 안내 RAG 챗봇입니다.
로컬 LLM(Ollama)과 하이브리드 검색(Vector DB + Knowledge Graph)을 사용하여 학사 관련 질문에 답변합니다.

## 아키텍처

```
사용자 질문
    │
    ▼
QueryAnalyzer       → 의도(Intent) + 엔티티 추출, 학번 파싱
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
AnswerGenerator     → Ollama(exaone3.5) LLM으로 최종 답변 생성
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
| LLM | Ollama + exaone3.5:7.8b (로컬) |
| 임베딩 | BAAI/bge-m3 (multilingual, CPU) |
| 리랭킹 | BAAI/bge-reranker-v2-m3 (Cross-Encoder, CPU) |
| 벡터 DB | ChromaDB (SQLite 기반 로컬 파일) |
| 지식 그래프 | NetworkX (pkl 파일) |
| PDF 처리 | PyMuPDF + pdfplumber |
| 수업시간표 파싱 | timetable_parser (교시→시각 변환, 학과명 정규화) |
| UI | Streamlit (멀티페이지: 챗봇 + 로그 뷰어) |

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
# 학사안내 PDF 인제스트 (내국인 기준)
python scripts/ingest_pdf.py --pdf data/pdfs/학사안내.pdf --doc-type domestic --student-id 2023

# 수업시간표 PDF 인제스트
python scripts/ingest_pdf.py --pdf data/pdfs/수업시간표.pdf --doc-type timetable --semester 2026-1

# 디렉토리 일괄 인제스트
python scripts/ingest_pdf.py --dir data/pdfs/

# DB 현황 확인
python scripts/ingest_pdf.py --status

# 추출 결과 JSON 저장 (디버깅용)
python scripts/ingest_pdf.py --pdf data/pdfs/파일.pdf --save-json
```

`--doc-type` 선택지: `domestic` (내국인, 기본값) / `foreign` (외국인) / `transfer` (편입생) / `schedule` (학사일정) / `timetable` (수업시간표)

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
rm -rf data/chromadb/
python scripts/ingest_pdf.py --pdf data/pdfs/파일.pdf
```

## 실행

```bash
streamlit run main.py
```

| 페이지 | URL | 설명 |
|--------|-----|------|
| 챗봇 (메인) | `http://localhost:8501` | 학사 질문 답변 |
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
│   │   └── academic_graph.py   # NetworkX 지식 그래프
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
│   └── logs.py                 # 로그 뷰어 페이지
├── scripts/
│   ├── ingest_pdf.py           # PDF → ChromaDB 인제스트
│   ├── build_graph.py          # 지식 그래프 빌드
│   ├── pdf_to_graph.py         # PDF에서 학사 데이터 자동 파싱
│   ├── make_eval_dataset.py    # 평가용 JSONL 생성
│   ├── evaluate.py             # 자동 평가 + LLM-as-a-Judge
│   ├── qualitative_judge.py    # 정성적 Judge (0-5 척도)
│   ├── compare_eval.py         # 평가 결과 비교
│   └── make_report.py          # 평가 보고서 생성
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
| 별점 피드백 | 답변 하단 1~5점 평가, 로그에 함께 저장 |
| 대화 로그 | 날짜별 JSONL 저장, 웹 UI에서 CSV 다운로드 |
| 로딩 애니메이션 | 답변 생성 중 책 넘기는 애니메이션 표시 |
| 학번 인식 | 질문 내 학번 자동 파싱 → 맞춤 졸업요건 답변 |
| 도메인 용어 정규화 | 한국어 약어/별칭 → 정규 명칭 변환 |
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
| LLM 응답 | exaone3.5:7.8b (GPU 스트리밍) |
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
