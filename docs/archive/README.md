# docs/archive — 시점성 문서 보관소

프로젝트의 **Canon(`CLAUDE.md`)** 과 **Living Doc(`README.md`)** 을 제외한, 특정 시점의 진단·테스트 리포트 등 "스냅샷" 문서는 여기로 이관한다. 루트를 깨끗하게 유지하고, 과거 지식을 버리지 않기 위함이다 (CLAUDE.md 원칙 3 — 지식 생애주기 관리).

## 디렉터리 규칙

```
docs/archive/<YYYY-MM-DD>_<topic>/
```

- `<YYYY-MM-DD>`: 문서 생성 시점 (또는 진단 수행일)
- `<topic>`: `diagnostics`, `pipeline_test`, `ragas_eval` 등 간결한 주제어

## 이관 원칙

1. **삭제하지 않는다.** 시점성 문서는 현재와 혼동되지 않게 격리만 한다.
2. **Resolution Status 블록**을 상단에 한 번 추가해, 당시 제기된 이슈가 현재 어떻게 되었는지 한눈에 알 수 있게 한다 (해결/부분 해결/미해결).
3. **본문은 리라이트하지 않는다.** 원본은 역사 기록이다.

## 현재 보관 문서

| 경로 | 내용 |
|---|---|
| `2026-04-05_diagnostics/BUFS_PIPELINE_TEST_SUMMARY.md` | 파이프라인 20문항 테스트 (15% 성공률 → 이후 상당수 해결) |
| `2026-04-05_diagnostics/DIAGNOSTIC_SUMMARY.md` | ChromaDB/Intent 분류 진단 요약 |
| `2026-04-05_diagnostics/SEARCH_DIAGNOSTIC_REPORT.md` | "전과" 쿼리 상세 진단 보고서 |
| `2026-04-05_diagnostics/README_DIAGNOSTICS.md` | 당시 진단 도구 사용 가이드 |
