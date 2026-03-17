"""
학사 관계 그래프 (NetworkX 기반)
JSX 스키마 기준: 9 노드 타입 × 14 엣지 타입 × 5 특수 분기 규칙
"""

import logging
import pickle
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

import networkx as nx

from app.config import settings
from app.models import SearchResult

logger = logging.getLogger(__name__)


# ── 학번 그룹 매핑 ──────────────────────────────────────────
# 졸업요건/수강규칙은 입학년도 그룹별로 상이
# 그룹: 2016_before / 2017_2020 / 2021 / 2022 / 2023 / 2024_2025

def get_student_group(student_id: str) -> str:
    """학번(4자리 연도)을 졸업요건 그룹 키로 변환합니다."""
    try:
        year = int(student_id)
    except (ValueError, TypeError):
        return "2023"  # 기본값

    if year >= 2024:
        return "2024_2025"
    elif year == 2023:
        return "2023"
    elif year == 2022:
        return "2022"
    elif year == 2021:
        return "2021"
    elif year >= 2017:
        return "2017_2020"
    else:
        return "2016_before"


def get_reg_group(student_id: str) -> str:
    """학번을 수강신청규칙 그룹으로 변환합니다."""
    group = get_student_group(student_id)
    return "2023이후" if group in ("2023", "2024_2025") else "2022이전"


GROUP_LABELS = {
    "2024_2025": "2024학번 이후",
    "2023": "2023학번",
    "2022": "2022학번",
    "2021": "2021학번",
    "2017_2020": "2017~2020학번",
    "2016_before": "2016학번 이전",
}


