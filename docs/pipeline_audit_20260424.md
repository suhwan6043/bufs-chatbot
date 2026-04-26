# 파이프라인 전체 감사 — 2026-04-24

**대상 브랜치**: `feat/en-support-optimization` @ glossary 7 추가 반영 상태
**목적**: 내부 테스트 후 구조적 개선 로드맵 수립

---

## 스테이지별 결함 요약

### 1. 인덱싱 (가장 심각)

| # | 심각도 | 결함 | 위치 | 영향 |
|---|--------|------|------|------|
| 1.1 | 🔴 심각 | **청크 ID 생성 시 text[:50] 해시 충돌** | [chunking.py:83-86](app/ingestion/chunking.py#L83-L86), [ingest_pdf.py:191-193](scripts/ingest_pdf.py#L191-L193) | 선행 청크 덮어쓰기, 데이터 암묵 손실 |
| 1.2 | 🟡 중 | 청크 길이 상한 미적용(text sliding window) | [chunking.py:38](app/ingestion/chunking.py#L38) | 과도하게 긴 청크 → 임베딩 품질 저하 |
| 1.3 | 🟡 중 | detect_cohort() regex 미흡 — "2024학번 이후"/"입학자" 패턴 미감지 | [chunking.py:45-80](app/ingestion/chunking.py#L45-L80) | cohort=(2016,2030) 폴백 → 학번 필터 오작동 |
| 1.4 | 🟢 경 | ChromaDB 메타데이터 flat 직렬화 손실 | [chroma_store.py:54-65](app/vectordb/chroma_store.py#L54-L65) | section_path 같은 중첩 구조 유실 |
| 1.5 | 🟢 경 | 테이블 1500자 절단 → 후반 학번 학점 누락 | [chunking.py:188-193](app/ingestion/chunking.py#L188-L193) | 다조건 표 질의 불완전 |

### 2. 그래프 빌딩

| # | 심각도 | 결함 | 위치 | 영향 |
|---|--------|------|------|------|
| 2.1 | 🟡 중 | `_is_redirect_answer()` 초반 120자만 스캔 → 본문 수치 놓침 | [academic_graph.py:62-90](app/graphdb/academic_graph.py#L62-L90) | 정답 FAQ가 redirect로 강등 |
| 2.2 | 🟢 경 | FAQ 역인덱스 캐시 reload 시 미리셋 | academic_graph.py:299 부근 | 오래된 가중치 지속 |

### 3. 크롤링·변경감지

| # | 심각도 | 결함 | 영향 |
|---|--------|------|------|
| 3.1 | 🟡 중 | **크롤 실패 시 재시도 없음** — 빈 결과 → 전체 DELETED 오판 | 일시적 네트워크 오류로 공지 전부 손실 가능 |
| 3.2 | 🟡 중 | 첨부파일 URL 유효성 미검증 | 죽은 링크 → ingest 재시도 반복 |
| 3.3 | 🟢 경 | content_hashes.json 무한 누적 | 조회 성능 O(N) 악화 |

### 4. 검색 (Retrieval)

| # | 심각도 | 결함 | 위치 | 영향 |
|---|--------|------|------|------|
| 4.1 | 🔴 심각 | **department 필터 실제로는 무작동** — 인덱스에 메타 미주입 | [chroma_store.py:198-200](app/vectordb/chroma_store.py#L198-L200) + 청킹 단계 | COURSE_INFO에서 타 학과 시간표 혼입 |
| 4.2 | 🟡 중 | cohort 범위 필터 오버펫치 — 공통 청크가 학번 특화 청크 밀어냄 | [chroma_store.py:183-189](app/vectordb/chroma_store.py#L183-L189) | 2024 학생 쿼리에서 2022 규정 상위 노출 |
| 4.3 | 🟢 경 | EN→KO 크로스링구얼 임베딩 거리 큼 | query_router.py | EN 쿼리에서 관련 KO 청크 누락 |

### 5. 리랭킹

| # | 심각도 | 결함 | 위치 | 영향 |
|---|--------|------|------|------|
| 5.1 | 🟢 경 | `_dedup_near_similar()` 주석 처리 — 회귀 사례 불명확 | [reranker.py:157-161](app/pipeline/reranker.py#L157-L161) | 중복 청크 상위 포함 가능 |
| 5.2 | 🟢 경 | asks_url boost 감지 로직 불투명 | reranker.py:130-152 | URL 질의에서 boost 변동성 |

### 6. 컨텍스트 병합

| # | 심각도 | 결함 | 위치 | 영향 |
|---|--------|------|------|------|
| 6.1 | 🟡 중 | **adaptive_cutoff가 direct_answer/transcript 부스트 청크 기준으로 왜곡** | [context_merger.py:119-159](app/pipeline/context_merger.py#L119-L159) | 중요 벡터 청크가 cutoff로 버려짐 |
| 6.2 | 🟢 경 | intent별 컨텍스트 예산 정량 검증 부족 | context_merger.py:85-96 | GRADUATION_REQ 1600~1800 예산 효과 불명 |

### 7. 답변 생성

| # | 심각도 | 결함 | 위치 | 영향 |
|---|--------|------|------|------|
| 7.1 | 🟡 중 | term_guide_section 실제 주입 여부 테스트 없음 | answer_generator.py:45-72 | EN 용어 일관성 미검증 |
| 7.2 | 🟢 경 | max_tokens 자동 조정이 .env 상한 초과 가능 | answer_generator.py:207-250 | 지연 증가 |

### 8. 검증

| # | 심각도 | 결함 | 영향 |
|---|--------|------|------|
| 8.1 | 🟢 경 | NO_CONTEXT_PHRASES 커버리지 부족 — 변형("확인할 수 없", "알 수 없") 누락 | 거절 응답 패턴 미스매치로 불필요 경고 |
| 8.2 | 🟢 경 | 숫자 환각 검사가 학점만 엄격, 날짜·학번 미검사 | SCHEDULE intent 환각 우회 |

---

## 엔드-투-엔드 연쇄 사슬 (다중 스테이지 원인)

### 사슬 A: 학번 감지 실패 → 검색 오류 → 답변 혼입

```
인덱싱: detect_cohort() "2024학번 이후" 미감지 → cohort=(2016,2030) 저장
    ↓
검색: 2024 학번 쿼리 → $lte/$gte 필터 통과하는 모든 청크 반환
    ↓
컨텍스트: 2022·2023·2024 규정 섞여 포함
    ↓
답변: LLM이 어느 학번인지 불확실 → "2024 기준 130학점이지만 2022는 120" 혼동 답
```

### 사슬 B: EN 쿼리 → 크로스링구얼 거리 → 답변 누락

```
검색: "When is graduation deadline?" → 임베더가 다국어 처리해도 KO "졸업기한" 벡터 거리 큼
    ↓
리랭킹: Cross-Encoder도 KO 맥락 약해 낮은 점수
    ↓
컨텍스트: 무관 청크 상위 → 노이즈
    ↓
답변: "해당 정보를 찾을 수 없습니다" 거절 (실제 데이터는 있음)
```

### 사슬 C: 크롤 실패 → 증분 삭제 → 영구 손실

```
크롤링: 네트워크 일시 오류 → 빈 attachments 반환
    ↓
변경감지: ChangeEvent(MODIFIED) 발생 (내용 동일하지만 해시 계산 차이)
    ↓
증분 업데이트: _delete_attachment_chunks() 기존 청크 제거
    ↓
영구 손실: 새 청크 없음 + 기존 청크 삭제 → 다음 크롤까지 해당 공지 비어있음
```

---

## 우선순위 로드맵 (Top 10)

| # | 심각도 | 난이도 | 항목 | 영향 영역 | 예상 시간 |
|---|--------|--------|------|-----------|-----------|
| 1 | 🔴 | 중 | 청크 ID 충돌 해결 (text[:50]→ page+suffix 조합) | 전체 인덱스 | 2h |
| 2 | 🔴 | 경 | department 메타 주입 복원 (시간표 청킹) | COURSE_INFO | 1.5h |
| 3 | 🟡 | 중 | 크롤 실패 재시도 + 빈 결과 가드 | 크롤링 안정성 | 1.5h |
| 4 | 🟡 | 중 | detect_cohort regex 보강 | 학번별 필터링 | 1.5h |
| 5 | 🟡 | 경 | 청크 길이 hard cap 적용 (sliding window 후) | 임베딩 품질 | 1h |
| 6 | 🟡 | 중 | adaptive_cutoff 부스트 청크 제외 로직 | 컨텍스트 품질 | 1h |
| 7 | 🟢 | 경 | 테이블 절단 한도 상향 (1500→3000) or 행단위 분할 | 다조건 표 질의 | 1.5h |
| 8 | 🟢 | 경 | EN term_guide 주입 검증 테스트 | 용어 일관성 | 0.5h |
| 9 | 🟢 | 경 | 거절 패턴 regex 통합 | 검증 정확도 | 0.5h |
| 10 | 🟢 | 경 | 첨부 URL HTTP 재시도 | 크롤 복원력 | 1h |

**총 예상 시간**: ~12시간 (집중 1~2일)

---

## 즉시 조치 vs 테스트 후 조치

### 내일 테스트 전 (오늘 밤)

❌ **건드리지 않음** — 모두 eval 재실행 + 검증 필요한 변경이라 내일 테스트에 리스크. 현 상태(glossary 7개만 반영)로 테스트 진행.

### 내일 테스트 후 (우선순위 순)

**Day 1 (테스트 다음날)**:
- #1 청크 ID 충돌 해결 → 재인덱싱 → eval 재측정
- #2 department 메타 주입

**Day 2**:
- #3 크롤 재시도 안전성
- #4 detect_cohort regex
- #5 청크 길이 hard cap

**Day 3+**:
- #6~#10 나머지 개선

---

## 의견 — 가장 큰 레버리지 3개

1. **청크 ID 충돌 + dedupe 전체 재색인** → 현재 측정되지 않는 "손실된 청크"가 많을 수 있음. 재색인 후 F1 상승 가능성
2. **department 필터 복원** → COURSE_INFO 카테고리 정확도 즉시 개선 (시간표 질의)
3. **학번 감지 regex 강화** → GRADUATION_REQ 카테고리 혼동 감소 → 사용자 핵심 시나리오 품질 상승

나머지는 점진적 개선. 위 3개만 먼저 해도 **F1 +3~5pp** 기대 가능.

---

## 참고 — 작업에서 제외한 이유

- **Fix B (clarification)**: 이미 구현 완료, 내일 테스트 후 별도 활성화
- **Fix A (student_groups 필터)**: 롤백됨. cohort 개선(#4)과 함께 다시 설계 필요
- **EN 크로스링구얼**: 중장기 과제. BM25 하이브리드 검색 추가 검토 (별도 PR)
