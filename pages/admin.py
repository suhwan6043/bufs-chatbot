"""
캠챗 관리자 페이지
조기졸업·학사일정 등 학기가 바뀔 때마다 수정이 필요한 데이터를 GUI로 관리합니다.

접근 방법:
    Streamlit 사이드바 자동 네비게이션 또는 직접 URL /admin 접근
보안:
    비밀번호 게이트 — URL 노출 시에도 st.stop()으로 실행 차단
    비밀번호 변경: .env 파일에 ADMIN_PASSWORD=원하는비밀번호 추가
"""

import sys
from collections import Counter
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app.config import settings
from app.graphdb.academic_graph import AcademicGraph

# ── 페이지 설정 (반드시 첫 번째 st 호출) ──────────────
st.set_page_config(
    page_title="캠챗 관리자",
    page_icon="🔧",
    layout="wide",
)


# ════════════════════════════════════════════════════
# 인증 게이트
# ════════════════════════════════════════════════════
def _check_auth() -> None:
    """비밀번호가 맞을 때만 페이지를 계속 실행합니다."""
    if st.session_state.get("is_admin"):
        return

    st.title("🔐 관리자 로그인")
    st.markdown("---")
    with st.form("admin_login_form"):
        pw = st.text_input("비밀번호", type="password", placeholder="관리자 비밀번호 입력")
        if st.form_submit_button("로그인", use_container_width=True):
            if pw == settings.admin.password:
                st.session_state.is_admin = True
                st.rerun()
            else:
                st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


_check_auth()


# ════════════════════════════════════════════════════
# 그래프 로더 (관리자 세션 전용)
# ════════════════════════════════════════════════════
def _get_graph() -> AcademicGraph:
    """관리자 세션에 그래프를 캐시합니다. '다시 로드' 버튼으로 초기화 가능."""
    if "admin_graph" not in st.session_state:
        st.session_state.admin_graph = AcademicGraph()
    return st.session_state.admin_graph


graph = _get_graph()


# ════════════════════════════════════════════════════
# 헤더
# ════════════════════════════════════════════════════
col_title, col_actions = st.columns([5, 1])
with col_title:
    st.title("🔧 캠챗 관리자 페이지")
    st.caption(
        f"그래프: {settings.graph.graph_path}  |  "
        f"노드 {graph.G.number_of_nodes()}개 / 엣지 {graph.G.number_of_edges()}개"
    )
with col_actions:
    st.markdown("<div style='margin-top:1.4rem;'></div>", unsafe_allow_html=True)
    if st.button("🔄 그래프 새로고침", use_container_width=True):
        del st.session_state["admin_graph"]
        st.rerun()
    if st.button("🚪 로그아웃", use_container_width=True):
        st.session_state.pop("is_admin", None)
        st.rerun()

st.divider()

# ════════════════════════════════════════════════════
# 탭 구성
# ════════════════════════════════════════════════════
tab_early, tab_schedule, tab_status = st.tabs([
    "🎓 조기졸업 관리",
    "📅 학사일정 관리",
    "📊 그래프 현황",
])


