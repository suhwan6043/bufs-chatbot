"use client";
import { useState, useEffect } from "react";
import { useAdmin, type EarlyGradData } from "@/hooks/useAdmin";

export default function EarlyGradPage() {
  const {
    token, fetchEarlyGrad,
    saveEarlyGradSchedule, saveEarlyGradEligibility,
    saveEarlyGradCriteria, saveEarlyGradNotes,
  } = useAdmin();

  const [data, setData] = useState<EarlyGradData | null>(null);
  const [msg, setMsg] = useState("");

  // A. 신청기간
  const [schedForm, setSchedForm] = useState({ semester: "", start_date: "", end_date: "", method: "" });

  // B. 졸업기준 (학번별 기준학점)
  const [criteriaForms, setCriteriaForms] = useState<Record<string, { credits: number; note: string; condition: string }>>({
    "2022이전": { credits: 130, note: "", condition: "" },
    "2023이후": { credits: 120, note: "", condition: "" },
  });

  // C. 신청자격
  const [eligForm, setEligForm] = useState({
    semester_req: "6학기 또는 7학기 등록 재학생",
    gpa_2005: "4.0 이상", gpa_2006: "4.2 이상", gpa_2007: "4.3 이상",
    global_college: "별도기준 적용", no_transfer: true,
  });

  // D. 기타사항
  const [notesForm, setNotesForm] = useState({
    dropout: "전어학기 등록금 납부, 수강신청 및 학점이수 필수",
    pass_note: "신청 불가 (졸업합격자로 유예대상 아님)",
    sem7_note: "7학기 등록 학생은 대상 학기(7학기차) 지정된 신청기간 내에 신청 필수. 기간 내 미신청 시 조기졸업 불가, 해당 학기는 이수 완료 학기로 처리됨",
  });

  const reload = () => fetchEarlyGrad().then(setData).catch(() => {});

  useEffect(() => {
    if (!token) return;
    fetchEarlyGrad().then((d) => {
      setData(d);
      // eligibility 로드
      if (d.eligibility) {
        const e = d.eligibility as Record<string, unknown>;
        setEligForm((prev) => ({
          semester_req: (e["신청학기"] as string) || prev.semester_req,
          gpa_2005: (e["평점기준_2005이전"] as string) || prev.gpa_2005,
          gpa_2006: (e["평점기준_2006"] as string) || prev.gpa_2006,
          gpa_2007: (e["평점기준_2007이후"] as string) || prev.gpa_2007,
          global_college: (e["글로벌미래융합학부"] as string) || prev.global_college,
          no_transfer: e["편입생_신청불가"] != null ? Boolean(e["편입생_신청불가"]) : prev.no_transfer,
        }));
      }
      // criteria 로드
      if (d.criteria?.length) {
        const updated: Record<string, { credits: number; note: string; condition: string }> = { ...criteriaForms };
        for (const c of d.criteria) {
          const key = c.group || "";
          if (key in updated) {
            updated[key] = { credits: c.credits, note: c.note || "", condition: c.condition || "" };
          }
        }
        setCriteriaForms(updated);
      }
      // notes 로드
      if (d.notes) {
        const n = d.notes as Record<string, string>;
        setNotesForm((prev) => ({
          dropout: n["탈락자처리"] || prev.dropout,
          pass_note: n["합격자졸업유예"] || prev.pass_note,
          sem7_note: n["7학기등록주의"] || prev.sem7_note,
        }));
      }
    }).catch(() => {});
  }, [token, fetchEarlyGrad]);

  const showMsg = (text: string) => { setMsg(text); setTimeout(() => setMsg(""), 5000); };

  const handleSchedSave = async () => {
    try { await saveEarlyGradSchedule(schedForm); showMsg("일정 저장 완료"); reload(); } catch (e) { showMsg(`오류: ${e}`); }
  };

  const handleCriteriaSave = async (key: string) => {
    try {
      await saveEarlyGradCriteria({ group: key, ...criteriaForms[key] });
      showMsg(`${key} 기준학점 저장 완료`);
      reload();
    } catch (e) { showMsg(`오류: ${e}`); }
  };

  const handleEligSave = async () => {
    try { await saveEarlyGradEligibility(eligForm); showMsg("신청자격 저장 완료"); reload(); } catch (e) { showMsg(`오류: ${e}`); }
  };

  const handleNotesSave = async () => {
    try { await saveEarlyGradNotes(notesForm); showMsg("기타사항 저장 완료"); reload(); } catch (e) { showMsg(`오류: ${e}`); }
  };

  if (!token || !data) return null;

  const CRITERIA_LABELS: Record<string, string> = { "2022이전": "2022학번 이전", "2023이후": "2023학번 이후" };

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-lg font-bold text-navy">조기졸업 관리</h1>
      <p className="text-xs text-muted">각 섹션을 수정하고 저장 버튼을 누르면 그래프 파일에 즉시 반영됩니다. 저장 후 채팅에 반영하려면 [그래프 현황] → &apos;채팅 세션 초기화&apos; 버튼을 누르세요.</p>
      {msg && <p className="text-xs text-green-600 bg-green-50 p-2 rounded">{msg}</p>}

      {/* A. 신청기간 */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-3">
        <h2 className="text-sm font-bold">A. 신청기간</h2>
        {data.schedules.length > 0 ? (
          <table className="w-full text-xs">
            <thead><tr className="border-b text-left text-muted"><th className="py-1 pr-3">학기</th><th className="py-1 pr-3">시작일</th><th className="py-1 pr-3">종료일</th><th className="py-1">방법</th></tr></thead>
            <tbody>{data.schedules.map((s, i) => (
              <tr key={i} className="border-b border-border/50"><td className="py-1.5 pr-3">{s.semester}</td><td className="py-1.5 pr-3">{s.start_date}</td><td className="py-1.5 pr-3">{s.end_date}</td><td className="py-1.5">{s.method}</td></tr>
            ))}</tbody>
          </table>
        ) : <p className="text-xs text-muted">등록된 일정 없음</p>}

        <details className="mt-2">
          <summary className="text-xs text-accent cursor-pointer">일정 추가/수정</summary>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
            <input value={schedForm.semester} onChange={(e) => setSchedForm({ ...schedForm, semester: e.target.value })} placeholder="학기 (2026-1)" className="px-2 py-1.5 border rounded text-sm" />
            <input value={schedForm.start_date} onChange={(e) => setSchedForm({ ...schedForm, start_date: e.target.value })} placeholder="시작일 (YYYY-MM-DD)" className="px-2 py-1.5 border rounded text-sm" />
            <input value={schedForm.end_date} onChange={(e) => setSchedForm({ ...schedForm, end_date: e.target.value })} placeholder="종료일 (YYYY-MM-DD)" className="px-2 py-1.5 border rounded text-sm" />
            <input value={schedForm.method} onChange={(e) => setSchedForm({ ...schedForm, method: e.target.value })} placeholder="신청방법" className="px-2 py-1.5 border rounded text-sm" />
          </div>
          <button onClick={handleSchedSave} className="mt-2 px-3 py-1.5 text-xs bg-accent text-white rounded-lg">저장</button>
        </details>
      </div>

      {/* B. 졸업기준 (학번별 기준학점) */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-4">
        <h2 className="text-sm font-bold">B. 졸업기준 (학번별 기준학점)</h2>
        {Object.entries(CRITERIA_LABELS).map(([key, label]) => (
          <div key={key} className="border border-border/50 rounded-lg p-3 space-y-2">
            <h3 className="text-xs font-medium">{label}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-muted block mb-1">기준학점 (이상)</label>
                <input type="number" value={criteriaForms[key].credits}
                  onChange={(e) => setCriteriaForms({ ...criteriaForms, [key]: { ...criteriaForms[key], credits: Number(e.target.value) } })}
                  className="w-full px-2 py-1.5 border rounded text-sm" />
              </div>
              <div>
                <label className="text-xs text-muted block mb-1">비고</label>
                <input value={criteriaForms[key].note}
                  onChange={(e) => setCriteriaForms({ ...criteriaForms, [key]: { ...criteriaForms[key], note: e.target.value } })}
                  className="w-full px-2 py-1.5 border rounded text-sm" />
              </div>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">이수조건</label>
              <textarea value={criteriaForms[key].condition}
                onChange={(e) => setCriteriaForms({ ...criteriaForms, [key]: { ...criteriaForms[key], condition: e.target.value } })}
                className="w-full px-2 py-1.5 border rounded text-sm h-16 resize-none"
                placeholder="각 영역별(교양, 전공 등) 이수학점 취득 / 졸업 전공시험(졸업논문) 합격 / 기타 졸업인증 등" />
            </div>
            <button onClick={() => handleCriteriaSave(key)} className="px-3 py-1.5 text-xs bg-accent text-white rounded-lg">{label} 기준학점 저장</button>
          </div>
        ))}
      </div>

      {/* C. 신청자격 (평점 기준) */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-3">
        <h2 className="text-sm font-bold">C. 신청자격 (평점 기준 · 대상 학기)</h2>
        <div>
          <label className="text-xs text-muted block mb-1">신청 가능 학기</label>
          <input value={eligForm.semester_req} onChange={(e) => setEligForm({ ...eligForm, semester_req: e.target.value })}
            className="w-full px-2 py-1.5 border rounded text-sm" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">평점 (2005학번 이전)</label>
            <input value={eligForm.gpa_2005} onChange={(e) => setEligForm({ ...eligForm, gpa_2005: e.target.value })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">평점 (2006학번)</label>
            <input value={eligForm.gpa_2006} onChange={(e) => setEligForm({ ...eligForm, gpa_2006: e.target.value })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">평점 (2007학번 이후)</label>
            <input value={eligForm.gpa_2007} onChange={(e) => setEligForm({ ...eligForm, gpa_2007: e.target.value })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">글로벌미래융합학부</label>
            <input value={eligForm.global_college} onChange={(e) => setEligForm({ ...eligForm, global_college: e.target.value })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div className="flex items-center gap-2 pt-4">
            <input type="checkbox" checked={eligForm.no_transfer} onChange={(e) => setEligForm({ ...eligForm, no_transfer: e.target.checked })} />
            <label className="text-xs">편입생 신청 불가</label>
          </div>
        </div>
        <button onClick={handleEligSave} className="px-4 py-2 text-sm bg-accent text-white rounded-lg">신청자격 저장</button>
      </div>

      {/* D. 기타사항 */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-3">
        <h2 className="text-sm font-bold">D. 기타사항 (탈락자·합격자·7학기 주의)</h2>
        <div>
          <label className="text-xs text-muted block mb-1">탈락자 처리</label>
          <textarea value={notesForm.dropout} onChange={(e) => setNotesForm({ ...notesForm, dropout: e.target.value })}
            className="w-full px-2 py-1.5 border rounded text-sm h-16 resize-none" />
        </div>
        <div>
          <label className="text-xs text-muted block mb-1">합격자 졸업유예 신청</label>
          <textarea value={notesForm.pass_note} onChange={(e) => setNotesForm({ ...notesForm, pass_note: e.target.value })}
            className="w-full px-2 py-1.5 border rounded text-sm h-16 resize-none" />
        </div>
        <div>
          <label className="text-xs text-muted block mb-1">7학기 등록 학생 주의사항</label>
          <textarea value={notesForm.sem7_note} onChange={(e) => setNotesForm({ ...notesForm, sem7_note: e.target.value })}
            className="w-full px-2 py-1.5 border rounded text-sm h-20 resize-none" />
        </div>
        <button onClick={handleNotesSave} className="px-4 py-2 text-sm bg-accent text-white rounded-lg">기타사항 저장</button>
      </div>
    </div>
  );
}
