# Step 6 — Import 그래프 (ast 기반, pydeps 대체)

**측정**: 204 project modules, 405 internal import edges, **0 cycles (Tarjan SCC)**
**도구**: `scripts/audit_import_graph.py` (ast.ImportFrom + ast.Import → DOT)
**SVG**: graphviz `dot` 미설치 → DOT 파일만 (`import_graph_full.dot`, `import_graph_top.dot`). 그래프 시각화는 `dot -Tsvg import_graph_top.dot -o import_graph.svg` 명령으로 추후 생성.

## 1. Top-level 패키지 의존 (상위 2 레벨 압축)

| 패키지 | 의존하는 패키지 (out-edges) | 개수 |
|---|---|---:|
| `app.crawler` | `app.config` | 1 |
| `app.embedding` | `app.config` | 1 |
| `app.graphdb` | `app.config`, `app.crawler`, `app.models`, `app.pipeline` | 4 |
| `app.ingestion` | `app.config`, `app.crawler`, `app.graphdb`, `app.models`, `app.pdf`, `app.vectordb` | 6 |
| `app.logging` | `app.config` | 1 |
| `app.pdf` | `app.config`, `app.models` | 2 |
| `app.pipeline` | `app.config`, `app.contacts`, `app.embedding`, `app.graphdb`, `app.models`, `app.pipeline`, `app.transcript`, `app.ui`, `app.vectordb` | 9 |
| `app.scheduler` | `app.config`, `app.crawler`, `app.ingestion`, `app.scheduler` | 4 |
| `app.transcript` | `app.config`, `app.graphdb`, `app.models`, `app.transcript` | 4 |
| `app.ui` | `app.config`, `app.contacts`, `app.crawler`, `app.embedding`, `app.graphdb`, `app.ingestion`, `app.logging`, `app.models`, `app.pdf`, `app.pipeline`, `app.scheduler`, `app.transcript`, `app.ui`, `app.vectordb` | 14 |
| `app.vectordb` | `app.config`, `app.embedding`, `app.models` | 3 |
| `backend.routers` | `app.config`, `app.contacts`, `app.crawler`, `app.embedding`, `app.graphdb`, `app.ingestion`, `app.logging`, `app.models`, `app.pdf`, `app.pipeline`, `app.scheduler`, `app.transcript`, `app.vectordb`, `backend`, `backend.routers`, `backend.schemas` | 16 |
| `backend.schemas` | (없음) | 0 |
| `pages` | `app.config`, `app.contacts`, `app.crawler`, `app.graphdb`, `app.ingestion`, `app.logging`, `app.models`, `app.pdf`, `app.pipeline`, `app.transcript`, `backend.routers` | 11 |
| `scripts` | `app.*`, `backend.*` 다수 | ≥10 |

## 2. 핵심 패키지 in-edges (의존하는 쪽)

| 패키지 | 의존받는 쪽 | 의미 |
|---|---|---|
| `app.config` | 거의 모든 패키지 | settings SSOT — **변경 시 영향 광범위** |
| `app.models` | crawler, graphdb, ingestion, pdf, pipeline, transcript, vectordb, backend, pages | Intent / Entity dataclass — **변경 시 호환성 검토 필수** |
| `app.pipeline` | graphdb, ui, backend, pages, scripts | 핵심 RAG — 다층 의존 |
| `app.graphdb` | ingestion, pipeline, transcript, ui, backend, pages, scripts | 학사 그래프 — 인제스트·검색 양쪽 인입 |
| `app.embedding` | pipeline, ui, vectordb, scripts | BGE-M3 — 검색·인제스트 공용 |
| `app.vectordb` | ingestion, pipeline, ui, backend, scripts | ChromaDB |
| `app.contacts` | pipeline, ui, backend, pages | 부서 검색 |

## 3. 사이클 (cycles.txt)

**0 cycles 발견** ✓ — Tarjan SCC 적용 결과 모든 컴포넌트가 acyclic.

multi-task 1에서 의심됐던 `query_understanding → query_analyzer ↔ ?` 패턴도 없음. (Step 1에서도 동일 결과)

## 4. 의존 hotspot (in-edges Top 5)

1. `app.config` — 거의 모든 모듈이 settings import. 변경 시 단일 reload 영향.
2. `app.models` — Intent enum / dataclass. multi-task 1 (Intent 18 카테고리)이 다층 호환성 영향.
3. `app.pipeline` — RAG 본체. backend/pages/ui/scripts 모두 import.
4. `app.graphdb` — ingestion / pipeline / transcript / backend 양쪽 호출.
5. `app.embedding` — pipeline 검색 + ingestion 인제스트 공용.

## 5. backend ↔ app 경계

```
backend.routers.chat → app.pipeline (analyzer/router/merger/generator/validator)
backend.routers.admin → app.* (다양)
backend.dependencies → app.shared_resources + app.pipeline
backend.schemas → (자기 완결)
```

**관찰**: `backend.schemas`가 in-edge 0, out-edge 0 — 의존성 완전 격리. 좋은 패턴.

## 6. PR 후보 (Step 7 인풋)

| 작업 | ROI | 우선순위 |
|---|---|---|
| `app.ui` (Streamlit) 의존 14패키지 — 운영 시 사용 안 하면 cold path 검증 + import lazy화 | 가벼움 (lazy 이미 적용) | P3 |
| `app.config` 변경 시 영향 패키지 자동 grep 도구 | 운영 변경 안전성 | P2 |
| `app.models` Intent enum 변경 → multi-task 1 호환 매트릭스 검증 (PR 별도) | multi-task 1 종료 후 | P1 |

## 7. Step 6 산출물 검증 (import_graph 부분)

| 항목 | 상태 |
|---|---|
| `import_graph.svg` (pydeps) | ✗ pydeps + graphviz 미설치 → `.dot` 텍스트로 대체 |
| `import_graph_top.dot` | ✓ 211줄 |
| `import_graph_full.dot` | ✓ 408줄 |
| `cycles.txt` | ✓ 0 cycles (clean) |
| `import_graph.md` (텍스트 요약, 이 문서) | ✓ |
| `module_globals.json` | ✓ 283 모듈 글로벌 |
