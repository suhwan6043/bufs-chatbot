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
import json
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
# 그래프 로더 (동시 접속 대응)
# ════════════════════════════════════════════════════
def _get_graph() -> AcademicGraph:
    """
    세션 캐시 그래프를 반환합니다.
    디스크 파일이 다른 세션에서 갱신되었으면 자동으로 다시 로드합니다.
    """
    cached: AcademicGraph | None = st.session_state.get("admin_graph")
    if cached is None or cached.is_stale():
        st.session_state.admin_graph = AcademicGraph()
    return st.session_state.admin_graph


def _save_graph(mutate_fn, audit_action: str = "", audit_detail: str = ""):
    """
    reload-merge-save 패턴으로 안전하게 그래프를 저장합니다.

    동시 접속 시 다른 세션의 변경이 사라지지 않도록:
      1. 디스크에서 최신 그래프를 다시 로드
      2. mutate_fn(fresh_graph) 로 원하는 변경만 적용
      3. 저장
      4. 세션 캐시 갱신

    사용 예:
        def _apply(g):
            g.add_graduation_req("2024_2025", "내국인", data)
        _save_graph(_apply, "SAVE_GRAD_REQ", "group=2024_2025")
    """
    fresh = AcademicGraph()      # 최신 디스크 상태
    mutate_fn(fresh)             # 변경 적용
    fresh.save()                 # 원자적 저장
    st.session_state.admin_graph = fresh  # 세션 캐시 갱신
    if audit_action:
        _audit(audit_action, audit_detail)


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
tab_grad, tab_early, tab_schedule, tab_status, tab_crawler, tab_history, tab_contacts = st.tabs([
    "📋 졸업요건 관리",
    "🎓 조기졸업 관리",
    "📅 학사일정 관리",
    "📊 그래프 현황",
    "🕷️ 크롤러 관리",
    "📋 크롤 히스토리",
    "📞 연락처 관리",
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

        _grp, _stype, _mjr, _nd = sel_group, sel_type, sel_major, new_data
        _save_graph(
            lambda g: g.add_graduation_req(_grp, _stype, _nd, major=_mjr),
            "SAVE_GRAD_REQ",
            f"group={sel_group}, type={sel_type}" + (f", major={sel_major}" if sel_major else ""),
        )
        st.success(
            f"저장 완료: **{disp_label}**  \n"
            f"채팅에 반영하려면 [그래프 현황] 탭 → '채팅 세션 초기화' 버튼을 누르세요."
        )
        st.rerun()

    # ── 학과별 졸업인증 연동 편집 ─────────────────────────────────
    if sel_major:
        st.markdown("---")
        st.markdown("#### 📝 학과별 졸업인증 요건")
        st.caption(
            f"**{sel_major_label}** 학과 노드에 저장된 졸업시험·졸업인증 요건을 편집합니다.  \n"
            "학번 그룹/학생유형과 무관하게 학과 단위로 관리됩니다."
        )

        # ── 학과 노드 탐색 ──────────────────────────────────────────
        def _find_cert_node(g, major: str) -> str | None:
            """전공명으로 학과전공 노드 ID를 찾습니다 (최장 매칭 우선)."""
            for candidate in (f"dept_{major}전공", f"dept_{major}"):
                if candidate in g.G.nodes:
                    return candidate
            # 부분 매칭: 한국어 정규화 후 점수 계산 → 가장 긴 매칭 선택
            mj = major.replace("어", "").replace(" ", "")
            best_nid, best_score = None, 0
            for nid, d in g.G.nodes(data=True):
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
                    score = 30 + len(nm_norm)   # 더 긴 노드명일수록 높은 점수
                if score > best_score:
                    best_score, best_nid = score, nid
            return best_nid

        cert_nid = _find_cert_node(graph, sel_major)

        if cert_nid is None:
            st.warning(
                f"**{sel_major}** 에 해당하는 학과 노드를 찾을 수 없습니다.  \n"
                "학과 노드가 없으면 저장 시 새로 생성됩니다.",
                icon="⚠️",
            )
            cert_nid = f"dept_{sel_major}전공"
            cert_cur: dict = {}
        else:
            cert_cur = {
                k: v for k, v in dict(graph.G.nodes[cert_nid]).items()
                if k in ("졸업시험_요건", "졸업시험_과목", "졸업시험_합격기준", "졸업시험_대체방법")
            }
            if any(cert_cur.values()):
                st.success(f"기존 졸업인증 데이터 로드: **{cert_nid}**", icon="✅")
            else:
                st.info("졸업인증 데이터가 아직 없습니다. 아래에서 입력 후 저장하세요.", icon="ℹ️")

        with st.form("form_dept_cert"):
            f_cert_req = st.text_area(
                "졸업시험·졸업인증 요건 (전체 요약)",
                value=cert_cur.get("졸업시험_요건", "") or "",
                height=120,
                placeholder="예: 졸업시험(정보보호개론, 암호론) 70점 이상 합격. 자격증으로 대체 가능.",
            )
            c1, c2 = st.columns(2)
            with c1:
                f_cert_subj = st.text_input(
                    "시험 과목",
                    value=cert_cur.get("졸업시험_과목", "") or "",
                    placeholder="예: 정보보호개론, 암호론",
                )
            with c2:
                f_cert_pass = st.text_input(
                    "합격 기준",
                    value=cert_cur.get("졸업시험_합격기준", "") or "",
                    placeholder="예: 70점 이상 / 평균 70점 이상 (과락 50점)",
                )
            f_cert_alt = st.text_area(
                "대체 방법",
                value=cert_cur.get("졸업시험_대체방법", "") or "",
                height=80,
                placeholder="예: 정보처리기사 자격증 / 취업박람회 참가 등",
            )
            cert_saved = st.form_submit_button(
                "💾 졸업인증 저장", type="primary", use_container_width=True
            )

        if cert_saved:
            _cnid = cert_nid
            _cmjr = sel_major
            _cert_attrs = {
                "졸업시험_요건":    f_cert_req.strip(),
                "졸업시험_과목":    f_cert_subj.strip(),
                "졸업시험_합격기준": f_cert_pass.strip(),
                "졸업시험_대체방법": f_cert_alt.strip(),
            }

            def _apply_cert(g):
                if _cnid not in g.G.nodes:
                    g.G.add_node(_cnid, type="학과전공", 전공명=f"{_cmjr}전공")
                g.G.nodes[_cnid].update(_cert_attrs)

            _save_graph(_apply_cert, "SAVE_DEPT_CERT", f"node={cert_nid}")
            st.success(
                f"저장 완료: **{cert_nid}**  \n"
                "채팅에 반영하려면 [그래프 현황] 탭 → '채팅 세션 초기화' 버튼을 누르세요."
            )
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
                _ns = new_semester
                _sd = {
                    "시작일": new_start.strftime("%Y-%m-%d"),
                    "종료일": new_end.strftime("%Y-%m-%d"),
                    "신청방법": new_method,
                }

                def _apply_early_sched(g):
                    g.add_schedule("조기졸업신청", _ns, _sd)
                    g.add_relation(
                        f"schedule_조기졸업신청_{_ns}",
                        "early_grad_신청자격",
                        "기간정한다",
                    )

                _save_graph(_apply_early_sched, "SAVE_EARLY_SCHEDULE", f"semester={new_semester}")
                st.success(f"신청기간 저장 완료: {new_semester}")
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
                _inputs = {k: dict(v) for k, v in inputs.items()}
                _gg = dict(GRAD_GROUPS)

                def _apply_criteria(g):
                    for key, v in _inputs.items():
                        updated = dict(v["cur"])
                        updated.update({
                            "적용대상": _gg[key],
                            "기준학점": v["credits"],
                            "이수조건": v["condition"],
                        })
                        if v["note"]:
                            updated["비고"] = v["note"]
                        else:
                            updated.pop("비고", None)
                        g.add_early_graduation(f"기준_{key}", updated)

                _save_graph(_apply_criteria, "SAVE_GRAD_CRITERIA")
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
                _elig_data = {
                    "신청학기": semester_req,
                    "평점기준_2005이전": gpa_2005,
                    "평점기준_2006":     gpa_2006,
                    "평점기준_2007이후": gpa_2007,
                    "글로벌미래융합학부": global_college,
                    "편입생_신청불가":   no_transfer,
                }

                def _apply_elig(g):
                    node = dict(g.G.nodes.get("early_grad_신청자격", {}))
                    node.update(_elig_data)
                    g.add_early_graduation("신청자격", node)

                _save_graph(_apply_elig, "SAVE_ELIGIBILITY")
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
                _notes_data = {
                    "탈락자처리":   dropout,
                    "합격자졸업유예": pass_note,
                    "7학기등록주의": sem7_note,
                }

                def _apply_notes(g):
                    node = dict(g.G.nodes.get("early_grad_기타사항", {}))
                    node.update(_notes_data)
                    g.add_early_graduation("기타사항", node)

                _save_graph(_apply_notes, "SAVE_NOTES")
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
                    _en, _es = ev_name, ev_semester
                    _sd2 = {"시작일": ev_start.strftime("%Y-%m-%d"), "종료일": ev_end.strftime("%Y-%m-%d")}
                    if ev_note:
                        _sd2["비고"] = ev_note
                    _save_graph(
                        lambda g: g.add_schedule(_en, _es, _sd2),
                        "ADD_SCHEDULE", f"{ev_name} ({ev_semester})",
                    )
                    st.success(f"일정 추가 완료: {ev_name} ({ev_semester})")
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
                    _us = dict(chosen)
                    _us["시작일"] = edit_start.strftime("%Y-%m-%d")
                    _us["종료일"] = edit_end.strftime("%Y-%m-%d")
                    if edit_note:
                        _us["비고"] = edit_note
                    _en2 = chosen.get("이벤트명", "")
                    _es2 = chosen.get("학기", "")
                    _save_graph(
                        lambda g: g.add_schedule(_en2, _es2, _us),
                        "EDIT_SCHEDULE", f"{_en2} ({_es2})",
                    )
                    st.success("일정 수정 완료")
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


# ════════════════════════════════════════════════════
# Tab 4 : 크롤러 관리
# ════════════════════════════════════════════════════
_CRAWL_META = Path(settings.graph.graph_path).parent.parent / "crawl_meta"
_HASH_FILE  = _CRAWL_META / "content_hashes.json"
_HIST_FILE  = _CRAWL_META / "crawl_history.jsonl"

with tab_crawler:
    st.subheader("크롤러 관리")

    # ── 실시간 스케줄러 상태 ──────────────────────────
    from app.scheduler import get_scheduler
    _sched = get_scheduler()
    _jobs  = _sched.get_jobs_info()

    enabled  = settings.crawler.enabled
    interval = settings.crawler.notice_interval_minutes
    notice_count = len(json.loads(_HASH_FILE.read_text(encoding="utf-8"))) if _HASH_FILE.exists() else 0

    # 다음 실행 시각 (실제 스케줄러에서 조회)
    _next_run = "—"
    for j in _jobs:
        if j.get("id") == "notice_crawl":
            _next_run = j.get("next_run", "—")
            break

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "스케줄러 상태",
        "실행 중" if _sched.is_running() else ("대기" if enabled else "비활성"),
        delta="자동 크롤링 ON" if _sched.is_running() else None,
        delta_color="normal" if _sched.is_running() else "off",
    )
    m2.metric("크롤링 주기", f"{interval}분")
    m3.metric("다음 실행 예정", _next_run if _next_run != "—" else "미설정")
    m4.metric("추적 중인 공지", f"{notice_count}건")

    if not enabled:
        st.info(
            "자동 크롤링이 비활성화되어 있습니다.  \n"
            "`.env` 에 `CRAWLER_ENABLED=true` 를 설정하면 자동 실행됩니다.",
            icon="ℹ️",
        )

    st.divider()

    # ── 수동 즉시 실행 + 해시 초기화 ────────────────
    st.markdown("**공지사항 즉시 수집**")
    st.caption("BUFS 학사공지 게시판을 지금 바로 크롤링하여 변경된 내용을 ChromaDB에 반영합니다.")

    btn_col1, btn_col2 = st.columns([2, 1])
    with btn_col1:
        if st.button("▶ 지금 크롤링 실행", type="primary", use_container_width=True):
            _audit("CRAWL_TRIGGERED", "manual trigger from admin")
            with st.spinner("크롤링 중... (약 30~60초 소요)"):
                _sched.trigger_notice_now()
            st.success("크롤링 완료.")
            st.rerun()

    with btn_col2:
        if st.button("🔄 해시 초기화 (전체 재수집)", use_container_width=True,
                     help="저장된 해시를 지워 다음 크롤링 시 모든 공지를 NEW로 처리합니다."):
            if _HASH_FILE.exists():
                _HASH_FILE.write_text("{}", encoding="utf-8")
            _audit("HASH_RESET", "manual hash reset from admin")
            st.success("해시 초기화 완료. 다음 크롤링 시 전체 재수집됩니다.")
            st.rerun()

    st.divider()

    # ── 전체 재인제스트 ──────────────────────────────
    st.markdown("**전체 재인제스트**")
    st.caption(
        "공지사항 청크(notice · notice_attachment)를 ChromaDB에서 전부 삭제한 뒤, "
        "최신 청킹 로직(is_table=False 등)으로 즉시 재생성합니다. "
        "졸업요건·시간표 등 수동 인제스트 데이터는 유지됩니다."
    )

    if st.button(
        "♻️ 전체 재인제스트 실행",
        type="primary",
        use_container_width=True,
        help="공지 청크를 모두 삭제하고 최신 청킹 로직으로 다시 생성합니다.",
    ):
        _audit("FULL_REINGEST_TRIGGERED", "manual full re-ingest from admin")

        progress = st.progress(0, text="ChromaDB 공지 청크 삭제 중…")

        from app.shared_resources import get_chroma_store as _get_chroma
        _chroma = _get_chroma()

        # 1단계: notice / notice_attachment 청크 삭제
        deleted_notice = _chroma.delete_all_by_doc_type("notice")
        progress.progress(33, text=f"notice 청크 {deleted_notice}개 삭제 완료…")

        deleted_attach = _chroma.delete_all_by_doc_type("notice_attachment")
        progress.progress(55, text=f"notice_attachment 청크 {deleted_attach}개 삭제 완료…")

        # 2단계: 해시 초기화 (다음 크롤링 시 모두 NEW로 처리)
        if _HASH_FILE.exists():
            _HASH_FILE.write_text("{}", encoding="utf-8")
        progress.progress(65, text="해시 초기화 완료… 크롤링 시작 중…")

        # 3단계: 즉시 크롤링 실행 (최신 청킹 로직으로 재인제스트)
        _sched.trigger_notice_now()
        progress.progress(100, text="완료")

        _audit(
            "FULL_REINGEST_DONE",
            f"deleted notice={deleted_notice} attach={deleted_attach}, crawl triggered",
        )
        st.success(
            f"재인제스트 완료: notice {deleted_notice}개 · notice_attachment {deleted_attach}개 삭제 후 재수집."
        )
        st.rerun()

    st.divider()

    # ── 수집된 공지 목록 ──────────────────────────────
    st.markdown("**현재 추적 중인 공지 목록**")

    if _HASH_FILE.exists():
        hashes: dict = json.loads(_HASH_FILE.read_text(encoding="utf-8"))
        if hashes:
            rows = []
            for url, val in sorted(
                hashes.items(),
                key=lambda x: x[1].get("metadata", {}).get("post_date", ""),
                reverse=True,
            ):
                meta = val.get("metadata", {})
                rows.append({
                    "제목":       val.get("title", ""),
                    "게시일":     meta.get("post_date", ""),
                    "학기":       meta.get("semester", ""),
                    "최초 수집":  val.get("first_seen", "")[:10],
                    "최근 확인":  val.get("last_seen",  "")[:10],
                    "URL":        url,
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("수집된 공지가 없습니다. '지금 크롤링 실행'을 눌러 수집하세요.")
    else:
        st.info("아직 크롤링을 실행한 적이 없습니다.")

    st.divider()

    # ── 첨부파일 다운로드 현황 ────────────────────────
    st.markdown("**첨부파일 다운로드 현황**")

    from app.config import DATA_DIR as _DATA_DIR
    _attach_dirs = {
        "PDF": _DATA_DIR / "pdfs" / "crawled",
        "HWP": _DATA_DIR / "attachments" / "hwp",
        "기타": _DATA_DIR / "attachments" / "other",
    }

    af1, af2, af3 = st.columns(3)
    for col, (label, adir) in zip([af1, af2, af3], _attach_dirs.items()):
        files = list(adir.glob("*")) if adir.exists() else []
        files = [f for f in files if f.is_file()]
        total_kb = sum(f.stat().st_size for f in files) // 1024
        col.metric(f"{label} 파일", f"{len(files)}개", delta=f"{total_kb}KB")

    # 파일 목록 expander
    all_files = []
    for label, adir in _attach_dirs.items():
        if adir.exists():
            for f in sorted(adir.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.is_file():
                    all_files.append({
                        "종류": label,
                        "파일명": f.name,
                        "크기(KB)": round(f.stat().st_size / 1024, 1),
                        "다운로드": f.stat().st_mtime,
                    })

    if all_files:
        with st.expander(f"첨부파일 목록 ({len(all_files)}개)", expanded=False):
            for f in all_files:
                f["다운로드"] = datetime.fromtimestamp(f["다운로드"]).strftime("%Y-%m-%d %H:%M")
            st.dataframe(all_files, use_container_width=True, hide_index=True)
    else:
        st.caption("다운로드된 첨부파일이 없습니다.")


# ════════════════════════════════════════════════════
# Tab 5 : 크롤 히스토리
# ════════════════════════════════════════════════════
with tab_history:
    st.subheader("크롤 히스토리")

    if _HIST_FILE.exists():
        raw_lines = _HIST_FILE.read_text(encoding="utf-8").strip().splitlines()
        if raw_lines:
            records = [json.loads(l) for l in raw_lines if l.strip()]
            records.reverse()  # 최신순

            # ── 요약 지표 ──────────────────────────────
            last = records[0]
            h1, h2, h3, h4 = st.columns(4)
            h1.metric("총 실행 횟수", len(records))
            h2.metric("마지막 실행", last.get("timestamp", "")[:16])
            h3.metric("마지막 추가", f"{last.get('added', 0) + last.get('updated', 0)}건")
            h4.metric("마지막 오류", f"{len(last.get('errors', []))}건",
                      delta_color="inverse" if last.get("errors") else "off")

            st.divider()

            # ── 이력 테이블 ────────────────────────────
            st.markdown("**실행 이력 (최신순)**")
            rows = []
            for r in records[:20]:
                err_count = len(r.get("errors", []))
                rows.append({
                    "시각":   r.get("timestamp", "")[:16],
                    "잡ID":   r.get("job_id", ""),
                    "추가":   r.get("added", 0),
                    "수정":   r.get("updated", 0),
                    "삭제":   r.get("deleted", 0),
                    "건너뜀": r.get("skipped", 0),
                    "오류":   err_count,
                    "소요(초)": round(r.get("duration_ms", 0) / 1000, 1),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # ── 오류 상세 ──────────────────────────────
            error_records = [r for r in records[:10] if r.get("errors")]
            if error_records:
                st.divider()
                with st.expander(f"⚠️ 최근 오류 상세 ({len(error_records)}건)", expanded=False):
                    for r in error_records:
                        st.markdown(f"**{r.get('timestamp', '')[:16]}**")
                        for e in r.get("errors", []):
                            st.caption(f"- {e[:120]}")
        else:
            st.info("크롤 히스토리가 없습니다.")
    else:
        st.info("아직 크롤링을 실행한 적이 없습니다.")

# ════════════════════════════════════════════════════
with tab_contacts:
    st.subheader("학과/부서 연락처 관리")

    _CONTACTS_FILE = Path(__file__).resolve().parent.parent / "data" / "contacts" / "departments.json"

    try:
        from app.contacts import get_dept_searcher
        searcher = get_dept_searcher()
        flat = searcher._flat

        st.info(f"총 **{len(flat)}개** 학과/부서 항목이 로드되어 있습니다.")

        # ── 연락처 검색 테스트 ──────────────────────────
        st.divider()
        st.markdown("**🔍 연락처 검색 테스트**")
        test_q = st.text_input("질문 입력 (예: 영어학부 전화번호)", key="contact_test_q")
        if test_q:
            results = searcher.search(test_q, top_k=5)
            is_c = searcher.is_contact_query(test_q)
            st.write(f"연락처 쿼리 감지: **{'✅ YES' if is_c else '❌ NO'}**")
            if results:
                rows = [
                    {
                        "부서명": r.name,
                        "단과대학": r.college or "-",
                        "내선번호": r.extension,
                        "전화번호": r.phone,
                        "사무실": r.office or "-",
                        "매칭유형": r.match_type,
                    }
                    for r in results
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.warning("매칭 결과 없음")

        # ── 전체 연락처 목록 ────────────────────────────
        st.divider()
        with st.expander(f"📋 전체 연락처 목록 ({len(flat)}개)", expanded=False):
            rows_all = [
                {
                    "부서명": e["name"],
                    "단과대학": e.get("college") or "-",
                    "내선번호": e["extension"],
                    "전화번호": e["phone"],
                    "사무실": e.get("office") or "-",
                }
                for e in flat
            ]
            st.dataframe(rows_all, use_container_width=True, hide_index=True)

        # ── JSON 편집 ────────────────────────────────────
        st.divider()
        st.markdown("**✏️ departments.json 직접 편집**")
        if _CONTACTS_FILE.exists():
            current_json = _CONTACTS_FILE.read_text(encoding="utf-8")
            edited_json = st.text_area(
                "departments.json 내용",
                value=current_json,
                height=400,
                key="contacts_json_editor",
            )
            if st.button("💾 저장", key="save_contacts_json"):
                try:
                    # JSON 유효성 검사
                    json.loads(edited_json)
                    _CONTACTS_FILE.write_text(edited_json, encoding="utf-8")
                    # 싱글턴 리셋 (다음 호출 시 재로드)
                    import app.contacts.dept_search as _ds
                    _ds._searcher = None
                    st.success("✅ departments.json 저장 완료. 다음 검색 시 자동 반영됩니다.")
                    st.rerun()
                except json.JSONDecodeError as e:
                    st.error(f"JSON 형식 오류: {e}")
                except Exception as e:
                    st.error(f"저장 실패: {e}")
        else:
            st.error(f"파일을 찾을 수 없습니다: {_CONTACTS_FILE}")

    except Exception as e:
        st.error(f"연락처 모듈 로드 실패: {e}")
