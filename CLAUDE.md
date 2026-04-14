# BUFS Chatbot — 프로젝트 지침서

## 네 가지 핵심 원칙

다음의 세 가지 원칙을 모든 설계·구현에 걸쳐 고수한다.

### 1. 유연한 스키마 진화
데이터로부터 스키마가 스스로 진화하게 만드는 유연한 아키텍처를 지향할 것.
새로운 문서 유형이나 메타데이터가 추가될 때 기존 코드를 최소한으로 수정하면서 수용할 수 있도록 설계한다.

### 2. 비용·지연 최적화
인덱싱과 검색 과정에서 발생하는 비용과 지연 시간을 통제하기 위해 동적 커뮤니티 선택과 같은 최적화 기법을 적극 도입할 것.
불필요한 LLM 호출, 중복 임베딩, 과도한 재랭킹 후보를 줄이는 방향으로 구현한다.

### 3. 지식 생애주기 관리
지식의 생애주기를 고려하여 증분 업데이트와 버전 관리 체계를 구축함으로써 시스템의 지속 가능성을 확보할 것.
문서의 추가·수정·삭제가 전체 재구축 없이도 반영될 수 있는 구조를 유지한다.

### 4. 하드코딩 절대 금지
---

## 검색 우선순위 체계

| 순위 | 소스 | doc_type | 설명 |
|------|------|----------|------|
| 1 | Tier 1 | `domestic`, `guide` | 학교 공식 PDF, 학생포털 정적 페이지 |
| 2 | 그래프 / FAQ / 고정공지 | graph direct_answer, `faq`, `notice`(📌) | 그래프 직접 답변 & FAQ & 고정공지 (동등, RRF 점수 경쟁) |
| 3 | 기타 | `notice`(일반), `scholarship` 등 | 일반 공지사항, 장학 안내 등 |

---

## 주요 컴포넌트

- `app/graphdb/academic_graph.py` — 학사 규정 그래프 DB
- `app/pipeline/` — 검색·답변 파이프라인
  - 전처리: `language_detector` · `query_analyzer` · `glossary` · `ko_tokenizer` · `translator`
  - 검색·병합: `query_router` · `community_selector` · `reranker` · `context_merger`
  - 생성·검증: `answer_generator` · `response_validator`
- `app/ui/chat_app.py` — Streamlit UI
- `data/pdfs/` — 원본 PDF 문서
- `scripts/ingest_all.py` — 전체 재인제스트 스크립트 (세부는 `README.md` 참조)
- `docs/archive/` — 시점성 스냅샷 문서 보관 (진단 리포트 등)
