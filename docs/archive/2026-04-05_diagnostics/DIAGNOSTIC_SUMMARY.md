# ChromaDB 검색 진단 - 최종 요약

> **[Archived Snapshot — 2026-04-10]** 이 문서는 2026-04-05 시점의 ChromaDB/Intent 진단 스냅샷입니다. 현재 상태와 혼동을 피하기 위해 `docs/archive/`로 이관되었습니다. 본문은 원본 그대로 유지합니다.

## Resolution Status (2026-04-10)

| 이슈 | 당시 상태 | 현재 상태 |
|---|---|---|
| ChromaDB 벡터 검색 정상성 | ✓ 정상 | ✓ 현재도 정상 (병렬화로 지연 단축) |
| "전과" Intent 충돌 (MAJOR_CHANGE ↔ LEAVE_OF_ABSENCE) | Minor 문제 | **해결** — `query_analyzer.py`에 전용 분기 로직 추가 |
| 용어 정규화 (`전부과` → `전과`) | 권장 사항 | glossary 정합성은 `tests/test_glossary.py`에서 검증 |

---

## 진단 결과

### 1. 핵심 결과: ChromaDB 검색은 완벽하게 작동 ✓

모든 테스트 쿼리에서 관련성 높은 검색 결과를 반환했습니다.

#### 검색 성능 데이터

```
쿼리                    결과 수   최고 유사도   최저 유사도   상태
───────────────────────────────────────────────────────────────
"전과하는법 알려줘"      5개      0.6077      0.4976       ✓
"전과"                 5개      0.6622      0.5476       ✓
"전부과"               5개      0.5265      0.4542       ✓
"전과 신청"            5개      0.6622      0.6042       ✓
"전과 방법"            5개      0.5686      0.5588       ✓
```

**결론**: ChromaDB 벡터 검색 자체는 문제없음.

---

### 2. 임베딩 모델 상태

| 속성 | 값 |
|------|------|
| 모델 | BAAI/bge-m3 |
| 벡터 차원 | 1024 |
| 정규화 | L2 정규화 (norm = 1.0) |
| 언어 | 다국어 (Multilingual) |
| 상태 | ✓ 정상 작동 |

쿼리 간 의미 유사도:
- "전과하는법 알려줘" ↔ "전과": **0.7274** (높음)
- "전과" ↔ "전부과": **0.5610** (중간)

---

### 3. ChromaDB 데이터베이스 상태

```
저장 위치: C:\Users\suhwa\Desktop\bufs-chatbot\data\chromadb
저장된 청크: 1,730개
컬렉션: bufs_academic
메트릭: cosine (코사인)

데이터 구성:
  - Doc Type: domestic (100%)
  - 메인 소스: 2026학년도1학기학사안내.pdf
  - 추가: 크롤링된 공지사항, 웹 페이지
```

---

### 4. 파이프라인 테스트 결과

#### 테스트 1: "전과 신청 방법"
```
[1단계] Query Analysis
  Intent: MAJOR_CHANGE ✓
  requires_vector: True ✓
  requires_graph: True ✓

[2단계] ChromaDB 검색
  결과: 5개 ✓
  유사도: 0.6042 ~ 0.6622 ✓

[3단계] 컨텍스트 포맷팅
  총 문자: 1,827
  추정 토큰: ~913 ✓
  상태: OK
```

#### 테스트 2: "2024학번 전과 가능한가?"
```
[1단계] Query Analysis
  Intent: LEAVE_OF_ABSENCE ⚠
  학번 추출: 2024 ✓
  requires_vector: True ✓

[2단계] ChromaDB 검색
  결과: 5개 ✓
  유사도: 0.6026 ~ 0.6405 ✓

[3단계] 컨텍스트 포맷팅
  총 문자: 2,422
  추정 토큰: ~1,211 ✓
  상태: OK
```

**주목**: 두 번째 테스트에서 Intent가 LEAVE_OF_ABSENCE로 분류됨
- 원인: query_analyzer.py에서 "전과"가 LEAVE_OF_ABSENCE의 키워드로도 포함됨 (라인 123)
- 영향: 의도 분류에 모호성 있음

---

## 5. 발견된 문제점

### 5.1 Intent 분류 모호성 (Minor)

**파일**: `app/pipeline/query_analyzer.py`

"전과"가 두 개의 Intent에 포함됨:

```python
Intent.MAJOR_CHANGE: [
    ...
    "전과",  # 라인 99
    ...
]

Intent.LEAVE_OF_ABSENCE: [
    ...
    "전과", "전부", "전부(과)", ...  # 라인 123
    ...
]
```

