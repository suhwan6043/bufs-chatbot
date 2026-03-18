"""
캠챗 관리자 페이지 (보안 강화)

보안 수칙 (6가지 전부 적용):
  1. hmac.compare_digest + SHA-256  — 타이밍 공격 방지
  2. 연속 실패 잠금                 — 브루트포스 방지 (기본 5회 / 15분)
  3. 세션 타임아웃                  — 비활성 30분 후 자동 로그아웃
  4. 감사 로그                      — 로그인·저장·로그아웃 전부 기록
  5. 기본 비밀번호 경고             — .env 미설정 시 경고 배너 표시
  6. ADMIN_PASSWORD 미설정 차단     — env 없이 기본값 사용 시 관리자 접근 거부

접근:  Streamlit URL /admin  (사이드바 자동 링크 또는 직접 입력)
설정:  .env  →  ADMIN_PASSWORD=강력한비밀번호
"""

import hashlib
import hmac
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app.config import settings, _ADMIN_PW_DEFAULT
from app.graphdb.academic_graph import AcademicGraph, get_student_group, GROUP_LABELS, _DEPT_TREE

# ── 페이지 설정 (반드시 첫 번째 st 호출) ──────────────
st.set_page_config(
    page_title="캠챗 관리자",
    page_icon="🔧",
    layout="wide",
)

# ── 보안 상수 (config에서 로드) ───────────────────────
_MAX_ATTEMPTS      = settings.admin.max_login_attempts
_LOCKOUT_SECS      = settings.admin.lockout_minutes * 60
_TIMEOUT_SECS      = settings.admin.session_timeout_minutes * 60


# ════════════════════════════════════════════════════
# 감사 로그 (모든 관리자 행동 기록)
# ════════════════════════════════════════════════════
def _audit(action: str, detail: str = "") -> None:
    """
    관리자 행동을 data/logs/admin_audit.log 에 기록합니다.
    비밀번호·개인정보는 절대 기록하지 않습니다.
    """
    log_dir = Path(settings.graph.graph_path).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "admin_audit.log"

    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {action}"
    if detail:
        line += f" | {detail}"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 로그 실패가 페이지를 막으면 안 됨


