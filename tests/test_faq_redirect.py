"""
리다이렉트 FAQ 판정 단위 테스트.

원칙 1(스키마 진화): 휴리스틱(텍스트 마커) + 선언적 플래그(answer_type)가
데이터로부터 자동 유도된다.
원칙 2(비용·지연): 문자열 탐색 + 정규식 1회 → ms 단위.

회귀 방지 대상:
- "2020학번 졸업요건 알려줘"에 FAQ-0017("졸업 요건은 어디서 확인할 수 있어?")
  의 리다이렉트성 답("...참고하시기 바랍니다. 통합정보시스템 > ...")이 direct_answer
  로 주입돼 PDF의 "130학점" 데이터를 가리는 버그.
"""

from app.graphdb.academic_graph import _is_redirect_answer


# ── 휴리스틱 경로 ────────────────────────────────────────────

def test_redirect_marker_only_classified_as_redirect():
    """리다이렉트 마커만 있고 구체 수치가 없으면 redirect."""
    answer = (
        "매학기 시간표의 교육과정 및 교육과정기본이수표의 해당 학번의 "
        "졸업기준을 참고하시기 바랍니다. 통합정보시스템 > 졸업 > 학업성적사정표"
    )
    assert _is_redirect_answer(answer, {}) is True


def test_answer_with_concrete_credits_is_not_redirect():
    """안내 + 구체 데이터(130학점)가 섞이면 data로 취급."""
    answer = (
        "2020학번 졸업학점은 130학점 이상입니다. "
        "자세한 사항은 학사안내 참고하시기 바랍니다."
    )
    assert _is_redirect_answer(answer, {}) is False


def test_short_data_answer_not_redirect():
    """짧지만 구체 수치가 있는 답은 data."""
    assert _is_redirect_answer("130학점 이상입니다.", {}) is False


def test_pure_data_answer_no_marker_not_redirect():
    """마커가 전혀 없으면 redirect 아님."""
    assert _is_redirect_answer("130학점 이상입니다.", {}) is False
    assert _is_redirect_answer("2026년 8월 21일 예정입니다.", {}) is False


def test_date_data_overrides_marker():
    """날짜(YYYY년 MM월 DD일)가 있으면 안내문이어도 data."""
    answer = (
        "2026학년도 후기 졸업식 : 2026.08.21.(금) 예정. "
        "자세한 사항은 각 학부(과) 사무실에 문의하시기 바랍니다."
    )
    assert _is_redirect_answer(answer, {}) is False


def test_marker_without_data_is_redirect():
    """다른 마커(문의)도 리다이렉트로 감지."""
    answer = (
        "해당 내용은 각 학부(과) 사무실로 문의하시기 바랍니다. "
        "자세한 내용은 담당자에게 확인해 주세요."
    )
    assert _is_redirect_answer(answer, {}) is True


# ── 선언적 override ──────────────────────────────────────────

def test_explicit_answer_type_redirect_flag():
    """answer_type: redirect 플래그만 있어도 redirect로 취급."""
    assert _is_redirect_answer("아무 답", {"answer_type": "redirect"}) is True


def test_explicit_answer_type_data_overrides_heuristic():
    """answer_type: data로 선언하면 휴리스틱 무시."""
    answer = (
        "통합정보시스템 > 성적 메뉴에서 참고하시기 바랍니다."
    )
    assert _is_redirect_answer(answer, {"answer_type": "data"}) is False


# ── 엣지 케이스 ──────────────────────────────────────────────

def test_empty_answer_not_redirect():
    assert _is_redirect_answer("", {}) is False


def test_none_metadata_safe():
    assert _is_redirect_answer("130학점 이상입니다.", None) is False


# ── search_faq 통합: 격리된 임시 그래프로 테스트 ────────────────

def _make_isolated_graph(tmp_path):
    """pickle 로드를 피하기 위해 빈 임시 그래프 인스턴스 생성."""
    from app.graphdb.academic_graph import AcademicGraph
    empty_path = tmp_path / "empty_graph.pkl"
    return AcademicGraph(graph_path=str(empty_path))


def test_search_faq_skips_direct_answer_for_redirect_faq(tmp_path):
    """FAQ 노드에 answer_type=redirect가 달려 있으면 search_faq가 direct_answer를
    metadata에 넣지 않아야 한다 (PDF의 구체 데이터가 가려지는 것을 방지)."""
    g = _make_isolated_graph(tmp_path)
    g.add_faq_node(
        faq_id="FAQ-REDIRECT-TEST",
        question="졸업 요건은 어디서 확인할 수 있어?",
        answer=(
            "매학기 시간표의 교육과정 및 교육과정기본이수표의 해당 학번의 "
            "졸업기준을 참고하시기 바랍니다. 통합정보시스템 > 졸업 > 학업성적사정표"
        ),
        category="성적/시험",
        metadata={"answer_type": "redirect"},
    )

    results = g.search_faq("2020학번 졸업요건 알려줘", top_k=3)
    assert results, "FAQ가 매칭되어 검색 결과에는 포함되어야 함"
    top = results[0]
    # 리다이렉트 FAQ는 컨텍스트에는 있어도 direct_answer로 쓰이면 안 됨
    assert "direct_answer" not in top.metadata, (
        "리다이렉트 FAQ에 direct_answer가 달려 PDF 데이터를 가릴 위험"
    )
    assert top.metadata.get("answer_type") == "redirect"


def test_search_faq_assigns_direct_answer_for_data_faq(tmp_path):
    """일반 데이터 FAQ는 기존처럼 direct_answer가 부여돼야 한다 (회귀 방지)."""
    g = _make_isolated_graph(tmp_path)
    g.add_faq_node(
        faq_id="FAQ-DATA-TEST",
        question="제2전공으로 교직신청 가능한가요?",
        answer="복수전공·부전공으로 교직신청은 불가능합니다. 주전공 교직신청만 이수 가능합니다.",
        category="교직",
    )

    results = g.search_faq("제2전공으로 교직신청 가능한가요?", top_k=3)
    assert results
    top = results[0]
    # 강한 매칭이면 direct_answer가 달려야 함
    assert top.metadata.get("direct_answer"), (
        f"일반 FAQ에 direct_answer가 누락됨. metadata={top.metadata}"
    )
    # 리다이렉트 마커가 없으므로 answer_type=redirect가 아니어야 함
    assert top.metadata.get("answer_type") != "redirect"


def test_search_faq_heuristic_classifies_unflagged_redirect(tmp_path):
    """answer_type 플래그가 없어도 '참고하시기 바랍니다'만 있는 답은
    휴리스틱이 자동으로 redirect로 분류해야 한다."""
    g = _make_isolated_graph(tmp_path)
    g.add_faq_node(
        faq_id="FAQ-AUTO-REDIRECT",
        question="성적정정 요령 어디서 볼 수 있어?",
        answer=(
            "각 학부(과) 사무실에 문의하시기 바랍니다. "
            "자세한 사항은 학사지원팀으로 연락 바랍니다."
        ),
        category="성적/시험",
    )

    results = g.search_faq("성적정정 요령", top_k=3)
    if results:
        top = results[0]
        assert "direct_answer" not in top.metadata, (
            "휴리스틱이 리다이렉트 FAQ를 감지하지 못함"
        )
