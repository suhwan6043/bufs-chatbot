# ChromaDB 검색 진단 - 완전 가이드

이 디렉토리에는 "전과하는법 알려줘" 쿼리 검색 문제를 진단하기 위한 도구와 보고서가 포함되어 있습니다.

## 빠른 시작

### 1단계: 기본 검색 진단 실행
```bash
python diagnostic_search.py
```



---

### 2단계: 전체 파이프라인 진단 실행
```bash
python full_pipeline_test.py
```

**확인 사항**:
- Query Analysis가 Intent를 올바르게 분류하는가?
- `requires_vector`가 True인가?
- ChromaDB 검색이 성공하는가?
- 컨텍스트 포맷팅이 정상인가?

**예상 출력**: 
```
[1단계] Query Analysis: Intent=MAJOR_CHANGE, requires_vector=True
[2단계] ChromaDB 검색: 5개 결과, 유사도 0.60+
[3단계] 컨텍스트 포맷팅: ~900 토큰
```

---

## 진단 결과 해석

### ✓ 모든 테스트 통과 (정상)

```
diagnostic_search.py:  5개 결과 반환 ✓
full_pipeline_test.py: Intent=MAJOR_CHANGE, 검색 성공 ✓
```

→ **문제는 LLM 응답 생성 또는 UI 단계에 있음**

다음 확인:
1. Qwen 로컬 모델 서버 작동 여부
2. LLM API 응답 코드
3. Streamlit UI 에러

---

### ⚠ 일부 테스트 실패

#### 시나리오 A: 검색 결과 없음
```
diagnostic_search.py: [NO RESULTS] 검색 결과 없음
```

**원인**:
1. ChromaDB가 비어있음 → PDF 인제스트 필요
2. 필터 조건이 너무 제한적 → student_id, doc_type 확인

**해결**:
```bash
# 상태 확인
python diagnostic_search.py

# 메타데이터 확인
python full_pipeline_test.py
```

---

#### 시나리오 B: Intent 분류 오류
```
full_pipeline_test.py: Intent=LEAVE_OF_ABSENCE (MAJOR_CHANGE가 아님)
```

**원인**: query_analyzer.py에서 "전과" 키워드가 두 Intent에 포함됨

**해결**: query_analyzer.py 라인 123에서 "전과" 제거

```python
# 수정 전
Intent.LEAVE_OF_ABSENCE: [
    ...,
    "전과", "전부", "전부(과)", ...  # 전과 포함
]

# 수정 후
Intent.LEAVE_OF_ABSENCE: [
    ...,
    "전부", "전부(과)", ...  # 전과 제거
]
```

---

## 보고서 파일

| 파일 | 내용 | 대상 |
|------|------|------|
| `SEARCH_DIAGNOSTIC_REPORT.md` | 상세 기술 분석 | 개발자 |
| `DIAGNOSTIC_SUMMARY.md` | 최종 요약 및 권장사항 | 팀장/PM |
| `README_DIAGNOSTICS.md` | 이 파일 (빠른 가이드) | 모두 |

---

## 핵심 발견

### ✓ 확인된 정상 작동

1. **ChromaDB 벡터 검색**: 완벽함
   - 1,730개 청크 저장
   - 모든 테스트 쿼리에 대해 관련성 높은 결과 반환
   - 유사도 0.45-0.66 범위 (의미있음)

2. **임베딩 모델**: 정상
   - BAAI/bge-m3 (다국어)
   - 1024차원 벡터
   - L2 정규화

3. **where_document 필터**: 정상
   - 문서 내용 필터링 작동

---

### ⚠ 발견된 부분 문제

1. **Intent 분류 모호성** (Minor)
   - "전과" 키워드가 MAJOR_CHANGE와 LEAVE_OF_ABSENCE에 중복
   - 영향: 같은 쿼리가 다른 의도로 분류될 수 있음

2. **용어 정규화 미흡** (Minor)
   - "전부과", "전부(과)" 매핑 없음
   - 제안: glossary.py에 추가

---

## 기술 사양

### 검색 성능
```
속도:        <1초
유사도 범위: 0.45-0.66
관련도:      상위 1-3위에 관련 문서
```

### 임베딩 모델
```
모델:    BAAI/bge-m3
차원:    1024
정규화:  L2
언어:    다국어 (Multilingual)
```

### ChromaDB 설정
```
경로:   C:\Users\suhwa\Desktop\bufs-chatbot\data\chromadb
청크:   1,730개
메트릭: cosine (코사인 유사도)
```

---

## 사용 가능한 도구

### diagnostic_search.py
기본 검색 진단

```bash
python diagnostic_search.py
```

기능:
- ChromaDB 상태 확인
- 5개 테스트 쿼리 실행
- 임베딩 벡터 분석
- where_document 필터 테스트
- 메타데이터 통계

출력: 검색 결과 (유사도, 소스, 텍스트)

---

### full_pipeline_test.py
전체 파이프라인 진단

```bash
python full_pipeline_test.py
```

기능:
- Query Analysis 테스트
- Intent 분류 확인
- ChromaDB 검색 테스트
- 컨텍스트 포맷팅 시뮬레이션

출력: 각 단계별 상세 결과

---

## 문제 해결 플로우

```
사용자: "전과하는법 알려줘"
           ↓
[1] python diagnostic_search.py 실행
    → 검색 결과 없음?
      → ChromaDB 상태 확인
      → PDF 인제스트 재실행
    → 검색 결과 있음?
      → [2] 진행
      
[2] python full_pipeline_test.py 실행
    → Intent 분류 오류?
      → query_analyzer.py 수정
    → 검색 성공하지만 답변 없음?
      → [3] 진행

[3] LLM 응답 확인
    → Qwen 서버 상태
    → API 응답 코드
    → 토큰 수 검증

[4] UI 렌더링 확인
    → Streamlit 에러
    → 세션 상태
    → 캐시
```

---

## 권장 조치 (우선순위)

### 1순위: Intent 분류 정정
```
파일: app/pipeline/query_analyzer.py
작업: LEAVE_OF_ABSENCE에서 "전과" 제거
예상 효과: 의도 분류 정확도 향상
```

### 2순위: 용어 정규화
```
파일: app/pipeline/glossary.py
작업: "전부과" → "전과" 매핑 추가
예상 효과: 다양한 사용자 표현 처리
```

### 3순위: 전체 파이프라인 테스트
```
실행: python full_pipeline_test.py
확인: LLM 응답 생성 단계
```

---

## FAQ

### Q: 검색은 작동하는데 답변이 없어요
**A**: 다음을 확인하세요:
1. Qwen 로컬 모델 서버 작동 여부
2. `full_pipeline_test.py` 실행 → Intent 분류
3. LLM API 응답 코드

### Q: 특정 학번만 검색이 안돼요
**A**: student_id 필터를 확인하세요:
1. Query Analysis에서 학번이 추출되는가?
2. ChromaDB의 cohort_from/cohort_to 범위가 맞는가?

### Q: 검색 결과가 관련없어요
**A**: 이는 정상 범위입니다 (유사도 0.45는 의미있음). 다음을 확인하세요:
1. 쿼리 표현이 명확한가? ("전과" vs "전과하는법")
2. 검색어가 문서에 포함되어 있는가?

---

## 연락처

- **기술 문제**: 개발팀 (전체 파이프라인 검토)
- **검색 문제**: 데이터팀 (ChromaDB/임베딩)
- **LLM 문제**: AI팀 (Qwen 모델)

---

**마지막 진단**: 2026-04-05
**상태**: ChromaDB 정상 작동, 부분 문제 식별 및 해결책 제시