# ════════════════════════════════════════════════════
# Tab 1 : 조기졸업 관리
# ════════════════════════════════════════════════════
with tab_early:
    st.subheader("조기졸업 데이터 관리")
    st.info(
        "각 섹션을 수정하고 **저장** 버튼을 누르면 그래프 파일에 즉시 반영됩니다.  \n"
        "저장 후 채팅 페이지에서 변경사항을 반영하려면 아래 **[그래프 현황] 탭 > '채팅 세션 초기화'** 버튼을 눌러주세요.",
        icon="ℹ️",
    )

    # ── A. 신청기간 ─────────────────────────────────
    with st.expander("📆 신청기간 추가 / 수정", expanded=True):
        # 현재 등록된 조기졸업 일정 목록 표시
        existing = [
            {"id": nid, **data}
            for nid, data in graph.G.nodes(data=True)
            if data.get("type") == "학사일정"
            and "조기졸업" in data.get("이벤트명", "")
        ]
        if existing:
            st.markdown("**현재 등록된 신청기간**")
            for s in sorted(existing, key=lambda x: x.get("시작일", "")):
                st.markdown(
                    f"- `{s['id']}` : **{s.get('시작일','')} ~ {s.get('종료일','')}** "
                    f"({s.get('학기','')})"
                )
            st.markdown("---")

        st.markdown("**새 신청기간 입력 (학기가 바뀌면 여기에 추가)**")
        with st.form("form_early_schedule"):
            c1, c2, c3 = st.columns(3)
            with c1:
                new_semester = st.text_input(
                    "학기", value="2026-1", placeholder="예: 2026-1"
                )
            with c2:
                new_start = st.date_input("시작일", key="es_start")
            with c3:
                new_end = st.date_input("종료일", key="es_end")
            new_method = st.text_input(
                "신청방법",
                value=(
                    "학생포털시스템(https://m.bufs.ac.kr)"
                    " → 로그인 → 졸업 → 조기졸업 신청/조회"
                ),
            )
            if st.form_submit_button("신청기간 저장", use_container_width=True):
                graph.add_schedule(
                    "조기졸업신청",
                    new_semester,
                    {
                        "시작일": new_start.strftime("%Y-%m-%d"),
                        "종료일": new_end.strftime("%Y-%m-%d"),
                        "신청방법": new_method,
                    },
                )
                # 학사일정 → 신청자격 엣지 자동 연결
                graph.add_relation(
                    f"schedule_조기졸업신청_{new_semester}",
                    "early_grad_신청자격",
                    "기간정한다",
                )
                graph.save()
                st.success(f"신청기간 저장 완료: {new_semester}")
                del st.session_state["admin_graph"]
                st.rerun()

    # ── B. 졸업기준 (학번별 기준학점) ───────────────
    with st.expander("📋 졸업기준 (학번별 기준학점)", expanded=True):
        GRAD_GROUPS = {
            "2022이전": "2022학번 이전",
            "2023이후": "2023학번 이후",
        }
        with st.form("form_grad_criteria"):
            st.markdown("**학번별 기준학점을 수정하세요.**")
            inputs: dict = {}
            for key, label in GRAD_GROUPS.items():
                node_id = f"early_grad_기준_{key}"
                cur = dict(graph.G.nodes[node_id]) if node_id in graph.G.nodes else {}
                st.markdown(f"**{label}**")
                c1, c2 = st.columns([1, 2])
                with c1:
                    credits = st.number_input(
                        "기준학점 (이상)",
                        min_value=60,
                        max_value=200,
                        value=int(cur.get("기준학점", 120 if "2023" in key else 130)),
                        step=1,
                        key=f"credits_{key}",
                    )
                with c2:
                    note = st.text_input(
                        "비고",
                        value=cur.get("비고", ""),
                        key=f"note_{key}",
                    )
                condition = st.text_area(
                    "이수조건",
                    value=cur.get(
                        "이수조건",
                        "각 영역별(교양, 전공 등) 이수학점 취득 / "
                        "졸업 전공시험(졸업논문) 합격 / "
                        "기타 졸업인증 등 학번별 졸업요건 충족",
                    ),
                    height=80,
                    key=f"cond_{key}",
                )
                inputs[key] = {
                    "cur": cur,
                    "credits": credits,
                    "note": note,
                    "condition": condition,
                }
                st.markdown("---")

            if st.form_submit_button("기준학점 저장", use_container_width=True):
                for key, v in inputs.items():
                    label = GRAD_GROUPS[key]
                    updated = dict(v["cur"])
                    updated.update(
                        {
                            "적용대상": label,
                            "기준학점": v["credits"],
                            "이수조건": v["condition"],
                        }
                    )
                    if v["note"]:
                        updated["비고"] = v["note"]
                    else:
                        updated.pop("비고", None)
                    graph.add_early_graduation(f"기준_{key}", updated)
                graph.save()
                st.success("기준학점 저장 완료")

    # ── C. 신청자격 (평점 기준) ─────────────────────
    with st.expander("✅ 신청자격 (평점 기준 · 대상 학기)", expanded=False):
        elig = dict(graph.G.nodes.get("early_grad_신청자격", {}))
        with st.form("form_eligibility"):
            semester_req = st.text_input(
                "신청 가능 학기",
                value=elig.get("신청학기", "6학기 또는 7학기 등록 재학생"),
            )
            st.markdown("**평점평균 기준 (신청일 기준 직전 학기까지 누적)**")
            c1, c2, c3 = st.columns(3)
            with c1:
                gpa_2005 = st.text_input(
                    "2005학번 이전",
                    value=elig.get("평점기준_2005이전", "4.0 이상"),
                )
            with c2:
                gpa_2006 = st.text_input(
                    "2006학번",
                    value=elig.get("평점기준_2006", "4.2 이상"),
                )
            with c3:
                gpa_2007 = st.text_input(
                    "2007학번 이후",
                    value=elig.get("평점기준_2007이후", "4.3 이상"),
                )
            global_college = st.text_input(
                "글로벌미래융합학부",
                value=elig.get("글로벌미래융합학부", "별도기준 적용"),
            )
            no_transfer = st.checkbox(
                "편입생 신청 불가",
                value=bool(elig.get("편입생_신청불가", True)),
            )
            if st.form_submit_button("신청자격 저장", use_container_width=True):
                updated_elig = dict(elig)
                updated_elig.update(
                    {
                        "신청학기": semester_req,
                        "평점기준_2005이전": gpa_2005,
                        "평점기준_2006": gpa_2006,
                        "평점기준_2007이후": gpa_2007,
                        "글로벌미래융합학부": global_college,
                        "편입생_신청불가": no_transfer,
                    }
                )
                graph.add_early_graduation("신청자격", updated_elig)
                graph.save()
                st.success("신청자격 저장 완료")

    # ── D. 기타사항 ─────────────────────────────────
    with st.expander("📌 기타사항 (탈락자·합격자·7학기 주의)", expanded=False):
        notes = dict(graph.G.nodes.get("early_grad_기타사항", {}))
        with st.form("form_notes"):
            dropout = st.text_area(
                "탈락자 처리",
                value=notes.get("탈락자처리", "전어학기 등록금 납부, 수강신청 및 학점이수 필수"),
                height=80,
            )
            pass_note = st.text_area(
                "합격자 졸업유예 신청",
                value=notes.get("합격자졸업유예", "신청 불가 (졸업합격자로 유예대상 아님)"),
                height=80,
            )
            sem7_note = st.text_area(
                "7학기 등록 학생 주의사항",
                value=notes.get(
                    "7학기등록주의",
                    "7학기 등록 학생은 대상 학기(7학기차) 지정된 신청기간 내에 신청 필수. "
                    "기간 내 미신청 시 조기졸업 불가, 해당 학기는 이수 완료 학기로 처리됨",
                ),
                height=100,
            )
            if st.form_submit_button("기타사항 저장", use_container_width=True):
                updated_notes = dict(notes)
                updated_notes.update(
                    {
                        "탈락자처리": dropout,
                        "합격자졸업유예": pass_note,
                        "7학기등록주의": sem7_note,
                    }
                )
                graph.add_early_graduation("기타사항", updated_notes)
                graph.save()
                st.success("기타사항 저장 완료")