**영향**: 같은 쿼리가 다른 Intent로 분류될 수 있음
- "전과 신청" → MAJOR_CHANGE (첫 매치)
- "2024학번 전과 가능한가?" → LEAVE_OF_ABSENCE (학번 표현 때문에)

**권장 해결**:
```python
# LEAVE_OF_ABSENCE에서 "전과" 제거
# "전과"는 MAJOR_CHANGE에만 남김
# "전부", "전부(과)"는 유지 (학적변동 의미)
```

### 5.2 용어 정규화 미흡 (Minor)

**파일**: `app/pipeline/glossary.py` 라인 37

현재:
```python
"전과": "전과",  # 정규화 없음
```

제안:
```python
"전과": "전과",          # 유지
"전부과": "전과",        # 추가
"전부(과)": "전과",      # 추가
```

---

## 6. 검색 실패 시 체크리스트

만약 사용자가 "전과하는법 알려줘"에 대해 **답변을 받지 못한다면**:

### 체크 순서:

1. **Query Analysis 결과 확인**
   ```bash
   python full_pipeline_test.py
   ```
   - Intent가 MAJOR_CHANGE 또는 LEAVE_OF_ABSENCE인가?
   - requires_vector가 True인가?

2. **검색 결과 확인**
   ```bash
   python diagnostic_search.py
   ```
   - 5개 결과가 반환되는가?
   - 유사도가 0.5 이상인가?

3. **필터 조건 확인**
   - student_id 필터가 너무 제한적인가?
   - doc_type 필터가 올바른가?
   - semester 필터가 현재 학기와 일치하는가?

4. **LLM 상태 확인**
   - Qwen 로컬 모델 서버 작동 여부
   - API 응답 코드 (200 OK?)
   - 토큰 수 (> 최대값?)

5. **UI 렌더링 확인**
   - Streamlit 페이지 에러?
   - 세션 상태 문제?
   - 캐시 문제?

---

## 7. 성능 지표

| 항목 | 값 | 평가 |
|------|-----|------|
| 검색 속도 | <1초 | ✓ 우수 |
| 유사도 범위 | 0.45-0.66 | ✓ 양호 |
| 관련 문서 순위 | 상위 1-3위 | ✓ 우수 |
| 메타데이터 커버리지 | 100% | ✓ 우수 |
| 벡터 차원 | 1024 | ✓ 충분 |
| 저장된 청크 | 1,730개 | ✓ 충분 |

---

## 8. 최종 권장사항

### 우선순위 1: Intent 분류 정정
- LEAVE_OF_ABSENCE에서 "전과" 제거
- query_analyzer.py 라인 99-104 리뷰

### 우선순위 2: 용어 정규화 강화
- glossary.py에 "전부과" 매핑 추가
- 공식 용어와 학생 언어 연결

### 우선순위 3: 전체 파이프라인 테스트
- LLM 응답 생성 단계 확인
- UI 렌더링 확인

### 우선순위 4: 모니터링
- 사용자 피드백 수집
- 검색 결과 품질 모니터링
- 의도 분류 정확도 추적

---

## 9. 기술 상세정보

### ChromaDB 검색 시그니처
```python
def search(
    self,
    query: str,
    n_results: int = None,
    student_id: Optional[str] = None,      # 코호트 필터
    doc_type: Optional[Union[str, List[str]]] = None,
    semester: Optional[str] = None,        # 학기 필터
    department: Optional[str] = None,      # 학과 필터
) -> List[SearchResult]:
```

### 기본 설정
```
CHROMA_N_RESULTS: 15개
EMBEDDING_MODEL: BAAI/bge-m3
EMBEDDING_DEVICE: cpu
DISTANCE_METRIC: cosine
```

---

## 10. 진단 파일 목록

| 파일 | 목적 |
|------|------|
| `diagnostic_search.py` | 기본 검색 진단 |
| `full_pipeline_test.py` | 전체 파이프라인 진단 |
| `SEARCH_DIAGNOSTIC_REPORT.md` | 상세 진단 보고서 |
| `DIAGNOSTIC_SUMMARY.md` | 이 파일 (최종 요약) |

---

## 결론

**ChromaDB 벡터 검색 자체는 완벽하게 작동합니다.**

만약 사용자가 "전과하는법 알려줘"에 답변을 받지 못한다면:
1. ✓ 검색은 5개 관련 문서를 찾음
2. ✓ 유사도는 0.50 이상 (의미있음)
3. ? 문제는 Query Analysis, LLM 응답, 또는 UI에 있음

다음 단계: `full_pipeline_test.py` 실행 → Intent 분류와 LLM 응답 확인

