"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAdmin, type FaqItem, type UncoveredCluster } from "@/hooks/useAdmin";

type Tab = "uncovered" | "list" | "create";

export default function FaqAdminPage() {
  const { token, fetchFaqList, fetchUncovered, createFaq, updateFaq, deleteFaq } = useAdmin();

  const [tab, setTab] = useState<Tab>("uncovered");
  const [items, setItems] = useState<FaqItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [clusters, setClusters] = useState<UncoveredCluster[]>([]);
  const [scannedDays, setScannedDays] = useState(7);
  const [totalCandidates, setTotalCandidates] = useState(0);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const [form, setForm] = useState({ question: "", answer: "", category: "", source_question: "" });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "admin" | "academic">("all");
  const [search, setSearch] = useState("");

  const reload = useCallback(async () => {
    setErr(""); setMsg("");
    try {
      const [list, unc] = await Promise.all([
        fetchFaqList(filter),
        fetchUncovered(scannedDays),
      ]);
      setItems(list.items);
      setCategories(list.categories);
      setClusters(unc.clusters);
      setTotalCandidates(unc.total_candidates);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [fetchFaqList, fetchUncovered, filter, scannedDays]);

  useEffect(() => { if (token) reload(); }, [token, reload]);

  const resetForm = () => {
    setForm({ question: "", answer: "", category: categories[0] || "기타", source_question: "" });
    setEditingId(null);
  };

  const handleAnswerThis = (cluster: UncoveredCluster) => {
    setForm({
      question: "",
      answer: "",
      category: categories[0] || "기타",
      source_question: cluster.representative_question,
    });
    setEditingId(null);
    setTab("create");
  };

  const handleEdit = (item: FaqItem) => {
    if (item.source !== "admin") return;
    setForm({
      question: item.question,
      answer: item.answer,
      category: item.category,
      source_question: item.source_question || "",
    });
    setEditingId(item.id);
    setTab("create");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.question.trim() || !form.answer.trim() || !form.category.trim()) {
      setErr("질문·답변·카테고리를 모두 입력하세요.");
      return;
    }
    setLoading(true); setMsg(""); setErr("");
    try {
      if (editingId) {
        await updateFaq(editingId, {
          question: form.question, answer: form.answer, category: form.category,
          source_question: form.source_question || undefined,
        });
        setMsg(`FAQ 수정 완료 (${editingId})`);
      } else {
        const created = await createFaq({
          question: form.question, answer: form.answer, category: form.category,
          source_question: form.source_question || undefined,
        });
        setMsg(`FAQ 추가 완료 (${created.id}) — 다음 채팅부터 바로 반영됩니다.`);
      }
      resetForm();
      await reload();
      setTab("list");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm(`정말로 ${id} 를 삭제하시겠습니까?`)) return;
    setLoading(true); setErr(""); setMsg("");
    try {
      await deleteFaq(id);
      setMsg(`FAQ 삭제 완료 (${id})`);
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase();
    if (!kw) return items;
    return items.filter((it) =>
      it.question.toLowerCase().includes(kw) ||
      it.answer.toLowerCase().includes(kw) ||
      it.category.toLowerCase().includes(kw) ||
      it.id.toLowerCase().includes(kw),
    );
  }, [items, search]);

  if (!token) return null;

  return (
    <div className="p-6 space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-navy">FAQ 관리 (피드백 루프)</h1>
        <button onClick={reload} disabled={loading}
          className="px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-gray-50 disabled:opacity-50">새로고침</button>
      </header>

      {msg && <p className="text-xs text-green-600 bg-green-50 p-2 rounded">{msg}</p>}
      {err && <p className="text-xs text-red-600 bg-red-50 p-2 rounded">오류: {err}</p>}

      <nav className="flex gap-2 border-b border-border">
        {([
          { key: "uncovered", label: `미답변 질의 (${clusters.length})` },
          { key: "list",      label: `FAQ 목록 (${items.length})` },
          { key: "create",    label: editingId ? "FAQ 수정" : "FAQ 추가" },
        ] as { key: Tab; label: string }[]).map((t) => (
          <button key={t.key}
            onClick={() => { setTab(t.key); if (t.key === "create" && !editingId) resetForm(); }}
            className={`px-4 py-2 text-sm transition-colors ${tab === t.key ? "border-b-2 border-accent text-accent font-medium" : "text-text-sub hover:text-navy"}`}>
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "uncovered" && (
        <section className="space-y-3">
          <div className="flex items-center gap-3 text-xs text-text-sub">
            <label>최근
              <select value={scannedDays} onChange={(e) => setScannedDays(Number(e.target.value))}
                className="ml-1 border border-border rounded px-1.5 py-0.5">
                {[3, 7, 14, 30].map((n) => <option key={n} value={n}>{n}일</option>)}
              </select>
            </label>
            <span>총 후보 {totalCandidates}건 · 클러스터 {clusters.length}개</span>
            <span className="text-muted">(거절 문구 또는 rating ≤ 2 · 기존 FAQ stem 커버리지 ≥ 75% 시 자동 제외)</span>
          </div>

          {clusters.length === 0 && (
            <p className="text-sm text-muted bg-white border border-border rounded-lg p-6 text-center">
              탐지된 미답변 질의가 없습니다.
            </p>
          )}

          <ul className="space-y-3">
            {clusters.map((c, idx) => (
              <li key={idx} className="bg-white rounded-xl border border-border p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-xs text-muted mb-1">
                      <span className="font-medium text-accent">{c.count}회 질문됨</span>
                      <span>·</span>
                      <span>최근: {c.last_asked?.replace("T", " ") || "-"}</span>
                    </div>
                    <p className="text-sm font-medium text-navy break-words">{c.representative_question}</p>
                    {c.examples.length > 1 && (
                      <details className="mt-2 text-xs text-text-sub">
                        <summary className="cursor-pointer hover:text-navy">유사 질의 {c.examples.length}건 보기</summary>
                        <ul className="mt-1 space-y-1 pl-4 list-disc">
                          {c.examples.map((ex, i) => (
                            <li key={i}>
                              <span>{ex.question}</span>
                              <span className="ml-2 text-muted">
                                ({ex.timestamp?.slice(0, 10)}
                                {ex.refused ? ", 거절" : ""}
                                {ex.rating ? `, ★${ex.rating}` : ""})
                              </span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                  <button onClick={() => handleAnswerThis(c)}
                    className="shrink-0 px-3 py-1.5 text-xs bg-accent text-white rounded-lg hover:bg-accent/90">
                    답변 작성 →
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {tab === "list" && (
        <section className="space-y-3">
          <div className="flex items-center gap-3">
            <select value={filter} onChange={(e) => setFilter(e.target.value as "all" | "admin" | "academic")}
              className="text-xs border border-border rounded px-2 py-1">
              <option value="all">전체</option>
              <option value="admin">관리자 추가 (ADMIN-*)</option>
              <option value="academic">정식 (FAQ-*)</option>
            </select>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="검색 (질문·답변·ID)"
              className="flex-1 text-xs border border-border rounded px-2 py-1" />
            <span className="text-xs text-muted">{filtered.length} / {items.length}</span>
          </div>

          <div className="bg-white rounded-xl border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-xs text-text-sub">
                <tr>
                  <th className="px-3 py-2 text-left">ID</th>
                  <th className="px-3 py-2 text-left">카테고리</th>
                  <th className="px-3 py-2 text-left">질문</th>
                  <th className="px-3 py-2 text-left w-32">관리</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((it) => (
                  <tr key={it.id} className="border-t border-border hover:bg-gray-50">
                    <td className="px-3 py-2 text-xs">
                      <span className={`inline-block px-1.5 py-0.5 rounded mr-1 text-white text-[10px] ${it.source === "admin" ? "bg-emerald-600" : "bg-slate-500"}`}>
                        {it.source === "admin" ? "관리자" : "정식"}
                      </span>
                      <span className="font-mono text-muted">{it.id}</span>
                    </td>
                    <td className="px-3 py-2 text-xs">{it.category}</td>
                    <td className="px-3 py-2">
                      <div className="text-sm text-navy line-clamp-2">{it.question}</div>
                      {it.source_question && (
                        <div className="text-[11px] text-muted mt-0.5">원문: {it.source_question}</div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {it.source === "admin" ? (
                        <div className="flex gap-1">
                          <button onClick={() => handleEdit(it)}
                            className="px-2 py-1 text-[11px] border border-border rounded hover:bg-gray-100">수정</button>
                          <button onClick={() => handleDelete(it.id)} disabled={loading}
                            className="px-2 py-1 text-[11px] border border-red-200 text-red-500 rounded hover:bg-red-50 disabled:opacity-50">삭제</button>
                        </div>
                      ) : (
                        <span className="text-[11px] text-muted">읽기 전용</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filtered.length === 0 && (
              <p className="p-6 text-sm text-muted text-center">표시할 FAQ가 없습니다.</p>
            )}
          </div>
        </section>
      )}

      {tab === "create" && (
        <section>
          <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-border p-5 space-y-4 max-w-3xl">
            <h2 className="text-sm font-medium text-navy">
              {editingId ? `FAQ 수정: ${editingId}` : "새 FAQ 추가"}
            </h2>

            <label className="block">
              <span className="text-xs text-text-sub">카테고리</span>
              <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="mt-1 w-full border border-border rounded-lg px-2 py-2 text-sm">
                <option value="">카테고리 선택</option>
                {categories.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>

            <label className="block">
              <span className="text-xs text-text-sub">질문 (다듬은 표준 표현)</span>
              <input value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })}
                placeholder="예: 수강 정정 기간은 언제인가요?" required
                className="mt-1 w-full border border-border rounded-lg px-3 py-2 text-sm" />
            </label>

            <label className="block">
              <span className="text-xs text-text-sub">
                학생 원문 질문 (선택) — 검색면에 포함되어 비공식 표현으로도 매칭됩니다
              </span>
              <input value={form.source_question} onChange={(e) => setForm({ ...form, source_question: e.target.value })}
                placeholder="예: 정정 언제까지 돼요ㅠ"
                className="mt-1 w-full border border-border rounded-lg px-3 py-2 text-sm" />
            </label>

            <label className="block">
              <span className="text-xs text-text-sub">답변 (Markdown 지원)</span>
              <textarea value={form.answer} onChange={(e) => setForm({ ...form, answer: e.target.value })}
                rows={10} required
                className="mt-1 w-full border border-border rounded-lg px-3 py-2 text-sm font-mono" />
            </label>

            <div className="flex gap-2">
              <button type="submit" disabled={loading}
                className="px-4 py-2 text-sm bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-50">
                {loading ? "저장 중..." : (editingId ? "수정 저장" : "추가")}
              </button>
              <button type="button" onClick={() => { resetForm(); setTab("list"); }}
                className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-gray-50">취소</button>
            </div>

            <p className="text-[11px] text-muted leading-relaxed">
              <strong>매칭 로직:</strong> 새 학생 질문의 핵심 어근 75% 이상이 FAQ 질문(+원문)·답변에 포함되면 direct_answer 로 채택됩니다.
              추가 즉시 그래프·벡터 증분 반영되어 다음 채팅부터 적용됩니다.
            </p>
          </form>
        </section>
      )}
    </div>
  );
}