# ════════════════════════════════════════════════════
# 보안 수칙 1 — 타이밍 안전 비밀번호 비교
# ════════════════════════════════════════════════════
def _verify_password(input_pw: str) -> bool:
    """
    hmac.compare_digest 로 타이밍 공격을 방지합니다.
    두 해시값을 고정 시간에 비교하므로 비밀번호 길이·내용이 응답 시간에 노출되지 않습니다.
    """
    a = hashlib.sha256(input_pw.encode("utf-8")).digest()
    b = hashlib.sha256(settings.admin.password.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


# ════════════════════════════════════════════════════
# 보안 수칙 3 — 세션 타임아웃 체크
# ════════════════════════════════════════════════════
def _check_session_timeout() -> None:
    """
    마지막 활동 시각 기준으로 TIMEOUT_SECS 초 경과 시 자동 로그아웃합니다.
    페이지 렌더링마다 호출됩니다.
    """
    if not st.session_state.get("is_admin"):
        return

    last_active = st.session_state.get("admin_last_active", 0.0)
    if time.time() - last_active > _TIMEOUT_SECS:
        _audit("SESSION_TIMEOUT", f"timeout={settings.admin.session_timeout_minutes}분")
        for key in ("is_admin", "admin_login_time", "admin_last_active", "admin_graph"):
            st.session_state.pop(key, None)
        # 로그아웃 후 로그인 화면으로 이동 (rerun은 _check_auth에서 처리)

    # 활동 시각 갱신
    st.session_state.admin_last_active = time.time()


# ════════════════════════════════════════════════════
# 보안 수칙 2·5·6 — 인증 게이트
# ════════════════════════════════════════════════════
def _check_auth() -> None:
    """
    [수칙 6] ADMIN_PASSWORD 환경변수 미설정(기본값 사용) 여부를 먼저 확인합니다.
    [수칙 2] 연속 실패 시 세션 잠금 (MAX_ATTEMPTS 회 초과 → LOCKOUT_SECS 대기).
    [수칙 1] _verify_password() 로 타이밍 안전 비교.
    """
    if st.session_state.get("is_admin"):
        return  # 이미 인증됨

    # ── [수칙 6] 기본 비밀번호 사용 → 관리자 접근 차단 ──────────
    if settings.admin.password == _ADMIN_PW_DEFAULT:
        st.error(
            "**관리자 페이지 접근 불가**\n\n"
            "`.env` 파일에 `ADMIN_PASSWORD` 환경변수가 설정되지 않았습니다.  \n"
            "기본 비밀번호로는 보안상 관리자 페이지에 접근할 수 없습니다.\n\n"
            "```\n# .env\nADMIN_PASSWORD=강력한비밀번호를여기에입력\n```",
            icon="🚫",
        )
        st.stop()

    st.title("🔐 관리자 로그인")
    st.markdown("---")

    # ── [수칙 2] 잠금 상태 확인 ──────────────────────────────────
    lockout_until = st.session_state.get("admin_lockout_until", 0.0)
    remaining     = lockout_until - time.time()
    if remaining > 0:
        mins = int(remaining // 60) + 1
        st.warning(
            f"로그인 시도 횟수를 초과했습니다.  \n"
            f"약 **{mins}분** 후에 다시 시도하세요.",
            icon="🔒",
        )
        st.stop()

    # ── 로그인 폼 ─────────────────────────────────────────────────
    attempts_left = _MAX_ATTEMPTS - st.session_state.get("admin_failed_attempts", 0)

    with st.form("admin_login_form"):
        pw = st.text_input(
            "비밀번호",
            type="password",
            placeholder="관리자 비밀번호 입력",
        )
        submitted = st.form_submit_button("로그인", use_container_width=True)

    if submitted:
        if _verify_password(pw):
            # ── 로그인 성공 ───────────────────────────────────────
            st.session_state.is_admin          = True
            st.session_state.admin_login_time  = time.time()
            st.session_state.admin_last_active = time.time()
            st.session_state.pop("admin_failed_attempts", None)
            st.session_state.pop("admin_lockout_until",   None)
            _audit("LOGIN_SUCCESS")
            st.rerun()
        else:
            # ── 로그인 실패 ───────────────────────────────────────
            failed = st.session_state.get("admin_failed_attempts", 0) + 1
            st.session_state.admin_failed_attempts = failed
            _audit("LOGIN_FAILED", f"attempts={failed}")

            if failed >= _MAX_ATTEMPTS:
                st.session_state.admin_lockout_until = time.time() + _LOCKOUT_SECS
                st.session_state.admin_failed_attempts = 0
                _audit(
                    "ACCOUNT_LOCKED",
                    f"lockout={settings.admin.lockout_minutes}분",
                )
                st.error(
                    f"로그인 시도 횟수 {_MAX_ATTEMPTS}회를 초과했습니다.  \n"
                    f"**{settings.admin.lockout_minutes}분** 동안 잠깁니다.",
                    icon="🔒",
                )
            else:
                left = _MAX_ATTEMPTS - failed
                st.error(
                    f"비밀번호가 올바르지 않습니다. (남은 시도 {left}회)",
                    icon="⚠️",
                )

    st.stop()


# ── 순서 중요: 타임아웃 체크 → 인증 체크 ─────────────
_check_session_timeout()
_check_auth()


# ════════════════════════════════════════════════════
# 그래프 로더 (관리자 세션 전용)
# ════════════════════════════════════════════════════
def _get_graph() -> AcademicGraph:
    if "admin_graph" not in st.session_state:
        st.session_state.admin_graph = AcademicGraph()
    return st.session_state.admin_graph


graph = _get_graph()


# ════════════════════════════════════════════════════
# 헤더
# ════════════════════════════════════════════════════

# ── [수칙 5] 기본 비밀번호 경고 배너 ─────────────────
# (수칙 6으로 이미 차단되지만, 혹시 코드를 직접 수정하여 우회한 경우 대비)
if settings.admin.password == _ADMIN_PW_DEFAULT:
    st.warning(
        "**기본 비밀번호 사용 중** — `.env` 파일에서 `ADMIN_PASSWORD`를 변경하세요.",
        icon="⚠️",
    )

col_title, col_actions = st.columns([5, 1])
with col_title:
    login_dt  = datetime.fromtimestamp(
        st.session_state.get("admin_login_time", time.time())
    ).strftime("%H:%M:%S")
    timeout_m = settings.admin.session_timeout_minutes
    st.title("🔧 캠챗 관리자 페이지")
    st.caption(
        f"로그인 시각: {login_dt}  |  "
        f"세션 만료: {timeout_m}분 비활성 시  |  "
        f"그래프: 노드 {graph.G.number_of_nodes()}개 / 엣지 {graph.G.number_of_edges()}개"
    )

with col_actions:
    st.markdown("<div style='margin-top:1.4rem;'></div>", unsafe_allow_html=True)
    if st.button("🔄 그래프 새로고침", use_container_width=True):
        st.session_state.pop("admin_graph", None)
        st.rerun()
    if st.button("🚪 로그아웃", use_container_width=True):
        _audit("LOGOUT")
        for key in ("is_admin", "admin_login_time", "admin_last_active", "admin_graph"):
            st.session_state.pop(key, None)
        st.rerun()

st.divider()


# ════════════════════════════════════════════════════
# 탭 구성
# ════════════════════════════════════════════════════
tab_grad, tab_early, tab_schedule, tab_status = st.tabs([
    "📋 졸업요건 관리",
    "🎓 조기졸업 관리",
    "📅 학사일정 관리",
    "📊 그래프 현황",
])


# ════════════════════════════════════════════════════
# Tab 0 : 졸업요건 관리
# ════════════════════════════════════════════════════

# ── 학번 그룹 목록 (그래프 키 → 표시 레이블) ──────────
_GRAD_GROUP_OPTIONS: dict[str, str] = {
    "2024_2025": "2024학번 이후",
    "2023":      "2023학번",
    "2022":      "2022학번",
    "2021":      "2021학번",
    "2017_2020": "2017~2020학번",
    "2016_before": "2016학번 이전",
}
_STUDENT_TYPES = ["내국인", "외국인", "편입생"]

# ── 전공 선택 옵션 빌드 (공통 + 학부별 전공) ──────────────
# { 표시 레이블: 전공 키(None이면 공통) }
_MAJOR_OPTIONS: dict[str, str | None] = {"공통 (전공무관)": None}
for _dept, _majors in _DEPT_TREE.items():
    for _major in _majors:
        _MAJOR_OPTIONS[f"{_dept} › {_major}"] = _major

# ── 졸업요건 필드 정의 ─────────────────────────────────
# (key, 레이블, 타입, 기본값)
# 타입: "int_req" = 필수 정수, "int_opt" = 선택 정수, "text" = 문자열, "bool" = 불리언
_GRAD_FIELDS = [
    ("졸업학점",           "졸업학점",           "int_req", 120),
    ("교양이수학점",        "교양이수학점",        "int_req",  30),
    ("글로벌소통역량학점",  "글로벌소통역량학점",  "int_req",   6),
    ("진로탐색학점",        "진로탐색학점",        "int_opt",   None),
    ("전공탐색학점",        "전공탐색학점",        "int_opt",   None),
    ("취업커뮤니티요건",    "취업커뮤니티요건",    "text",    "2학점"),
    ("NOMAD비교과지수",     "NOMAD비교과지수",     "text",    ""),
    ("졸업시험여부",        "졸업시험 있음",       "bool",    False),
    ("졸업인증",            "졸업인증",            "text",    ""),
    ("제2전공방법",         "제2전공방법",         "text",    ""),
    ("복수전공이수학점",    "복수전공이수학점",    "int_opt",  None),
    ("융합전공이수학점",    "융합전공이수학점",    "int_opt",  None),
    ("마이크로전공이수학점","마이크로전공이수학점","int_opt",  None),
    ("부전공이수학점",      "부전공이수학점",      "int_opt",  None),
]


def _int_or_none(s: str):
    """빈 문자열 → None, 숫자 문자열 → int"""
    try:
        return int(s.strip()) if s.strip() else None
    except (ValueError, AttributeError):
        return None


def _cur_int(cur: dict, key: str, default: int) -> int:
    """그래프 노드에서 정수 값을 안전하게 읽습니다."""
    v = cur.get(key)
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


with tab_grad:
    st.subheader("📋 졸업요건 관리")
    st.info(
        "학번 그룹과 학생 유형을 선택해 졸업요건을 입력·수정하세요.  \n"
        "저장 후 **[그래프 현황] 탭 → '채팅 세션 초기화'** 버튼을 눌러야 채팅에 반영됩니다.",
        icon="ℹ️",
    )

    # ── 전체 현황 테이블 ────────────────────────────────
    with st.expander("📊 전체 졸업요건 현황 보기", expanded=False):
        rows = []
        # 공통(전공무관) 노드
        for grp, grp_label in _GRAD_GROUP_OPTIONS.items():
            for stype in _STUDENT_TYPES:
                nid = f"grad_{grp}_{stype}"
                d = dict(graph.G.nodes[nid]) if nid in graph.G.nodes else {}
                if not d:
                    continue
                rows.append({
                    "학번그룹":   grp_label,
                    "유형":       stype,
                    "전공":       "공통",
                    "졸업학점":   d.get("졸업학점", "-"),
                    "교양":       d.get("교양이수학점", "-"),
                    "글로벌소통": d.get("글로벌소통역량학점", "-"),
                    "취업커뮤":   d.get("취업커뮤니티요건", "-"),
                    "졸업시험":   "있음" if d.get("졸업시험여부") else "없음",
                    "졸업인증":   d.get("졸업인증", "-") or "-",
                    "복수전공":   d.get("복수전공이수학점", "-"),
                    "부전공":     d.get("부전공이수학점", "-"),
                })
        # 전공별 노드 (grad_{group}_{type}_{major} 형식)
        for nid, d in graph.G.nodes(data=True):
            if d.get("type") != "졸업요건":
                continue
            major_val = d.get("전공")
            if not major_val:
                continue  # 공통 노드는 이미 위에서 처리
            grp = d.get("적용학번그룹", "")
            stype = d.get("학생유형", "")
            rows.append({
                "학번그룹":   _GRAD_GROUP_OPTIONS.get(grp, grp),
                "유형":       stype,
                "전공":       major_val,
                "졸업학점":   d.get("졸업학점", "-"),
                "교양":       d.get("교양이수학점", "-"),
                "글로벌소통": d.get("글로벌소통역량학점", "-"),
                "취업커뮤":   d.get("취업커뮤니티요건", "-"),
                "졸업시험":   "있음" if d.get("졸업시험여부") else "없음",
                "졸업인증":   d.get("졸업인증", "-") or "-",
                "복수전공":   d.get("복수전공이수학점", "-"),
                "부전공":     d.get("부전공이수학점", "-"),
            })
        if rows:
            import pandas as _pd
            st.dataframe(_pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.warning("아직 졸업요건 데이터가 없습니다.")

    st.markdown("---")

    # ── 그룹 / 유형 / 전공 선택 ─────────────────────────
    col_g, col_t, col_m = st.columns(3)
    with col_g:
        sel_group = st.selectbox(
            "학번 그룹",
            list(_GRAD_GROUP_OPTIONS.keys()),
            format_func=lambda k: _GRAD_GROUP_OPTIONS[k],
            key="grad_sel_group",
        )
    with col_t:
        sel_type = st.selectbox(
            "학생 유형",
            _STUDENT_TYPES,
            key="grad_sel_type",
        )
    with col_m:
        sel_major_label = st.selectbox(
            "전공",
            list(_MAJOR_OPTIONS.keys()),
            key="grad_sel_major",
            help="공통(전공무관)은 학과 지정 없이 학번그룹·학생유형으로만 저장됩니다.",
        )
    sel_major: str | None = _MAJOR_OPTIONS[sel_major_label]

    node_id = f"grad_{sel_group}_{sel_type}"
    if sel_major:
        node_id = f"{node_id}_{sel_major}"

    cur = {k: v for k, v in dict(graph.G.nodes.get(node_id, {})).items()
           if k not in ("type", "적용학번그룹", "학생유형", "전공")}

    disp_label = f"{_GRAD_GROUP_OPTIONS[sel_group]} / {sel_type}"
    if sel_major:
        disp_label += f" / {sel_major}"

    if cur:
        st.success(f"기존 데이터 로드: **{disp_label}**", icon="✅")
    else:
        st.warning(f"데이터 없음: **{disp_label}** — 저장하면 새로 생성됩니다.", icon="⚠️")

    # ── 입력 폼 ─────────────────────────────────────────
    with st.form(f"form_grad_req"):
        st.markdown("#### 필수 항목")
        c1, c2, c3 = st.columns(3)
        with c1:
            f_grad    = st.number_input("졸업학점",          min_value=60, max_value=200,
                                        value=_cur_int(cur, "졸업학점",          120), step=1)
        with c2:
            f_liberal = st.number_input("교양이수학점",      min_value=0,  max_value=100,
                                        value=_cur_int(cur, "교양이수학점",      30),  step=1)
        with c3:
            f_global  = st.number_input("글로벌소통역량학점",min_value=0,  max_value=30,
                                        value=_cur_int(cur, "글로벌소통역량학점", 6),   step=1)

        c1, c2, c3 = st.columns(3)
        with c1:
            f_community = st.text_input("취업커뮤니티요건",
                                        value=cur.get("취업커뮤니티요건", "2학점"))
        with c2:
            f_exam = st.checkbox("졸업시험 있음",
                                 value=bool(cur.get("졸업시험여부", False)))
        with c3:
            f_cert = st.text_input("졸업인증",
                                   value=cur.get("졸업인증", "") or "",
                                   placeholder="예: TOPIK 4급, 없음")

        st.markdown("#### 선택 항목 (해당없으면 빈칸)")
        c1, c2, c3 = st.columns(3)
        with c1:
            f_nomad   = st.text_input("NOMAD비교과지수",
                                      value=cur.get("NOMAD비교과지수", "") or "",
                                      placeholder="예: 미적용")
        with c2:
            f_career  = st.text_input("진로탐색학점",
                                      value=str(cur["진로탐색학점"]) if cur.get("진로탐색학점") is not None else "",
                                      placeholder="예: 2")
        with c3:
            f_major_exp = st.text_input("전공탐색학점",
                                        value=str(cur["전공탐색학점"]) if cur.get("전공탐색학점") is not None else "",
                                        placeholder="예: 3")

        f_second = st.text_area("제2전공방법",
                                value=cur.get("제2전공방법", "") or "",
                                height=60,
                                placeholder="예: [방법1]복수·융합전공 30학점 / [방법2]마이크로전공 9학점")

        st.markdown("#### 전공 이수학점 (해당없으면 빈칸)")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            f_double  = st.text_input("복수전공이수학점",
                                      value=str(cur["복수전공이수학점"]) if cur.get("복수전공이수학점") is not None else "",
                                      placeholder="예: 30")
        with c2:
            f_fusion  = st.text_input("융합전공이수학점",
                                      value=str(cur["융합전공이수학점"]) if cur.get("융합전공이수학점") is not None else "",
                                      placeholder="예: 30")
        with c3:
            f_micro   = st.text_input("마이크로전공이수학점",
                                      value=str(cur["마이크로전공이수학점"]) if cur.get("마이크로전공이수학점") is not None else "",
                                      placeholder="예: 9")
        with c4:
            f_minor   = st.text_input("부전공이수학점",
                                      value=str(cur["부전공이수학점"]) if cur.get("부전공이수학점") is not None else "",
                                      placeholder="예: 18")

        submitted = st.form_submit_button("💾 졸업요건 저장", use_container_width=True, type="primary")

    if submitted:
        new_data: dict = {
            "졸업학점":          f_grad,
            "교양이수학점":      f_liberal,
            "글로벌소통역량학점": f_global,
            "취업커뮤니티요건":  f_community,
            "졸업시험여부":      f_exam,
        }
        # 선택 항목 — 값 있을 때만 추가
        if f_cert.strip():     new_data["졸업인증"]            = f_cert.strip()
        if f_nomad.strip():    new_data["NOMAD비교과지수"]      = f_nomad.strip()
        if f_second.strip():   new_data["제2전공방법"]          = f_second.strip()
        v = _int_or_none(f_career);    new_data["진로탐색학점"]        = v  # None 허용
        v = _int_or_none(f_major_exp); new_data["전공탐색학점"]        = v
        v = _int_or_none(f_double);    new_data["복수전공이수학점"]    = v
        v = _int_or_none(f_fusion);    new_data["융합전공이수학점"]    = v
        v = _int_or_none(f_micro);     new_data["마이크로전공이수학점"] = v
        v = _int_or_none(f_minor);     new_data["부전공이수학점"]      = v

        graph.add_graduation_req(sel_group, sel_type, new_data, major=sel_major)
        graph.save()
        audit_detail = f"group={sel_group}, type={sel_type}"
        if sel_major:
            audit_detail += f", major={sel_major}"
        _audit("SAVE_GRAD_REQ", audit_detail)
        st.success(
            f"저장 완료: **{disp_label}**  \n"
            f"채팅에 반영하려면 [그래프 현황] 탭 → '채팅 세션 초기화' 버튼을 누르세요."
        )
        st.session_state.pop("admin_graph", None)
        st.rerun()


# ════════════════════════════════════════════════════
# Tab 1 : 조기졸업 관리
# ════════════════════════════════════════════════════
with tab_early:
    st.subheader("조기졸업 데이터 관리")
    st.info(
        "각 섹션을 수정하고 **저장** 버튼을 누르면 그래프 파일에 즉시 반영됩니다.  \n"
        "저장 후 채팅에 반영하려면 **[그래프 현황] 탭 → '채팅 세션 초기화'** 버튼을 누르세요.",
        icon="ℹ️",
    )

    # ── A. 신청기간 ─────────────────────────────────
    with st.expander("📆 신청기간 추가 / 수정", expanded=True):
        existing = sorted(
            [
                {"id": nid, **data}
                for nid, data in graph.G.nodes(data=True)
                if data.get("type") == "학사일정"
                and "조기졸업" in data.get("이벤트명", "")
            ],
            key=lambda x: x.get("시작일", ""),
        )
        if existing:
            st.markdown("**현재 등록된 신청기간**")
            for s in existing:
                st.markdown(
                    f"- `{s['id']}` : **{s.get('시작일','')} ~ {s.get('종료일','')}**"
                    f" ({s.get('학기','')})"
                )
            st.markdown("---")

        st.markdown("**새 신청기간 입력**")
        with st.form("form_early_schedule"):
            c1, c2, c3 = st.columns(3)
            with c1:
                new_semester = st.text_input("학기", value="2026-1", placeholder="예: 2026-1")
            with c2:
                new_start = st.date_input("시작일", key="es_start")
            with c3:
                new_end = st.date_input("종료일", key="es_end")
            new_method = st.text_input(
                "신청방법",
                value="학생포털시스템(https://m.bufs.ac.kr) → 로그인 → 졸업 → 조기졸업 신청/조회",
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
                graph.add_relation(
                    f"schedule_조기졸업신청_{new_semester}",
                    "early_grad_신청자격",
                    "기간정한다",
                )
                graph.save()
                _audit("SAVE_EARLY_SCHEDULE", f"semester={new_semester}")
                st.success(f"신청기간 저장 완료: {new_semester}")
                st.session_state.pop("admin_graph", None)
                st.rerun()

    # ── B. 졸업기준 (학번별 기준학점) ───────────────
    with st.expander("📋 졸업기준 (학번별 기준학점)", expanded=True):
        GRAD_GROUPS = {
            "2022이전": "2022학번 이전",
            "2023이후": "2023학번 이후",
        }
        with st.form("form_grad_criteria"):
            inputs: dict = {}
            for key, label in GRAD_GROUPS.items():
                node_id = f"early_grad_기준_{key}"
                cur = dict(graph.G.nodes[node_id]) if node_id in graph.G.nodes else {}
                st.markdown(f"**{label}**")
                c1, c2 = st.columns([1, 2])
                with c1:
                    credits = st.number_input(
                        "기준학점 (이상)",
                        min_value=60, max_value=200,
                        value=int(cur.get("기준학점", 120 if "2023" in key else 130)),
                        step=1,
                        key=f"credits_{key}",
                    )
                with c2:
                    note = st.text_input("비고", value=cur.get("비고", ""), key=f"note_{key}")
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
                inputs[key] = {"cur": cur, "credits": credits, "note": note, "condition": condition}
                st.markdown("---")

            if st.form_submit_button("기준학점 저장", use_container_width=True):
                for key, v in inputs.items():
                    updated = dict(v["cur"])
                    updated.update({
                        "적용대상": GRAD_GROUPS[key],
                        "기준학점": v["credits"],
                        "이수조건": v["condition"],
                    })
                    if v["note"]:
                        updated["비고"] = v["note"]
                    else:
                        updated.pop("비고", None)
                    graph.add_early_graduation(f"기준_{key}", updated)
                graph.save()
                _audit("SAVE_GRAD_CRITERIA")
                st.success("기준학점 저장 완료")

    # ── C. 신청자격 (평점 기준) ─────────────────────
    with st.expander("✅ 신청자격 (평점 기준 · 대상 학기)", expanded=False):
        elig = dict(graph.G.nodes.get("early_grad_신청자격", {}))
        with st.form("form_eligibility"):
            semester_req = st.text_input(
                "신청 가능 학기",
                value=elig.get("신청학기", "6학기 또는 7학기 등록 재학생"),
            )
            st.markdown("**평점평균 기준**")
            c1, c2, c3 = st.columns(3)
            with c1:
                gpa_2005 = st.text_input("2005학번 이전", value=elig.get("평점기준_2005이전", "4.0 이상"))
            with c2:
                gpa_2006 = st.text_input("2006학번",      value=elig.get("평점기준_2006",     "4.2 이상"))
            with c3:
                gpa_2007 = st.text_input("2007학번 이후", value=elig.get("평점기준_2007이후", "4.3 이상"))
            global_college = st.text_input("글로벌미래융합학부", value=elig.get("글로벌미래융합학부", "별도기준 적용"))
            no_transfer    = st.checkbox("편입생 신청 불가", value=bool(elig.get("편입생_신청불가", True)))

            if st.form_submit_button("신청자격 저장", use_container_width=True):
                updated_elig = dict(elig)
                updated_elig.update({
                    "신청학기": semester_req,
                    "평점기준_2005이전": gpa_2005,
                    "평점기준_2006":     gpa_2006,
                    "평점기준_2007이후": gpa_2007,
                    "글로벌미래융합학부": global_college,
                    "편입생_신청불가":   no_transfer,
                })
                graph.add_early_graduation("신청자격", updated_elig)
                graph.save()
                _audit("SAVE_ELIGIBILITY")
                st.success("신청자격 저장 완료")

    # ── D. 기타사항 ─────────────────────────────────
    with st.expander("📌 기타사항 (탈락자·합격자·7학기 주의)", expanded=False):
        notes = dict(graph.G.nodes.get("early_grad_기타사항", {}))
        with st.form("form_notes"):
            dropout  = st.text_area("탈락자 처리",       value=notes.get("탈락자처리",   "전어학기 등록금 납부, 수강신청 및 학점이수 필수"), height=80)
            pass_note = st.text_area("합격자 졸업유예 신청", value=notes.get("합격자졸업유예", "신청 불가 (졸업합격자로 유예대상 아님)"),         height=80)
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
                updated_notes.update({
                    "탈락자처리":   dropout,
                    "합격자졸업유예": pass_note,
                    "7학기등록주의": sem7_note,
                })
                graph.add_early_graduation("기타사항", updated_notes)
                graph.save()
                _audit("SAVE_NOTES")
                st.success("기타사항 저장 완료")


# ════════════════════════════════════════════════════
# Tab 2 : 학사일정 관리
# ════════════════════════════════════════════════════
with tab_schedule:
    st.subheader("학사일정 관리")

    all_schedules = sorted(
        [
            {"id": nid, **data}
            for nid, data in graph.G.nodes(data=True)
            if data.get("type") == "학사일정" and data.get("시작일")
        ],
        key=lambda x: x.get("시작일", ""),
    )

    if all_schedules:
        st.markdown("**현재 등록된 학사일정**")
        st.dataframe(
            [
                {
                    "이벤트명": s.get("이벤트명", ""),
                    "학기":    s.get("학기",    ""),
                    "시작일":  s.get("시작일",  ""),
                    "종료일":  s.get("종료일",  ""),
                    "비고":    s.get("비고",    ""),
                }
                for s in all_schedules
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("등록된 학사일정이 없습니다.")

    st.markdown("---")

    with st.expander("➕ 새 일정 추가", expanded=False):
        with st.form("form_add_schedule"):
            c1, c2 = st.columns(2)
            with c1:
                ev_name     = st.text_input("이벤트명", placeholder="예: 수강신청")
                ev_semester = st.text_input("학기",     placeholder="예: 2026-1")
            with c2:
                ev_start = st.date_input("시작일", key="sched_s")
                ev_end   = st.date_input("종료일", key="sched_e")
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
                    _audit("ADD_SCHEDULE", f"{ev_name} ({ev_semester})")
                    st.success(f"일정 추가 완료: {ev_name} ({ev_semester})")
                    st.session_state.pop("admin_graph", None)
                    st.rerun()
                else:
                    st.error("이벤트명과 학기를 입력하세요.")

    with st.expander("✏️ 기존 일정 날짜 수정", expanded=False):
        if all_schedules:
            options = {f"{s['이벤트명']} ({s['학기']})": s for s in all_schedules}
            chosen_label = st.selectbox("수정할 일정 선택", list(options.keys()))
            chosen       = options[chosen_label]

            with st.form("form_edit_schedule"):
                from datetime import date as _date

                def _parse(d: str) -> _date:
                    try:
                        y, m, day = d.split("-")
                        return _date(int(y), int(m), int(day))
                    except Exception:
                        return _date.today()

                c1, c2 = st.columns(2)
                with c1:
                    edit_start = st.date_input("시작일", value=_parse(chosen.get("시작일", "")), key="edit_s")
                with c2:
                    edit_end   = st.date_input("종료일", value=_parse(chosen.get("종료일", "")), key="edit_e")
                edit_note = st.text_input("비고", value=chosen.get("비고", ""))

                if st.form_submit_button("일정 수정 저장", use_container_width=True):
                    updated_sched = dict(chosen)
                    updated_sched["시작일"] = edit_start.strftime("%Y-%m-%d")
                    updated_sched["종료일"] = edit_end.strftime("%Y-%m-%d")
                    if edit_note:
                        updated_sched["비고"] = edit_note
                    graph.add_schedule(
                        chosen.get("이벤트명", ""),
                        chosen.get("학기",    ""),
                        updated_sched,
                    )
                    graph.save()
                    _audit("EDIT_SCHEDULE", f"{chosen.get('이벤트명')} ({chosen.get('학기')})")
                    st.success("일정 수정 완료")
                    st.session_state.pop("admin_graph", None)
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

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("전체 노드", graph.G.number_of_nodes())
    mc2.metric("전체 엣지", graph.G.number_of_edges())
    mc3.metric("조기졸업 노드", type_counts.get("조기졸업", 0))
    mc4.metric("학사일정 노드", type_counts.get("학사일정", 0))

    st.markdown("---")

    st.markdown("**채팅 세션에 변경사항 반영**")
    st.caption("그래프 저장 후 이 버튼을 누르면, 채팅 페이지에서 다음 질문 시 그래프를 새로 로드합니다.")
    if st.button("♻️ 채팅 세션 초기화", type="primary"):
        st.session_state.pop("initialized", None)
        _audit("CHAT_SESSION_RESET")
        st.success("채팅 세션 초기화 완료. 채팅 페이지 이동 시 변경된 그래프가 자동 로드됩니다.")

    st.markdown("---")

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
                    for k, v in data.items():
                        if k not in ("type", "구분"):
                            st.markdown(f"**{k}**: {v}")
        else:
            st.warning("조기졸업 노드가 없습니다.")

    st.markdown("---")

    # ── 감사 로그 최근 20줄 표시 ─────────────────────
    st.markdown("**최근 감사 로그**")
    log_path = Path(settings.graph.graph_path).parent.parent / "logs" / "admin_audit.log"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").splitlines()
        recent = "\n".join(lines[-20:]) if lines else "(로그 없음)"
        st.code(recent, language=None)
    else:
        st.caption("아직 감사 로그가 없습니다.")

    st.caption(f"그래프 파일: `{settings.graph.graph_path}`")
