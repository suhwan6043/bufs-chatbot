"use client";
import { useState, useEffect } from "react";
import { useAdmin, type ContactEntry } from "@/hooks/useAdmin";

export default function ContactsPage() {
  const { token, fetchContacts, searchContacts, fetchContactsJson, saveContactsJson } = useAdmin();
  const [entries, setEntries] = useState<ContactEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ContactEntry[] | null>(null);
  const [isContactQuery, setIsContactQuery] = useState<boolean | null>(null);
  const [msg, setMsg] = useState("");

  // JSON 편집
  const [showJson, setShowJson] = useState(false);
  const [jsonContent, setJsonContent] = useState("");
  const [jsonLoaded, setJsonLoaded] = useState(false);

  useEffect(() => { if (token) fetchContacts().then((d) => { setEntries(d.entries); setTotal(d.total); }).catch(() => {}); }, [token, fetchContacts]);

  const handleSearch = async () => {
    if (!query.trim()) { setResults(null); setIsContactQuery(null); return; }
    try {
      const r = await searchContacts(query);
      setResults(r.results);
      setIsContactQuery(r.is_contact_query);
    } catch {
      setResults([]);
      setIsContactQuery(null);
    }
  };

  const handleLoadJson = async () => {
    try {
      const d = await fetchContactsJson();
      setJsonContent(d.json_content);
      setJsonLoaded(true);
    } catch (e) { setMsg(`JSON 로드 실패: ${e}`); }
  };

  const handleSaveJson = async () => {
    try {
      JSON.parse(jsonContent); // 유효성 검사
    } catch (e) {
      setMsg(`JSON 형식 오류: ${e}`);
      return;
    }
    try {
      await saveContactsJson(jsonContent);
      setMsg("departments.json 저장 완료. 다음 검색 시 자동 반영됩니다.");
      fetchContacts().then((d) => { setEntries(d.entries); setTotal(d.total); });
    } catch (e) { setMsg(`저장 실패: ${e}`); }
  };

  if (!token) return null;

  const showingResults = results !== null;
  const displayEntries = showingResults ? results : entries;

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-lg font-bold text-navy">연락처 관리 ({total}건)</h1>

      {/* Search */}
      <div className="space-y-2">
        <div className="flex gap-2">
          <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="질문 입력 (예: 영어학부 전화번호)" className="px-3 py-2 border border-border rounded-lg text-sm w-60" />
          <button onClick={handleSearch} className="px-4 py-2 text-sm bg-accent text-white rounded-lg hover:bg-accent/90">검색</button>
          {showingResults && <button onClick={() => { setResults(null); setIsContactQuery(null); }} className="px-3 py-2 text-sm border border-border rounded-lg">전체 보기</button>}
        </div>
        {isContactQuery !== null && (
          <div className="text-xs">
            연락처 쿼리 감지: <span className={isContactQuery ? "text-green-600 font-medium" : "text-red-500 font-medium"}>
              {isContactQuery ? "YES" : "NO"}
            </span>
          </div>
        )}
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted bg-gray-50">
              <th className="px-3 py-2">이름/학과</th><th className="px-3 py-2">소속</th>
              <th className="px-3 py-2">내선</th><th className="px-3 py-2">전화</th><th className="px-3 py-2">사무실</th>
              {showingResults && <th className="px-3 py-2">매칭유형</th>}
            </tr>
          </thead>
          <tbody>
            {displayEntries.map((c, i) => (
              <tr key={i} className="border-b border-border/50 hover:bg-gray-50">
                <td className="px-3 py-2 font-medium">{c.name}</td>
                <td className="px-3 py-2 text-text-sub">{c.college}</td>
                <td className="px-3 py-2 text-text-sub">{c.extension}</td>
                <td className="px-3 py-2 text-text-sub">{c.phone}</td>
                <td className="px-3 py-2 text-text-sub">{c.office}</td>
                {showingResults && <td className="px-3 py-2 text-text-sub">{c.match_type || "-"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {msg && <p className="text-xs text-green-600 bg-green-50 p-2 rounded">{msg}</p>}

      {/* JSON 직접 편집 */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5">
        <button onClick={() => { setShowJson(!showJson); if (!jsonLoaded) handleLoadJson(); }}
          className="text-sm text-accent hover:underline">
          {showJson ? "▼" : "▶"} departments.json 직접 편집
        </button>
        {showJson && (
          <div className="mt-3 space-y-3">
            <textarea value={jsonContent} onChange={(e) => setJsonContent(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-lg text-xs font-mono h-80 resize-y" />
            <button onClick={handleSaveJson} className="px-4 py-2 text-sm bg-accent text-white rounded-lg hover:bg-accent/90">
              저장
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