# ════════════════════════════════════════════════════
# Tab 2 : 학사일정 관리
# ════════════════════════════════════════════════════
with tab_schedule:
    st.subheader("학사일정 관리")

    # 현재 일정 목록 (날짜 있는 것만)
    all_schedules = [
        {"id": nid, **data}
        for nid, data in graph.G.nodes(data=True)
        if data.get("type") == "학사일정" and data.get("시작일")
    ]
    all_schedules.sort(key=lambda x: x.get("시작일", ""))

    if all_schedules:
        st.markdown("**현재 등록된 학사일정**")
        rows = [
            {
                "이벤트명": s.get("이벤트명", ""),
                "학기": s.get("학기", ""),
                "시작일": s.get("시작일", ""),
                "종료일": s.get("종료일", ""),
                "비고": s.get("비고", ""),
            }
            for s in all_schedules
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("등록된 학사일정이 없습니다.")

    st.markdown("---")

    with st.expander("➕ 새 일정 추가", expanded=False):
        with st.form("form_add_schedule"):
            c1, c2 = st.columns(2)
            with c1:
                ev_name = st.text_input("이벤트명", placeholder="예: 수강신청")
                ev_semester = st.text_input("학기", placeholder="예: 2026-1")
            with c2:
                ev_start = st.date_input("시작일", key="sched_s")
                ev_end = st.date_input("종료일", key="sched_e")
            ev_note = st.text_input("비고 (선택)")

            if st.form_submit_button("일정 추가", use_container_width=True):
                if ev_name and ev_semester:
                    sched_data = {
                        "시작일": ev_start.strftime("%Y-%m-%d"),
                        "종료일": ev_end.strftime("%Y-%m-%d"),
                    }
                    if ev_note:
                        sched_data["비고"] = ev_note
                    graph.add_schedule(ev_name, ev_semester, sched_data)
                    graph.save()
                    st.success(f"일정 추가 완료: {ev_name} ({ev_semester})")
                    del st.session_state["admin_graph"]
                    st.rerun()
                else:
                    st.error("이벤트명과 학기를 입력하세요.")

    with st.expander("✏️ 기존 일정 날짜 수정", expanded=False):
        if all_schedules:
            options = {
                f"{s['이벤트명']} ({s['학기']})": s
                for s in all_schedules
            }
            chosen_label = st.selectbox("수정할 일정 선택", list(options.keys()))
            chosen = options[chosen_label]

            with st.form("form_edit_schedule"):
                from datetime import date as _date
                def _parse(d: str):
                    try:
                        y, m, day = d.split("-")
                        return _date(int(y), int(m), int(day))
                    except Exception:
                        return _date.today()

                c1, c2 = st.columns(2)
                with c1:
                    edit_start = st.date_input(
                        "시작일", value=_parse(chosen.get("시작일", "")), key="edit_s"
                    )
                with c2:
                    edit_end = st.date_input(
                        "종료일", value=_parse(chosen.get("종료일", "")), key="edit_e"
                    )
                edit_note = st.text_input("비고", value=chosen.get("비고", ""))

                if st.form_submit_button("일정 수정 저장", use_container_width=True):
                    updated_sched = dict(chosen)
                    updated_sched["시작일"] = edit_start.strftime("%Y-%m-%d")
                    updated_sched["종료일"] = edit_end.strftime("%Y-%m-%d")
                    if edit_note:
                        updated_sched["비고"] = edit_note
                    # 기존 노드 속성 갱신 (add_schedule은 upsert 방식)
                    graph.add_schedule(
                        chosen.get("이벤트명", ""),
                        chosen.get("학기", ""),
                        updated_sched,
                    )
                    graph.save()
                    st.success("일정 수정 완료")
                    del st.session_state["admin_graph"]
                    st.rerun()
        else:
            st.info("수정할 일정이 없습니다.")


# ════════════════════════════════════════════════════
# Tab 3 : 그래프 현황
# ════════════════════════════════════════════════════
with tab_status:
    st.subheader("그래프 현황")

    type_counts = Counter(
        data.get("type", "unknown")
        for _, data in graph.G.nodes(data=True)
    )

    # ── 지표 카드 ──
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("전체 노드", graph.G.number_of_nodes())
    mc2.metric("전체 엣지", graph.G.number_of_edges())
    mc3.metric("조기졸업 노드", type_counts.get("조기졸업", 0))
    mc4.metric("학사일정 노드", type_counts.get("학사일정", 0))

    st.markdown("---")

    # ── 채팅 세션 초기화 버튼 ──
    st.markdown("**채팅 세션에 변경사항 반영**")
    st.caption(
        "그래프를 저장한 뒤 이 버튼을 누르면, "
        "채팅 페이지에서 다음 질문 시 그래프를 새로 로드합니다."
    )
    if st.button("♻️ 채팅 세션 초기화", type="primary", use_container_width=False):
        st.session_state.pop("initialized", None)
        st.success(
            "채팅 세션 초기화 완료. "
            "채팅 페이지로 이동하면 변경된 그래프가 자동으로 로드됩니다."
        )

    st.markdown("---")

    # ── 노드 타입별 현황 ──
    col_types, col_early = st.columns(2)

    with col_types:
        st.markdown("**노드 타입별 현황**")
        for ntype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            st.markdown(f"- **{ntype}**: {count}개")

    with col_early:
        st.markdown("**조기졸업 노드 상세**")
        early_nodes = [
            (nid, data)
            for nid, data in graph.G.nodes(data=True)
            if data.get("type") == "조기졸업"
        ]
        if early_nodes:
            for nid, data in sorted(early_nodes, key=lambda x: x[0]):
                with st.expander(f"📄 {nid}"):
                    skip = {"type", "구분"}
                    for k, v in data.items():
                        if k not in skip:
                            st.markdown(f"**{k}**: {v}")
        else:
            st.warning("조기졸업 노드가 없습니다.")

    st.markdown("---")
    st.caption(f"그래프 파일 경로: `{settings.graph.graph_path}`")
