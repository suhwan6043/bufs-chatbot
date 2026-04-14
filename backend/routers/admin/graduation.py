"""
졸업요건 + 조기졸업 + 학사일정 관리 API.
pages/admin.py Tab 0~2 (graduation, early_grad, schedule) 이식.
"""

import logging
from fastapi import APIRouter, Depends, Query

from backend.routers.admin.auth import require_admin, _audit
from backend.schemas.admin import (
    GraduationRequirement, GraduationOverview, GraduationOptions,
    DeptCertSave,
    EarlyGradSchedule, EarlyGradCriteria, EarlyGradEligibility,
    EarlyGradNotes,
    ScheduleEvent,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_GRAD_GROUP_OPTIONS = {
    "2024_2025": "2024학번 이후", "2023": "2023학번", "2022": "2022학번",
    "2021": "2021학번", "2017_2020": "2017~2020학번", "2016_before": "2016학번 이전",
}
_STUDENT_TYPES = ["내국인", "외국인", "편입생"]


def _get_graph():
    from app.graphdb.academic_graph import AcademicGraph
    return AcademicGraph()


def _save_graph(mutate_fn, audit_action="", audit_detail=""):
    from app.graphdb.academic_graph import AcademicGraph
    fresh = AcademicGraph()
    mutate_fn(fresh)
    fresh.save()
    if audit_action:
        _audit(audit_action, audit_detail)


# ── 졸업요건 ──

@router.get("/graduation", response_model=GraduationOverview)
async def get_graduation(_=Depends(require_admin)):
    """전체 졸업요건 현황 (모든 필드 포함)."""
    graph = _get_graph()
    rows = []
    for nid, d in graph.G.nodes(data=True):
        if d.get("type") != "졸업요건":
            continue
        grp = d.get("적용학번그룹", "")
        rows.append({
            "node_id": nid,
            "group": grp,
            "group_label": _GRAD_GROUP_OPTIONS.get(grp, grp),
            "student_type": d.get("학생유형", ""),
            "major": d.get("전공", "") or "공통",
            "credits": d.get("졸업학점", "-"),
            "liberal": d.get("교양이수학점", "-"),
            "global_comm": d.get("글로벌소통역량학점", "-"),
            "exam": "있음" if d.get("졸업시험여부") else "없음",
            "cert": d.get("졸업인증", "-") or "-",
            # 추가 필드 (Streamlit 대비 누락분)
            "community": d.get("취업커뮤니티요건", ""),
            "nomad": d.get("NOMAD비교과지수", ""),
            "career_explore": d.get("진로탐색학점"),
            "major_explore": d.get("전공탐색학점"),
            "exam_bool": bool(d.get("졸업시험여부", False)),
            "second_major_method": d.get("제2전공방법", ""),
            "double_major": d.get("복수전공이수학점"),
            "fusion_major": d.get("융합전공이수학점"),
            "micro_major": d.get("마이크로전공이수학점"),
            "minor_major": d.get("부전공이수학점"),
        })
    return GraduationOverview(rows=rows)


@router.put("/graduation")
async def update_graduation(body: GraduationRequirement, _=Depends(require_admin)):
    """졸업요건 저장/수정."""
    _grp, _stype, _mjr, _data = body.group, body.student_type, body.major, body.requirements
    _save_graph(
        lambda g: g.add_graduation_req(_grp, _stype, _data, major=_mjr),
        "SAVE_GRAD_REQ",
        f"group={_grp}, type={_stype}" + (f", major={_mjr}" if _mjr else ""),
    )
    return {"ok": True}


@router.get("/graduation/options", response_model=GraduationOptions)
async def get_graduation_options(_=Depends(require_admin)):
    """졸업요건 폼에 사용되는 선택 옵션 반환."""
    from app.graphdb.academic_graph import _DEPT_TREE
    return GraduationOptions(
        groups=_GRAD_GROUP_OPTIONS,
        student_types=_STUDENT_TYPES,
        dept_tree=dict(_DEPT_TREE),
    )


# ── 학과별 졸업인증 ──

@router.get("/graduation/dept-cert")
async def get_dept_cert(major: str = Query(...), _=Depends(require_admin)):
    """학과별 졸업인증 데이터 조회."""
    graph = _get_graph()
    cert_nid = _find_cert_node(graph, major)
    if not cert_nid or cert_nid not in graph.G.nodes:
        return {"node_id": None, "data": {}}
    d = dict(graph.G.nodes[cert_nid])
    return {
        "node_id": cert_nid,
        "data": {
            "cert_requirement": d.get("졸업시험_요건", ""),
            "cert_subjects": d.get("졸업시험_과목", ""),
            "cert_pass_criteria": d.get("졸업시험_합격기준", ""),
            "cert_alternative": d.get("졸업시험_대체방법", ""),
        },
    }


@router.put("/graduation/dept-cert")
async def save_dept_cert(body: DeptCertSave, _=Depends(require_admin)):
    """학과별 졸업인증 저장."""
    _major = body.major
    _attrs = {
        "졸업시험_요건": body.cert_requirement,
        "졸업시험_과목": body.cert_subjects,
        "졸업시험_합격기준": body.cert_pass_criteria,
        "졸업시험_대체방법": body.cert_alternative,
    }

    def _apply(g):
        cert_nid = _find_cert_node(g, _major)
        if not cert_nid:
            cert_nid = f"dept_{_major}전공"
        if cert_nid not in g.G.nodes:
            g.G.add_node(cert_nid, type="학과전공", 전공명=f"{_major}전공")
        g.G.nodes[cert_nid].update(_attrs)

    _save_graph(_apply, "SAVE_DEPT_CERT", f"major={_major}")
    return {"ok": True}


def _find_cert_node(graph, major: str):
    """전공명으로 학과전공 노드 ID를 찾습니다."""
    for candidate in (f"dept_{major}전공", f"dept_{major}"):
        if candidate in graph.G.nodes:
            return candidate
    mj = major.replace("어", "").replace(" ", "")
    best_nid, best_score = None, 0
    for nid, d in graph.G.nodes(data=True):
        if d.get("type") != "학과전공":
            continue
        nm = d.get("전공명", nid.replace("dept_", ""))
        nm_norm = nm.replace("전공", "").replace("학과", "").replace("어", "").replace(" ", "")
        score = 0
        if mj == nm_norm:
            score = 100
        elif len(mj) >= 3 and mj in nm_norm:
            score = 50 + len(nm_norm)
        elif len(nm_norm) >= 3 and nm_norm in mj:
            score = 30 + len(nm_norm)
        if score > best_score:
            best_score, best_nid = score, nid
    return best_nid


# ── 조기졸업 ──

@router.get("/early-graduation")
async def get_early_graduation(_=Depends(require_admin)):
    """조기졸업 전체 데이터."""
    graph = _get_graph()
    result = {"schedules": [], "criteria": [], "eligibility": {}, "notes": {}}

    for nid, d in graph.G.nodes(data=True):
        if d.get("type") == "학사일정" and "조기졸업" in d.get("이벤트명", ""):
            result["schedules"].append({
                "id": nid, "semester": d.get("학기", ""),
                "start_date": d.get("시작일", ""), "end_date": d.get("종료일", ""),
                "method": d.get("신청방법", ""),
            })
        if d.get("type") == "조기졸업" and nid.startswith("early_grad_기준"):
            result["criteria"].append({
                "id": nid,
                "group": d.get("적용대상", nid.replace("early_grad_기준_", "")),
                "credits": d.get("기준학점", 120),
                "note": d.get("비고", ""),
                "condition": d.get("이수조건", ""),
            })

    elig = dict(graph.G.nodes.get("early_grad_신청자격", {}))
    result["eligibility"] = {k: v for k, v in elig.items() if k != "type"}

    notes = dict(graph.G.nodes.get("early_grad_기타사항", {}))
    result["notes"] = {k: v for k, v in notes.items() if k != "type"}

    return result


@router.put("/early-graduation/schedule")
async def save_early_grad_schedule(body: EarlyGradSchedule, _=Depends(require_admin)):
    """조기졸업 신청기간 저장."""
    _ns, _sd = body.semester, {
        "시작일": body.start_date, "종료일": body.end_date, "신청방법": body.method,
    }
    def _apply(g):
        g.add_schedule("조기졸업신청", _ns, _sd)
        g.add_relation(f"schedule_조기졸업신청_{_ns}", "early_grad_신청자격", "기간정한다")
    _save_graph(_apply, "SAVE_EARLY_SCHEDULE", f"semester={body.semester}")
    return {"ok": True}


@router.put("/early-graduation/eligibility")
async def save_eligibility(body: EarlyGradEligibility, _=Depends(require_admin)):
    """조기졸업 신청자격(평점 기준) 저장."""
    _data = {
        "신청학기": body.semester_req, "평점기준_2005이전": body.gpa_2005,
        "평점기준_2006": body.gpa_2006, "평점기준_2007이후": body.gpa_2007,
        "글로벌미래융합학부": body.global_college, "편입생_신청불가": body.no_transfer,
    }
    def _apply(g):
        node = dict(g.G.nodes.get("early_grad_신청자격", {}))
        node.update(_data)
        g.add_early_graduation("신청자격", node)
    _save_graph(_apply, "SAVE_ELIGIBILITY")
    return {"ok": True}


@router.put("/early-graduation/criteria")
async def save_early_grad_criteria(body: EarlyGradCriteria, _=Depends(require_admin)):
    """학번별 기준학점 저장."""
    _key = body.group
    _data = {"기준학점": body.credits, "이수조건": body.condition}
    if body.note:
        _data["비고"] = body.note

    def _apply(g):
        cur = dict(g.G.nodes.get(f"early_grad_기준_{_key}", {}))
        cur.update(_data)
        cur["적용대상"] = _key
        g.add_early_graduation(f"기준_{_key}", cur)

    _save_graph(_apply, "SAVE_GRAD_CRITERIA", f"group={_key}")
    return {"ok": True}


@router.put("/early-graduation/notes")
async def save_early_grad_notes(body: EarlyGradNotes, _=Depends(require_admin)):
    """기타사항 저장."""
    _data = {
        "탈락자처리": body.dropout,
        "합격자졸업유예": body.pass_note,
        "7학기등록주의": body.sem7_note,
    }
    def _apply(g):
        node = dict(g.G.nodes.get("early_grad_기타사항", {}))
        node.update(_data)
        g.add_early_graduation("기타사항", node)
    _save_graph(_apply, "SAVE_NOTES")
    return {"ok": True}


# ── 학사일정 ──

@router.get("/schedule")
async def get_schedules(_=Depends(require_admin)):
    """전체 학사일정 목록."""
    graph = _get_graph()
    events = sorted(
        [{"id": nid, **{k: v for k, v in d.items() if k != "type"}}
         for nid, d in graph.G.nodes(data=True)
         if d.get("type") == "학사일정" and d.get("시작일")],
        key=lambda x: x.get("시작일", ""),
    )
    return {"events": events}


@router.post("/schedule")
async def add_schedule(body: ScheduleEvent, _=Depends(require_admin)):
    """학사일정 추가."""
    _en, _es = body.event_name, body.semester
    _sd = {"시작일": body.start_date, "종료일": body.end_date}
    if body.note:
        _sd["비고"] = body.note
    _save_graph(
        lambda g: g.add_schedule(_en, _es, _sd),
        "ADD_SCHEDULE", f"{_en} ({_es})",
    )
    return {"ok": True}


@router.put("/schedule")
async def update_schedule(body: ScheduleEvent, _=Depends(require_admin)):
    """학사일정 수정."""
    _en, _es = body.event_name, body.semester
    _sd = {"시작일": body.start_date, "종료일": body.end_date, "이벤트명": _en, "학기": _es}
    if body.note:
        _sd["비고"] = body.note
    _save_graph(
        lambda g: g.add_schedule(_en, _es, _sd),
        "EDIT_SCHEDULE", f"{_en} ({_es})",
    )
    return {"ok": True}
