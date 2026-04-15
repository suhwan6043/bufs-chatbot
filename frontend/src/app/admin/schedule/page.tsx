"use client";
import { useState, useEffect } from "react";
import { useAdmin, type ScheduleEvent } from "@/hooks/useAdmin";

const EMPTY: ScheduleEvent = { event_name: "", semester: "", start_date: "", end_date: "", note: "" };

export default function SchedulePage() {
  const { token, fetchSchedule, addSchedule, updateSchedule } = useAdmin();
  const [events, setEvents] = useState<ScheduleEvent[]>([]);
  const [form, setForm] = useState<ScheduleEvent>(EMPTY);
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [msg, setMsg] = useState("");

  const load = () => fetchSchedule().then((d) => setEvents(d.events)).catch(() => {});
  useEffect(() => { if (token) load(); }, [token]);

  const handleSave = async () => {
    try {
      if (editIdx !== null) await updateSchedule(form);
      else await addSchedule(form);
      setMsg("저장 완료"); setForm(EMPTY); setEditIdx(null); await load();
    } catch (e) { setMsg(`오류: ${e}`); }
  };

  if (!token) return null;

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-lg font-bold text-navy">학사일정 관리</h1>
      {msg && <p className="text-xs text-green-600 bg-green-50 p-2 rounded">{msg}</p>}

      {/* Form */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-3">
        <h2 className="text-sm font-bold">{editIdx !== null ? "일정 수정" : "일정 추가"}</h2>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <input value={form.event_name} onChange={(e) => setForm({ ...form, event_name: e.target.value })} placeholder="이벤트명" className="px-2 py-1.5 border rounded text-sm" />
          <input value={form.semester} onChange={(e) => setForm({ ...form, semester: e.target.value })} placeholder="학기 (2026-1)" className="px-2 py-1.5 border rounded text-sm" />
          <input value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} placeholder="시작일" className="px-2 py-1.5 border rounded text-sm" />
          <input value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} placeholder="종료일" className="px-2 py-1.5 border rounded text-sm" />
          <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="비고" className="px-2 py-1.5 border rounded text-sm" />
        </div>
        <div className="flex gap-2">
          <button onClick={handleSave} className="px-4 py-2 text-sm bg-accent text-white rounded-lg">{editIdx !== null ? "수정" : "추가"}</button>
          {editIdx !== null && <button onClick={() => { setForm(EMPTY); setEditIdx(null); }} className="px-4 py-2 text-sm border rounded-lg">취소</button>}
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted bg-gray-50">
              <th className="px-3 py-2">이벤트</th><th className="px-3 py-2">학기</th>
              <th className="px-3 py-2">시작일</th><th className="px-3 py-2">종료일</th>
              <th className="px-3 py-2">비고</th><th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={i} className="border-b border-border/50 hover:bg-gray-50">
                <td className="px-3 py-2 font-medium">{e.event_name}</td>
                <td className="px-3 py-2 text-text-sub">{e.semester}</td>
                <td className="px-3 py-2 text-text-sub">{e.start_date}</td>
                <td className="px-3 py-2 text-text-sub">{e.end_date}</td>
                <td className="px-3 py-2 text-text-sub">{e.note}</td>
                <td className="px-3 py-2"><button onClick={() => { setForm(e); setEditIdx(i); }} className="text-accent hover:underline">편집</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