class AcademicGraph:
    """
    [역할] BUFS 학사 관계 데이터를 그래프로 저장/검색
    [스키마] 9 노드 타입 × 14 엣지 타입 (JSX 스키마 기준)
    [핵심] 학번 그룹 분기 + 내국인/외국인/편입생 분기
    [저장] pickle 파일로 영구 저장
    """

    NODE_TYPES = [
        "학과전공", "교과목", "교양영역", "졸업요건",
        "전공이수방법", "수강신청규칙", "학사일정",
        "마이크로전공", "교직",
        "조기졸업",       # 조기졸업 신청자격·졸업기준·기타사항
    ]

    EDGE_TYPES = [
        "개설한다",       # 학과전공 → 교과목
        "소속된다",       # 교과목 → 교양영역
        "요구한다",       # 졸업요건 → 교양영역 / 교직 → 교과목
        "포함한다",       # 졸업요건 → 전공이수방법
        "적용된다",       # 전공이수방법 → 학과전공 / 수강신청규칙 → 교과목
        "연결된다",       # 학과전공 → 마이크로전공
        "제약한다",       # 수강신청규칙 → 졸업요건
        "기간정한다",     # 학사일정 → 수강신청규칙 / 학사일정 → 조기졸업
        "설치된다",       # 교직 → 학과전공
        "대체과목",       # 교과목 → 교과목
        "동일과목",       # 교과목 → 교과목
        "구성된다",       # 마이크로전공 → 교과목
        "교양전공상호인정", # 교과목 → 학과전공
        "신청자격적용",   # 조기졸업(신청자격) → 조기졸업(기준)
        "졸업기준적용",   # 조기졸업(기준) → 졸업요건
    ]

    def __init__(self, graph_path: str = None):
        self.path = graph_path or settings.graph.graph_path
        self.G = self._load_or_create()

    def _load_or_create(self) -> nx.DiGraph:
        path = Path(self.path)
        if path.exists():
            with open(path, "rb") as f:
                graph = pickle.load(f)
            logger.info(
                f"그래프 로드: {graph.number_of_nodes()}노드 / "
                f"{graph.number_of_edges()}엣지"
            )
            return graph
        logger.info("새 그래프 생성")
        return nx.DiGraph()

    def save(self) -> None:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self.G, f)
        logger.info(f"그래프 저장: {self.path}")

    # ── 노드 추가 메서드 ──────────────────────────────────────

    @staticmethod
    def _merge(base: dict, extra: dict) -> dict:
        """base 속성에 extra를 병합합니다. extra가 우선합니다."""
        result = dict(base)
        result.update(extra)
        return result

    def add_department(self, name: str, data: dict) -> str:
        """학과/전공 노드. ID = dept_{name}"""
        node_id = f"dept_{name}"
        attrs = self._merge({"type": "학과전공", "전공명": name}, data)
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_course(self, course_number: str, data: dict) -> str:
        """교과목 노드. ID = course_{course_number}"""
        node_id = f"course_{course_number}"
        attrs = self._merge({"type": "교과목", "과목번호": course_number}, data)
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_liberal_arts_area(self, area_name: str, data: dict) -> str:
        """교양영역 노드. ID = liberal_{area_name}"""
        node_id = f"liberal_{area_name}"
        attrs = self._merge({"type": "교양영역", "영역명": area_name}, data)
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_graduation_req(
        self, student_id: str, student_type: str, data: dict
    ) -> str:
        """
        졸업요건 노드. student_id는 4자리 연도 또는 그룹 키 허용.
        내부적으로 그룹 키로 정규화하여 저장.
        student_type: '내국인' | '외국인' | '편입생'
        """
        group = (
            get_student_group(student_id)
            if len(student_id) == 4 and student_id.isdigit()
            else student_id
        )
        node_id = f"grad_{group}_{student_type}"
        attrs = self._merge(
            {"type": "졸업요건", "적용학번그룹": group, "학생유형": student_type},
            data,
        )
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_major_method(
        self, method_type: str, student_id_range: str, data: dict
    ) -> str:
        """전공이수방법 노드. method_type: '방법1'|'방법2'|'방법3'"""
        node_id = f"method_{method_type}_{student_id_range}"
        attrs = self._merge(
            {"type": "전공이수방법", "방법유형": method_type, "적용학번범위": student_id_range},
            data,
        )
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_registration_rule(self, reg_group: str, data: dict) -> str:
        """수강신청규칙 노드. reg_group: '2023이후'|'2022이전'"""
        node_id = f"reg_{reg_group}"
        attrs = self._merge({"type": "수강신청규칙", "적용학번그룹": reg_group}, data)
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_schedule(
        self, event_name: str, semester: str, data: dict
    ) -> str:
        """학사일정 노드. ID = schedule_{event_name}_{semester}"""
        node_id = f"schedule_{event_name}_{semester}"
        attrs = self._merge(
            {"type": "학사일정", "이벤트명": event_name, "학기": semester},
            data,
        )
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_micro_major(self, name: str, data: dict) -> str:
        """마이크로/융합전공 노드."""
        node_id = f"micro_{name}"
        attrs = self._merge({"type": "마이크로전공", "전공명": name}, data)
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_teacher_training(self, department: str, data: dict) -> str:
        """교직과정 노드."""
        node_id = f"teacher_{department}"
        attrs = self._merge({"type": "교직", "설치학과": department}, data)
        self.G.add_node(node_id, **attrs)
        return node_id

    def add_early_graduation(self, node_key: str, data: dict) -> str:
        """
        조기졸업 노드.
        node_key 예시: '신청자격' | '기준_2022이전' | '기준_2023이후' | '기타사항'
        ID = early_grad_{node_key}
        """
        node_id = f"early_grad_{node_key}"
        attrs = self._merge({"type": "조기졸업", "구분": node_key}, data)
        self.G.add_node(node_id, **attrs)
        return node_id

    # ── 엣지 추가 ─────────────────────────────────────────────

    def add_relation(
        self, source: str, target: str, relation: str, data: dict = None
    ) -> None:
        """두 노드 간 관계(엣지) 추가."""
        edge_data = {"relation": relation}
        if data:
            edge_data.update(data)
        self.G.add_edge(source, target, **edge_data)

    # ── 조회 메서드 ───────────────────────────────────────────

    def get_graduation_req(
        self, student_id: str, student_type: str = "내국인"
    ) -> Optional[dict]:
        """학번 + 학생유형 기반 졸업요건 조회."""
        group = (
            get_student_group(student_id)
            if len(student_id) == 4 and student_id.isdigit()
            else student_id
        )
        node_id = f"grad_{group}_{student_type}"
        if node_id in self.G.nodes:
            return dict(self.G.nodes[node_id])
        return None

    def get_major_methods(self, student_id: str) -> List[dict]:
        """학번에 해당하는 전공이수방법 3가지(방법1/2/3) 반환."""
        group = get_student_group(student_id)
        results = []
        for node_id, data in self.G.nodes(data=True):
            if (
                data.get("type") == "전공이수방법"
                and data.get("적용학번범위") == group
            ):
                results.append({"id": node_id, **data})
        results.sort(key=lambda x: x.get("방법유형", ""))
        return results

    def get_registration_rule(self, student_id: str) -> Optional[dict]:
        """학번에 해당하는 수강신청규칙 반환."""
        reg_group = get_reg_group(student_id)
        node_id = f"reg_{reg_group}"
        if node_id in self.G.nodes:
            return dict(self.G.nodes[node_id])
        # 폴백: 전체 탐색
        for nid, data in self.G.nodes(data=True):
            if data.get("type") == "수강신청규칙":
                return dict(self.G.nodes[nid])
        return None

    def get_schedules(self, semester: str = None) -> List[dict]:
        """학사일정 반환. semester 미지정 시 전체."""
        results = []
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "학사일정":
                if semester is None or data.get("학기") == semester:
                    results.append({"id": node_id, **data})
        return results

    def get_alternatives(self, course_name: str) -> List[dict]:
        """대체과목/동일과목 체인 탐색 (1~2홉)."""
        node = self._find_course_by_name(course_name)
        if not node:
            return []
        alts = []
        for _, target, data in self.G.edges(node, data=True):
            if data.get("relation") in ("대체과목", "동일과목"):
                alts.append(dict(self.G.nodes[target]))
        return alts

    def get_department_info(self, dept_name: str) -> Optional[dict]:
        """학과 정보. 완전 매칭 후 부분 매칭 시도."""
        node_id = f"dept_{dept_name}"
        if node_id in self.G.nodes:
            return dict(self.G.nodes[node_id])
        for nid, data in self.G.nodes(data=True):
            if data.get("type") == "학과전공" and dept_name in data.get("전공명", ""):
                return dict(self.G.nodes[nid])
        return None

    def get_liberal_arts_areas(self, area_type: str = None) -> List[dict]:
        """교양영역 목록. area_type 부분 매칭 지원 ('인성체험교양' → '인성체험')."""
        results = []
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "교양영역":
                if area_type is None:
                    results.append({"id": node_id, **data})
                else:
                    area_val = data.get("영역구분", "")
                    if area_val in area_type or area_type in area_val:
                        results.append({"id": node_id, **data})
        return results

    def get_micro_majors(self) -> List[dict]:
        """마이크로/융합전공 목록."""
        return [
            {"id": nid, **data}
            for nid, data in self.G.nodes(data=True)
            if data.get("type") == "마이크로전공"
        ]

    def search_by_type(self, node_type: str) -> List[dict]:
        """특정 타입의 모든 노드 반환."""
        return [
            {"id": nid, **data}
            for nid, data in self.G.nodes(data=True)
            if data.get("type") == node_type
        ]

    def _find_course_by_name(self, course_name: str) -> Optional[str]:
        """과목명(부분 매칭 포함)으로 노드 ID 탐색."""
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "교과목":
                if (
                    data.get("과목명") == course_name
                    or course_name in data.get("과목명", "")
                ):
                    return node_id
        return None

    # ── 파이프라인 통합 ───────────────────────────────────────

    def query_to_search_results(
        self,
        student_id: str,
        intent: str,
        entities: dict = None,
        student_type: str = "내국인",
        question: str = "",
    ) -> List[SearchResult]:
        """
        의도 + 엔티티에 따라 그래프를 탐색하고 SearchResult 리스트로 반환.
        student_id 없는 intent(SCHEDULE, ALTERNATIVE)도 처리.
        """
        entities = entities or {}
        results = []

        if intent == "GRADUATION_REQ":
            results.extend(
                self._query_graduation(student_id, student_type, entities, question)
            )

        elif intent == "REGISTRATION":
            results.extend(self._query_registration(student_id, entities, question))

        elif intent == "SCHEDULE":
            results.extend(self._query_schedule(question, entities))

        elif intent == "COURSE_INFO":
            results.extend(
                self._query_course_info(
                    entities.get("course_name", ""),
                    entities.get("department", ""),
                )
            )

        elif intent == "MAJOR_CHANGE":
            if student_id:
                results.extend(self._query_major_methods(student_id))

        elif intent == "EARLY_GRADUATION":
            results.extend(self._query_early_graduation(student_id, question))

        elif intent == "ALTERNATIVE":
            results.extend(
                self._query_alternatives(entities.get("course_name", ""))
            )

        # ── 보충 탐색: 교양영역 / 마이크로전공 / 교직 ──
        if entities.get("liberal_arts_area"):
            results.extend(self._query_liberal_arts(entities["liberal_arts_area"]))

        if "마이크로전공" in question or "융합전공" in question:
            results.extend(self._query_micro_majors())

        if "교직" in question:
            results.extend(
                self._query_teacher_training(entities.get("department", ""))
            )

        return results

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[\s\-\.,:()\[\]/~·]", "", text or "").lower()

    @staticmethod
    def _format_date(date_str: str) -> str:
        if not date_str:
            return ""
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
        if not match:
            return date_str
        year, month, day = match.groups()
        return f"{int(year)}년 {int(month)}월 {int(day)}일"

    def _format_period(self, start: str, end: str) -> str:
        if not start:
            return ""
        if not end or start == end:
            return self._format_date(start)
        if len(end) >= 10 and end[4] == "-" and end[7] == "-":
            return f"{self._format_date(start)}부터 {int(end[5:7])}월 {int(end[8:10])}일까지"
        return f"{self._format_date(start)}부터 {end}까지"

    @staticmethod
    def _make_direct_result(
        context_text: str,
        answer_text: str,
        score: float = 1.2,
    ) -> SearchResult:
        return SearchResult(
            text=context_text,
            score=score,
            source="graph",
            metadata={"direct_answer": answer_text},
        )

    def _schedule_to_result(
        self,
        schedule: dict,
        answer_text: str = "",
        score: float = 1.1,
    ) -> SearchResult:
        start = schedule.get("시작일", "")
        end = schedule.get("종료일", "")
        period = start if start == end else f"{start}~{end}"
        line = f"[학사일정]\n- {schedule.get('이벤트명', '')}: {period}"
        if schedule.get("비고"):
            line += f"\n- 비고: {schedule['비고']}"
        metadata = {"direct_answer": answer_text} if answer_text else {}
        return SearchResult(text=line, score=score, source="graph", metadata=metadata)

    def _find_schedule_matches(self, question: str) -> List[dict]:
        schedules = self.get_schedules()
        if not question or not schedules:
            return []

        question_norm = self._normalize_text(question)
        trigger_map = [
            (
                lambda q: "조기졸업" in q and any(
                    kw in q for kw in ("기간", "언제", "신청", "일정", "마감")
                ),
                ["조기졸업신청"],
            ),
            (lambda q: "수강신청취소" in q or ("취소" in q and "까지" in q), ["수업일수1/4선"]),
            (lambda q: "수업시작일" in q or "수업시작" in q, ["수업시작일"]),
            (lambda q: "ocu" in q and "개강" in q, ["ocu개강일"]),
            (lambda q: "개강" in q and "수업시작" not in q and "ocu" not in q, ["개강"]),
            (lambda q: "장바구니" in q and ("기간" in q or "언제" in q), ["장바구니"]),
            (lambda q: "수강신청확인" in q or "수강정정" in q, ["수강신청확인"]),
            (lambda q: "중간고사" in q, ["중간고사"]),
            (lambda q: "기말고사" in q, ["기말고사"]),
            (lambda q: "제1·2전공" in q or "제1,2전공" in q or "변경(전과)" in q or "전과" in q,
             ["제12전공신청및변경전과"]),
            (lambda q: "ocu" in q and "납부" in q, ["ocusystem사용료납부기간", "ocu시스템사용료납부기간"]),
            (lambda q: "야간" in q or any(f"{i}교시" in q for i in range(10, 15)), ["야간수업시간표"]),
            (lambda q: "수강신청" in q and ("기간" in q or "언제" in q)
             and "정정" not in q and "확인" not in q and "취소" not in q
             and "장바구니" not in q, ["수강신청"]),
        ]

        matched: List[dict] = []
        for predicate, keywords in trigger_map:
            if not predicate(question_norm):
                continue
            for schedule in schedules:
                event_norm = self._normalize_text(schedule.get("이벤트명", ""))
                if any(keyword in event_norm for keyword in keywords):
                    matched.append(schedule)
            if matched:
                matched.sort(key=lambda m: len(m.get("이벤트명", "")))
                return matched

        for schedule in schedules:
            event_norm = self._normalize_text(schedule.get("이벤트명", ""))
            if event_norm and (event_norm in question_norm or question_norm in event_norm):
                matched.append(schedule)

        # 짧은 이름(더 정확한 매칭)을 우선
        matched.sort(key=lambda m: len(m.get("이벤트명", "")))
        return matched

    def _find_major_method(
        self, student_id: str, method_type: str
    ) -> Optional[dict]:
        for method in self.get_major_methods(student_id):
            if method.get("방법유형") == method_type:
                return method
        return None

    @staticmethod
    def _group_label(group: str) -> str:
        return GROUP_LABELS.get(group, f"{group}학번")

    def _query_graduation(
        self,
        student_id: str,
        student_type: str,
        entities: dict = None,
        question: str = "",
    ) -> List[SearchResult]:
        entities = entities or {}
        results = []

        student_groups = entities.get("student_groups") or []
        if (
            entities.get("second_major_credits")
            and len(student_groups) > 1
            and "복수전공" in question
        ):
            items = []
            lines = ["[복수전공 이수학점 비교]"]
            for group in student_groups:
                grad = self.get_graduation_req(group, "내국인")
                credits = grad.get("복수전공이수학점") if grad else None
                if credits is None:
                    continue
                label = self._group_label(group)
                lines.append(f"- {label}: {credits}학점")
                items.append(f"{label} {credits}학점")

            if items:
                answer = f"복수전공 이수학점은 {', '.join(items)}입니다."
                results.append(
                    self._make_direct_result("\n".join(lines), answer, score=1.3)
                )
                return results

        if entities.get("major_method"):
            method = self._find_major_method(student_id, entities["major_method"])
            if method and "복수전공" in question:
                main_credits = method.get("주전공학점")
                second_credits = method.get("제2전공학점")
                if main_credits and second_credits:
                    answer = (
                        f"{self._group_label(get_student_group(student_id))}이 "
                        f"{entities['major_method']}(주전공+복수전공)으로 졸업할 경우 "
                        f"주전공 {main_credits}학점, 복수전공 {second_credits}학점을 "
                        f"이수해야 합니다."
                    )
                    context = "\n".join(
                        [
                            f"[전공이수방법] {entities['major_method']} ({get_student_group(student_id)})",
                            f"- 주전공학점: {main_credits}",
                            f"- 복수전공학점: {second_credits}",
                        ]
                    )
                    results.append(
                        self._make_direct_result(context, answer, score=1.3)
                    )
                    return results

        if student_type == "외국인" and entities.get("graduation_cert") == "TOPIK":
            data = self.get_graduation_req(student_id, "외국인")
            topik = data.get("졸업인증") if data else None
            if topik:
                answer = (
                    f"외국인 학생의 졸업인증 TOPIK 기준은 {topik} 이상입니다."
                )
                results.append(
                    self._make_direct_result(
                        f"[졸업인증]\n- 외국인 TOPIK 기준: {topik}",
                        answer,
                        score=1.25,
                    )
                )
                return results

        # 요청 학생유형 우선, 없으면 내국인 (중복 방지)
        seen_types: set = set()
        for stype in (student_type, "내국인", "외국인", "편입생"):
            if stype in seen_types:
                continue
            seen_types.add(stype)
            data = self.get_graduation_req(student_id, stype)
            if data:
                text = self._fmt_graduation(student_id, stype, data)
                score = 1.0 if stype == student_type else 0.8
                results.append(SearchResult(text=text, score=score, source="graph"))
        # 전공이수방법 추가
        for m in self.get_major_methods(student_id):
            text = self._fmt_major_method(m)
            results.append(SearchResult(text=text, score=0.95, source="graph"))
        return results

    def _query_registration(
        self, student_id: str, entities: dict = None, question: str = ""
    ) -> List[SearchResult]:
        entities = entities or {}
        rule = self.get_registration_rule(student_id or "2023")
        if not rule:
            return []

        # 재수강 제한 전용 핸들러 (focused context)
        if "재수강" in question and any(kw in question for kw in ("제한", "한도", "최대")):
            retake_limit = rule.get("재수강제한", "")
            if retake_limit:
                context = f"[재수강 제한 규정]\n- {retake_limit}"
                return [self._make_direct_result(context, "", score=1.3)]

        # 학점이월 전용 핸들러 (19학점 혼동 방지)
        if "학점이월" in question:
            lines = ["[학점이월제]"]
            answer_parts = []
            for reg_grp in ("2022이전", "2023이후"):
                node_id = f"reg_{reg_grp}"
                if node_id not in self.G.nodes:
                    continue
                node = dict(self.G.nodes[node_id])
                carryover = node.get("학점이월여부", "")
                carryover_max = node.get("학점이월최대학점")
                carryover_cond = node.get("학점이월조건", "")
                label = "2023학번 이후" if reg_grp == "2023이후" else "2022학번 이전"
                lines.append(f"- {label}: {carryover}")
                if carryover_max:
                    lines.append(f"  이월 가능 최대학점: {carryover_max}학점")
                    answer_parts.append(
                        f"{label} 학번에만 적용되며, 최대 {carryover_max}학점까지 이월 가능합니다."
                    )
                elif "불가" in carryover or "폐지" in carryover:
                    answer_parts.append(f"{label}부터는 폐지되었습니다.")
                if carryover_cond:
                    lines.append(f"  조건: {carryover_cond}")
            answer = " ".join(answer_parts) if answer_parts else ""
            return [self._make_direct_result("\n".join(lines), answer, score=1.3)]

        if entities.get("gpa_exception"):
            limit = rule.get("평점4이상최대학점")
            if limit is not None:
                reg_group = rule.get("적용학번그룹", get_reg_group(student_id or "2023"))
                label = "2023학번 이후" if reg_group == "2023이후" else "2022학번 이전"
                answer = (
                    f"직전학기 평점 4.0 이상인 {label} 학생은 최대 "
                    f"{limit}학점까지 신청할 수 있습니다."
                )
                context = "\n".join(
                    [
                        f"[수강신청규칙] {reg_group}",
                        f"- 직전학기 평점 4.0 이상 최대학점: {limit}",
                    ]
                )
                return [self._make_direct_result(context, answer, score=1.3)]

        if entities.get("basket_limit"):
            basket_limit = rule.get("장바구니최대학점")
            if basket_limit is not None:
                answer = (
                    f"장바구니에 담을 수 있는 최대 학점은 {basket_limit}학점입니다."
                )
                context = f"[수강신청규칙]\n- 장바구니최대학점: {basket_limit}"
                return [self._make_direct_result(context, answer, score=1.3)]

        if entities.get("registration_deadline"):
            deadline = rule.get("수강취소마감일시")
            if deadline:
                parts = deadline.split(maxsplit=1)
                date_part = parts[0]
                time_part = parts[1] if len(parts) > 1 else ""
                if time_part:
                    answer = (
                        f"수강신청 취소는 {self._format_date(date_part)} "
                        f"{time_part[:2]}시까지 가능합니다."
                    )
                else:
                    answer = (
                        f"수강신청 취소는 {self._format_date(date_part)}까지 가능합니다."
                    )
                context = f"[수강신청 취소]\n- 수강취소마감일시: {deadline}"
                return [self._make_direct_result(context, answer, score=1.3)]

            matches = self._find_schedule_matches(question)
            if matches:
                schedule = matches[0]
                answer = (
                    f"수강신청 취소는 {self._format_date(schedule.get('시작일', ''))}까지 "
                    f"가능합니다."
                )
                return [self._schedule_to_result(schedule, answer, score=1.25)]

        if entities.get("ocu") and entities.get("payment_period"):
            start = rule.get("납부시작")
            end = rule.get("납부종료")
            if start and end:
                answer = (
                    f"OCU 시스템 사용료 납부기간은 {self._format_period(start, end)}입니다."
                )
                context = f"[OCU 납부기간]\n- 납부기간: {start}~{end}"
                return [self._make_direct_result(context, answer, score=1.3)]

        # 기간/일정 질문 → 학사일정에서 검색 (장바구니 기간, 수강신청 기간 등)
        # 절차/방법 질문은 제외 (e.g., "정정기간 이후 어떻게 처리되는가")
        _PROCESS_KW = ("어떻게", "무엇", "방법", "절차", "처리", "전에", "가능한")
        if (
            entities.get("question_focus") == "period"
            and not any(kw in question for kw in _PROCESS_KW)
        ):
            matches = self._find_schedule_matches(question)
            if matches:
                first = matches[0]
                event_name = first.get("이벤트명", "")
                period_text = self._format_period(
                    first.get("시작일", ""), first.get("종료일", "")
                )
                answer = f"{event_name} 기간은 {period_text}입니다."
                # 비고가 '수강 가능' 시간 안내이면 포함 (운영 시간표는 제외)
                bigo = first.get("비고", "")
                if bigo and ("수강 가능" in bigo or "부터" in bigo):
                    answer += f" {bigo}."

                return [self._schedule_to_result(first, answer, score=1.3)]

        return [SearchResult(
            text=self._fmt_registration_rule(student_id or "2023", rule),
            score=1.0,
            source="graph",
        )]

    def _query_schedule(
        self, question: str = "", entities: dict = None
    ) -> List[SearchResult]:
        entities = entities or {}
        matches = self._find_schedule_matches(question)
        if matches:
            results = []
            first = matches[0]

            # Reference-type node (no dates, e.g., 야간수업시간표)
            if not first.get("시작일"):
                skip_keys = {"id", "type", "이벤트명", "학기", "시작일", "종료일", "비고"}
                lines = [f"[{first.get('이벤트명', '')}]"]
                for k, v in first.items():
                    if k not in skip_keys and v:
                        lines.append(f"- {k}: {v}")
                return [SearchResult(
                    text="\n".join(lines), score=1.3, source="graph",
                )]

            answer = (
                f"{first.get('이벤트명', '')} 기간은 "
                f"{self._format_period(first.get('시작일', ''), first.get('종료일', ''))}입니다."
            )

            question_norm = self._normalize_text(question)
            if "ocu" in question_norm and "개강" in question_norm:
                answer = f"OCU 개강일은 {self._format_date(first.get('시작일', ''))}입니다."
                if first.get("비고"):
                    answer += f" {first['비고']}."

            elif "개강" in question_norm and "수업시작" not in question_norm:
                answer = (
                    f"개강일은 {self._format_date(first.get('시작일', ''))}입니다."
                )
            elif "수업시작" in question_norm:
                answer = (
                    f"수업시작일은 {self._format_date(first.get('시작일', ''))}입니다."
                )
            elif "전과" in question_norm or "제1·2전공" in question or "제1,2전공" in question_norm:
                answer = (
                    f"제1·2전공 신청 및 변경(전과) 기간은 "
                    f"{self._format_period(first.get('시작일', ''), first.get('종료일', ''))}입니다."
                )
            elif "ocu" in question_norm and "납부" in question_norm:
                answer = (
                    f"OCU 시스템 사용료 납부기간은 "
                    f"{self._format_period(first.get('시작일', ''), first.get('종료일', ''))}입니다."
                )

            results.append(self._schedule_to_result(first, answer, score=1.3))
            for extra in matches[1:3]:
                results.append(self._schedule_to_result(extra, score=1.0))
            return results

        schedules = self.get_schedules()
        if not schedules:
            return []
        results: List[SearchResult] = []

        # ① 날짜 있는 일반 일정 → 하나의 SearchResult로 병합
        standard = [s for s in schedules if s.get("시작일")]
        if standard:
            lines = ["[학사일정]"]
            for s in sorted(standard, key=lambda x: x.get("시작일", "")):
                start  = s.get("시작일", "")
                end    = s.get("종료일", "")
                period = start if start == end else f"{start}~{end}"
                line   = f"- {s.get('이벤트명', '')}: {period} ({s.get('학기', '')})"
                if s.get("시작시간"):           # OCU개강 등 시작시간 필드
                    line += f" {s['시작시간']}부터"
                if s.get("비고"):
                    line += f"  ※{s['비고']}"
                lines.append(line)
            results.append(SearchResult(text="\n".join(lines), score=1.0, source="graph"))

        # ② 시작일 없는 참조 노드(야간수업교시표 등) → 각각 별도 SearchResult
        skip_keys = {"id", "type", "이벤트명", "학기"}
        for s in schedules:
            if s.get("시작일"):
                continue
            lines = [f"[{s.get('이벤트명', '')}]"]
            for k, v in s.items():
                if k not in skip_keys:
                    lines.append(f"- {k}: {v}")
            results.append(SearchResult(text="\n".join(lines), score=0.95, source="graph"))

        return results

    def _query_course_info(
        self, course_name: str, dept: str
    ) -> List[SearchResult]:
        results = []
        if course_name:
            nid = self._find_course_by_name(course_name)
            if nid:
                results.append(SearchResult(
                    text=self._fmt_course(dict(self.G.nodes[nid])),
                    score=1.0,
                    source="graph",
                ))
        if dept:
            dept_data = self.get_department_info(dept)
            if dept_data:
                results.append(SearchResult(
                    text=self._fmt_department(dept_data),
                    score=0.9,
                    source="graph",
                ))
            # 개설한다 엣지 탐색: 학과 → 교과목
            dept_node = self._find_dept_node(dept)
            if dept_node:
                courses = [
                    dict(self.G.nodes[target])
                    for _, target, edata in self.G.edges(dept_node, data=True)
                    if edata.get("relation") == "개설한다"
                ]
                if courses:
                    lines = [f"[{dept} 개설 교과목]"]
                    for c in courses[:10]:
                        lines.append(
                            f"- {c.get('과목번호', '')} {c.get('과목명', '')} "
                            f"({c.get('학점', '')}학점)"
                        )
                    results.append(SearchResult(
                        text="\n".join(lines), score=0.95, source="graph",
                    ))
        return results

    def _find_dept_node(self, dept_name: str) -> Optional[str]:
        """학과명으로 노드 ID 탐색 (완전 → 부분 매칭)."""
        node_id = f"dept_{dept_name}"
        if node_id in self.G.nodes:
            return node_id
        for nid, data in self.G.nodes(data=True):
            if data.get("type") == "학과전공" and dept_name in data.get("전공명", ""):
                return nid
        return None

    def _query_liberal_arts(self, area_type: str) -> List[SearchResult]:
        """교양영역 노드 탐색."""
        areas = self.get_liberal_arts_areas(area_type)
        if not areas:
            return []
        lines = [f"[교양영역] {area_type}"]
        for a in areas:
            name = a.get("영역명", "")
            lines.append(f"- {name}")
            # 요구한다 엣지: 교양영역 → 교과목
            for _, target, edata in self.G.edges(a.get("id", ""), data=True):
                if edata.get("relation") == "요구한다":
                    course = self.G.nodes.get(target, {})
                    lines.append(
                        f"  · {course.get('과목명', '')} ({course.get('학점', '')}학점)"
                    )
        return [SearchResult(text="\n".join(lines), score=1.0, source="graph")]

    def _query_micro_majors(self) -> List[SearchResult]:
        """마이크로/융합전공 노드 + 구성된다 엣지(교과목) 탐색."""
        micros = self.get_micro_majors()
        if not micros:
            return []
        lines = ["[마이크로/융합전공]"]
        for m in micros:
            lines.append(f"- {m.get('전공명', '')}")
            # 구성된다 엣지: 마이크로전공 → 교과목
            for _, target, edata in self.G.edges(m.get("id", ""), data=True):
                if edata.get("relation") == "구성된다":
                    course = self.G.nodes.get(target, {})
                    lines.append(
                        f"  · {course.get('과목명', '')} ({course.get('학점', '')}학점)"
                    )
        return [SearchResult(text="\n".join(lines), score=1.0, source="graph")]

    def _query_teacher_training(self, dept: str = "") -> List[SearchResult]:
        """교직과정 노드 탐색."""
        results = []
        for nid, data in self.G.nodes(data=True):
            if data.get("type") != "교직":
                continue
            if dept and dept not in data.get("설치학과", ""):
                continue
            lines = [f"[교직과정] {data.get('설치학과', '')}"]
            skip_keys = {"type", "설치학과", "id"}
            for k, v in data.items():
                if k not in skip_keys and v:
                    lines.append(f"- {k}: {v}")
            results.append(SearchResult(
                text="\n".join(lines), score=1.0, source="graph",
            ))
        return results

    def _query_major_methods(self, student_id: str) -> List[SearchResult]:
        return [
            SearchResult(
                text=self._fmt_major_method(m),
                score=1.0,
                source="graph",
            )
            for m in self.get_major_methods(student_id)
        ]

    def _query_early_graduation(
        self, student_id: str, question: str = ""
    ) -> List[SearchResult]:
        """
        조기졸업 관련 그래프 탐색.
        - 신청기간 질문 → 학사일정 우선
        - 학번 있으면 해당 학번 기준학점 우선, 없으면 전 그룹 반환
        """
        results: List[SearchResult] = []
        question_norm = self._normalize_text(question)
        is_period_q = any(
            kw in question_norm
            for kw in ("기간", "언제", "일정", "마감", "신청")
        )

        # ① 신청기간 (학사일정 노드 활용)
        if is_period_q:
            matches = self._find_schedule_matches(question or "조기졸업신청기간언제")
            if not matches:
                # trigger_map 미적중 시 직접 탐색
                for nid, data in self.G.nodes(data=True):
                    if data.get("type") == "학사일정" and "조기졸업" in data.get("이벤트명", ""):
                        matches.append({"id": nid, **data})
            if matches:
                first = matches[0]
                period = self._format_period(
                    first.get("시작일", ""), first.get("종료일", "")
                )
                method = first.get("신청방법", "")
                answer = f"조기졸업 신청기간은 {period}입니다."
                if method:
                    answer += f" 신청방법: {method}"
                results.append(self._schedule_to_result(first, answer, score=1.3))

        # ② 신청자격
        if "early_grad_신청자격" in self.G.nodes:
            elig = dict(self.G.nodes["early_grad_신청자격"])
            results.append(SearchResult(
                text=self._fmt_early_graduation_eligibility(elig),
                score=1.15,
                source="graph",
            ))

        # ③ 학번별 졸업기준
        if student_id:
            try:
                grad_group = "2022이전" if int(student_id) <= 2022 else "2023이후"
            except (ValueError, TypeError):
                grad_group = "2023이후"
            node_id = f"early_grad_기준_{grad_group}"
            if node_id in self.G.nodes:
                results.append(SearchResult(
                    text=self._fmt_early_graduation_criteria(
                        dict(self.G.nodes[node_id])
                    ),
                    score=1.2,
                    source="graph",
                ))
        else:
            # 학번 미입력 → 전 그룹 기준 모두 반환
            for grad_group in ("2022이전", "2023이후"):
                node_id = f"early_grad_기준_{grad_group}"
                if node_id in self.G.nodes:
                    results.append(SearchResult(
                        text=self._fmt_early_graduation_criteria(
                            dict(self.G.nodes[node_id])
                        ),
                        score=1.1,
                        source="graph",
                    ))

        # ④ 기타사항
        if "early_grad_기타사항" in self.G.nodes:
            results.append(SearchResult(
                text=self._fmt_early_graduation_notes(
                    dict(self.G.nodes["early_grad_기타사항"])
                ),
                score=0.95,
                source="graph",
            ))

        return results

    def _query_alternatives(self, course_name: str) -> List[SearchResult]:
        if not course_name:
            return []
        alts = self.get_alternatives(course_name)
        if not alts:
            return []
        lines = [f"[대체/동일과목] {course_name}"]
        for a in alts:
            lines.append(
                f"- {a.get('과목번호', '')} {a.get('과목명', '')} "
                f"({a.get('학점', '')}학점, {a.get('이수구분', '')})"
            )
        return [SearchResult(text="\n".join(lines), score=1.0, source="graph")]

    # ── 포맷팅 헬퍼 ──────────────────────────────────────────

    @staticmethod
    def _fmt_graduation(student_id: str, student_type: str, data: dict) -> str:
        group = get_student_group(student_id)
        lines = [f"[졸업요건] {group}학번 {student_type}"]
        for key in (
            "졸업학점", "교양이수학점", "글로벌소통역량학점",
            "취업커뮤니티요건", "NOMAD비교과지수",
            "졸업시험여부", "졸업인증", "복수전공이수학점",
            "융합전공이수학점", "마이크로전공이수학점", "부전공이수학점",
        ):
            if key in data:
                lines.append(f"- {key}: {data[key]}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_major_method(data: dict) -> str:
        lines = [
            f"[전공이수방법] {data.get('방법유형', '')} "
            f"({data.get('적용학번범위', '')})"
        ]
        for key in (
            "주전공학점", "제2전공학점", "복수전공학점",
            "취업커뮤니티학점", "설명",
        ):
            if key in data:
                lines.append(f"- {key}: {data[key]}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_registration_rule(student_id: str, data: dict) -> str:
        group = get_reg_group(student_id)
        lines = [f"[수강신청규칙] {group}"]
        for key in (
            "최대신청학점", "장바구니최대학점",
            "평점4이상최대학점", "교직복수전공최대학점",
            "예외조건", "재수강제한", "재수강최고성적", "수강취소마감일시",
            "학점이월여부", "학점이월최대학점", "학점이월조건",
            "OCU초과학점",
            "정규학기_최대학점", "정규학기_최대과목",
            "졸업까지_최대학점", "졸업까지_최대과목",
            "시스템사용료_원", "초과수강료_원",
            "납부시작", "납부종료", "ID형식", "출석요건",
        ):
            if key in data:
                lines.append(f"- {key}: {data[key]}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_course(data: dict) -> str:
        lines = [
            f"[교과목] {data.get('과목번호', '')} {data.get('과목명', '')}"
        ]
        for key in ("학점", "시수", "이수구분", "성적평가방식", "개설학기", "수업방식"):
            if key in data:
                lines.append(f"- {key}: {data[key]}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_department(data: dict) -> str:
        lines = [f"[학과전공] {data.get('전공명', '')}"]
        for key in ("단과대학", "전공유형", "제1전공_이수학점", "전화번호", "사무실위치"):
            if key in data:
                lines.append(f"- {key}: {data[key]}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_early_graduation_eligibility(data: dict) -> str:
        """조기졸업 신청자격 노드 포맷팅."""
        lines = ["[조기졸업 신청자격]"]
        if data.get("신청학기"):
            lines.append(f"- 신청대상: {data['신청학기']}")
        if data.get("편입생_신청불가"):
            lines.append("- 편입생은 신청 불가")
        lines.append("- 평점평균 기준 (신청일 기준):")
        for key, label in (
            ("평점기준_2005이전", "2005학번 이전"),
            ("평점기준_2006", "2006학번"),
            ("평점기준_2007이후", "2007학번 이후"),
        ):
            if data.get(key):
                lines.append(f"  · {label}: {data[key]}")
        if data.get("글로벌미래융합학부"):
            lines.append(f"- 글로벌미래융합학부: {data['글로벌미래융합학부']}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_early_graduation_criteria(data: dict) -> str:
        """조기졸업 졸업기준 노드 포맷팅 (학번별)."""
        lines = [f"[조기졸업 졸업기준] {data.get('적용대상', '')}"]
        if data.get("기준학점"):
            lines.append(f"- 기준학점: {data['기준학점']}학점 이상")
        if data.get("비고"):
            lines.append(f"- 비고: {data['비고']}")
        if data.get("이수조건"):
            lines.append(f"- 이수조건: {data['이수조건']}")
        return "\n".join(lines)

    @staticmethod
    def _fmt_early_graduation_notes(data: dict) -> str:
        """조기졸업 기타사항 노드 포맷팅."""
        lines = ["[조기졸업 기타사항]"]
        if data.get("탈락자처리"):
            lines.append(f"- 탈락자: {data['탈락자처리']}")
        if data.get("합격자졸업유예"):
            lines.append(f"- 합격자 졸업유예 신청: {data['합격자졸업유예']}")
        if data.get("7학기등록주의"):
            lines.append(f"- 7학기 등록 학생 주의사항: {data['7학기등록주의']}")
        return "\n".join(lines)
