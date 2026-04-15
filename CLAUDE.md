# BUFS Chatbot — 프로젝트 지침서

부산외대 학사 RAG 챗봇. 자세한 아키텍처·실행 방법은 `README.md` 참조.

## 4대 원칙 (위반 금지)

1. **유연한 스키마 진화** — 새 문서 유형/메타데이터가 추가될 때 기존 코드를 최소 수정으로 수용. 데이터에서 스키마가 자라게 둔다.
2. **비용·지연 최적화** — 불필요한 LLM 호출, 중복 임베딩, 과도한 재랭킹 후보, 매번 전체 재크롤 등을 줄인다. 동적 커뮤니티 선택·증분 업데이트를 우선.
3. **지식 생애주기 관리** — 추가·수정·삭제가 전체 재구축 없이 반영되도록 설계. 해시 기반 변경 감지 / 증분 인덱싱.
4. **하드코딩 절대 금지** — 모델명·경로·임계치는 `.env` 또는 `app/config.py`. 문서·코드에 절대값을 박지 않는다.

## 디렉터리 SSOT

| 위치 | 역할 |
|------|------|
| `app/pipeline/` | 검색·답변 파이프라인 (전처리 → 검색·병합 → 생성·검증) |
| `app/crawler/` | gnuboard5 공지 크롤러 + 변경감지 (`change_detector`, `notice_crawler`) |
| `app/graphdb/` | 학사 규정 그래프 (NetworkX, FAQ 역인덱스) |
| `app/vectordb/` | ChromaDB 래퍼 |
| `app/ingestion/` | PDF/공지 청킹·임베딩·증분 업데이트 |
| `app/scheduler/` | APScheduler 백그라운드 크롤링 잡 |
| `backend/` | FastAPI (chat, admin, transcript, source, health, feedback) |
| `frontend/` | Next.js (다국어 ko/en, `/admin` 페이지 포함) |
| `scripts/` | 인제스트·평가·빌드 (`ingest_all.py`가 마스터 진입점) |
| `data/crawl_meta/` | `content_hashes.json` (크롤 변경감지 상태) |
| `docs/archive/` | 시점성 스냅샷 (진단 리포트) — 일반 문서 아님 |

## 검색 우선순위

| 순위 | 소스 | doc_type |
|------|------|----------|
| 1 | 공식 PDF / 학생포털 | `domestic`, `guide` |
| 2 | 그래프 직접답변 / FAQ / 고정공지 | graph direct, `faq`, `notice`(📌) — RRF 동등 경쟁 |
| 3 | 일반 공지 / 장학 | `notice`(일반), `scholarship` |

## 작업 규칙

- 새 기능 전 `app/pipeline/`, `app/crawler/` 기존 함수 우선 재사용. 중복 구현 금지.
- 크롤러 변경 시 `data/crawl_meta/content_hashes.json` 호환성 확인 (source_id 형식 변경은 일회성 마이그레이션 비용 발생).
- 모델·임계치는 `app/config.py`의 `Settings` 클래스에 추가. 코드에 매직 넘버 금지.
- 평가는 `scripts/eval_*` 사용. 새 평가 도입 시 기존 리포트 양식과 키 호환.

## 장기 작업 인수인계

여러 세션에 걸친 긴 작업(문서 대량 수정, 대형 리팩터링, 평가 이터레이션 등)을 수행할 때:

**세션 시작 시** — 프로젝트 루트의 `progress.txt`가 존재하면 반드시 먼저 읽는다. 이전 세션이 남긴 진행 상황·미완료 항목·주의사항을 파악한 뒤 작업을 재개한다.

**세션 종료 시** — 작업이 완료되거나 중단될 때 `progress.txt`를 아래 형식으로 덮어쓴다:

```
날짜: YYYY-MM-DD
작업: <작업명>
완료: <이번 세션에서 끝낸 것>
미완료: <다음 세션에서 이어야 할 것>
주의: <다음 세션이 알아야 할 중요 컨텍스트>
```

작업이 완전히 끝났으면 `progress.txt`를 삭제하거나 `미완료: 없음`으로 표시한다.
