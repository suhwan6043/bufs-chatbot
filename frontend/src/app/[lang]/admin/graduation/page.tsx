"use client";
import { useState, useEffect, use, useCallback } from "react";
import { useAdmin, type GradRow, type GradOptions, type DeptCertData } from "@/hooks/useAdmin";

export default function GraduationPage({ params }: { params: Promise<{ lang: string }> }) {
  use(params);
  const {
    token, fetchGraduation, saveGraduation,
    fetchGradOptions, fetchDeptCert, saveDeptCert,
  } = useAdmin();

  const [rows, setRows] = useState<GradRow[]>([]);
  const [options, setOptions] = useState<GradOptions | null>(null);
  const [msg, setMsg] = useState("");
  const [showOverview, setShowOverview] = useState(false);

  // 선택 상태
  const [selGroup, setSelGroup] = useState("");
  const [selType, setSelType] = useState("내국인");
  const [selMajor, setSelMajor] = useState<string | null>(null); // null = 공통

  // 폼 필드
  const [form, setForm] = useState({
    credits: 120, liberal: 30, global_comm: 6,
    community: "2학점", exam_bool: false, cert: "",
    nomad: "", career_explore: "", major_explore: "",
    second_major_method: "",
    double_major: "", fusion_major: "", micro_major: "", minor_major: "",
  });

  // 학과별 졸업인증
  const [certData, setCertData] = useState<DeptCertData | null>(null);
  const [certForm, setCertForm] = useState({
    cert_requirement: "", cert_subjects: "",
    cert_pass_criteria: "", cert_alternative: "",
  });

  // 전공 옵션 빌드
  const majorOptions = useCallback(() => {
    if (!options) return [];
    const items: { label: string; value: string | null }[] = [{ label: "공통 (전공무관)", value: null }];
    for (const [dept, majors] of Object.entries(options.dept_tree)) {
      for (const m of majors) {
        items.push({ label: `${dept} › ${m}`, value: m });
      }
    }
    return items;
  }, [options]);

  // 로드
  useEffect(() => {
    if (!token) return;
    fetchGraduation().then((d) => setRows(d.rows)).catch(() => {});
    fetchGradOptions().then((o) => {
      setOptions(o);
      if (o.groups && !selGroup) setSelGroup(Object.keys(o.groups)[0] || "");
    }).catch(() => {});
  }, [token, fetchGraduation, fetchGradOptions]);

  // 선택 변경 시 기존 데이터 로드
  useEffect(() => {
    if (!selGroup || !rows.length) return;
    const match = rows.find((r) =>
      r.group === selGroup && r.student_type === selType &&
      (selMajor ? r.major === selMajor : (r.major === "공통" || r.major === ""))
    );
    if (match) {
      setForm({
        credits: typeof match.credits === "number" ? match.credits : 120,
        liberal: typeof match.liberal === "number" ? match.liberal : 30,
        global_comm: typeof match.global_comm === "number" ? match.global_comm : 6,
        community: match.community || "2학점",
        exam_bool: match.exam_bool || false,
        cert: match.cert === "-" ? "" : match.cert || "",
        nomad: match.nomad || "",
        career_explore: match.career_explore != null ? String(match.career_explore) : "",
        major_explore: match.major_explore != null ? String(match.major_explore) : "",
        second_major_method: match.second_major_method || "",
        double_major: match.double_major != null ? String(match.double_major) : "",
        fusion_major: match.fusion_major != null ? String(match.fusion_major) : "",
        micro_major: match.micro_major != null ? String(match.micro_major) : "",
        minor_major: match.minor_major != null ? String(match.minor_major) : "",
      });
    } else {
      setForm({
        credits: 120, liberal: 30, global_comm: 6,
        community: "2학점", exam_bool: false, cert: "",
        nomad: "", career_explore: "", major_explore: "",
        second_major_method: "",
        double_major: "", fusion_major: "", micro_major: "", minor_major: "",
      });
    }
  }, [selGroup, selType, selMajor, rows]);

  // 학과별 졸업인증 로드
  useEffect(() => {
    if (!token || !selMajor) { setCertData(null); return; }
    fetchDeptCert(selMajor).then((d) => {
      setCertData(d);
      setCertForm(d.data);
    }).catch(() => setCertData(null));
  }, [token, selMajor, fetchDeptCert]);

  const intOrNull = (s: string) => {
    const v = s.trim();
    if (!v) return null;
    const n = parseInt(v, 10);
    return isNaN(n) ? null : n;
  };

  const handleSave = async () => {
    const requirements: Record<string, unknown> = {
      "졸업학점": form.credits,
      "교양이수학점": form.liberal,
      "글로벌소통역량학점": form.global_comm,
      "취업커뮤니티요건": form.community,
      "졸업시험여부": form.exam_bool,
    };
    if (form.cert.trim()) requirements["졸업인증"] = form.cert.trim();
    if (form.nomad.trim()) requirements["NOMAD비교과지수"] = form.nomad.trim();
    if (form.second_major_method.trim()) requirements["제2전공방법"] = form.second_major_method.trim();
    requirements["진로탐색학점"] = intOrNull(form.career_explore);
    requirements["전공탐색학점"] = intOrNull(form.major_explore);
    requirements["복수전공이수학점"] = intOrNull(form.double_major);
    requirements["융합전공이수학점"] = intOrNull(form.fusion_major);
    requirements["마이크로전공이수학점"] = intOrNull(form.micro_major);
    requirements["부전공이수학점"] = intOrNull(form.minor_major);

    try {
      await saveGraduation({
        group: selGroup, student_type: selType,
        major: selMajor || null, requirements,
      });
      setMsg("저장 완료. 채팅에 반영하려면 [그래프 현황] → '채팅 세션 초기화' 버튼을 누르세요.");
      fetchGraduation().then((d) => setRows(d.rows));
    } catch (e) { setMsg(`오류: ${e}`); }
  };

  const handleCertSave = async () => {
    if (!selMajor) return;
    try {
      await saveDeptCert({ major: selMajor, ...certForm });
      setMsg("졸업인증 저장 완료.");
    } catch (e) { setMsg(`오류: ${e}`); }
  };

  const curMatch = rows.find((r) =>
    r.group === selGroup && r.student_type === selType &&
    (selMajor ? r.major === selMajor : (r.major === "공통" || r.major === ""))
  );

  if (!token) return null;

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-lg font-bold text-navy">졸업요건 관리</h1>
      <p className="text-xs text-muted">학번 그룹과 학생 유형을 선택해 졸업요건을 입력·수정하세요. 저장 후 [그래프 현황] → &apos;채팅 세션 초기화&apos; 버튼을 눌러야 채팅에 반영됩니다.</p>

      {/* 전체 현황 테이블 */}
      <div>
        <button onClick={() => setShowOverview(!showOverview)} className="text-sm text-accent hover:underline">
          {showOverview ? "▼" : "▶"} 전체 졸업요건 현황 보기
        </button>
        {showOverview && (
          <div className="mt-2 bg-white rounded-xl border border-gray-100 shadow-sm overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-left text-muted bg-gray-50">
                  <th className="px-2 py-2">학번그룹</th><th className="px-2 py-2">유형</th><th className="px-2 py-2">전공</th>
                  <th className="px-2 py-2">졸업학점</th><th className="px-2 py-2">교양</th><th className="px-2 py-2">글로벌소통</th>
                  <th className="px-2 py-2">취업커뮤</th><th className="px-2 py-2">졸업시험</th><th className="px-2 py-2">졸업인증</th>
                  <th className="px-2 py-2">복수전공</th><th className="px-2 py-2">부전공</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.node_id} className="border-b border-border/50 hover:bg-gray-50">
                    <td className="px-2 py-1.5">{r.group_label}</td>
                    <td className="px-2 py-1.5">{r.student_type}</td>
                    <td className="px-2 py-1.5 text-text-sub">{r.major || "공통"}</td>
                    <td className="px-2 py-1.5 font-medium">{r.credits}</td>
                    <td className="px-2 py-1.5">{r.liberal}</td>
                    <td className="px-2 py-1.5">{r.global_comm}</td>
                    <td className="px-2 py-1.5">{r.community || "-"}</td>
                    <td className="px-2 py-1.5">{r.exam}</td>
                    <td className="px-2 py-1.5">{r.cert}</td>
                    <td className="px-2 py-1.5">{r.double_major ?? "-"}</td>
                    <td className="px-2 py-1.5">{r.minor_major ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 그룹/유형/전공 선택 */}
      {options && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">학번 그룹</label>
            <select value={selGroup} onChange={(e) => setSelGroup(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-lg text-sm">
              {Object.entries(options.groups).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">학생 유형</label>
            <select value={selType} onChange={(e) => setSelType(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-lg text-sm">
              {options.student_types.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">전공</label>
            <select value={selMajor ?? ""} onChange={(e) => setSelMajor(e.target.value || null)}
              className="w-full px-3 py-2 border border-border rounded-lg text-sm">
              {majorOptions().map((m) => (
                <option key={m.label} value={m.value ?? ""}>{m.label}</option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* 상태 표시 */}
      {curMatch ? (
        <div className="text-xs text-green-600 bg-green-50 p-2 rounded">기존 데이터 로드: {options?.groups[selGroup]} / {selType}{selMajor ? ` / ${selMajor}` : ""}</div>
      ) : (
        <div className="text-xs text-orange-600 bg-orange-50 p-2 rounded">데이터 없음: {options?.groups[selGroup]} / {selType}{selMajor ? ` / ${selMajor}` : ""} — 저장하면 새로 생성됩니다.</div>
      )}

      {/* 입력 폼 */}
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-4">
        <h2 className="text-sm font-bold">필수 항목</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">졸업학점</label>
            <input type="number" value={form.credits} onChange={(e) => setForm({ ...form, credits: Number(e.target.value) })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">교양이수학점</label>
            <input type="number" value={form.liberal} onChange={(e) => setForm({ ...form, liberal: Number(e.target.value) })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">글로벌소통역량학점</label>
            <input type="number" value={form.global_comm} onChange={(e) => setForm({ ...form, global_comm: Number(e.target.value) })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">취업커뮤니티요건</label>
            <input value={form.community} onChange={(e) => setForm({ ...form, community: e.target.value })}
              className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div className="flex items-center gap-2 pt-4">
            <input type="checkbox" checked={form.exam_bool} onChange={(e) => setForm({ ...form, exam_bool: e.target.checked })} />
            <label className="text-xs">졸업시험 있음</label>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">졸업인증</label>
            <input value={form.cert} onChange={(e) => setForm({ ...form, cert: e.target.value })}
              placeholder="예: TOPIK 4급, 없음" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
        </div>

        <h2 className="text-sm font-bold mt-4">선택 항목 (해당없으면 빈칸)</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">NOMAD비교과지수</label>
            <input value={form.nomad} onChange={(e) => setForm({ ...form, nomad: e.target.value })}
              placeholder="예: 미적용" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">진로탐색학점</label>
            <input value={form.career_explore} onChange={(e) => setForm({ ...form, career_explore: e.target.value })}
              placeholder="예: 2" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">전공탐색학점</label>
            <input value={form.major_explore} onChange={(e) => setForm({ ...form, major_explore: e.target.value })}
              placeholder="예: 3" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
        </div>
        <div>
          <label className="text-xs text-muted block mb-1">제2전공방법</label>
          <textarea value={form.second_major_method} onChange={(e) => setForm({ ...form, second_major_method: e.target.value })}
            placeholder="예: [방법1]복수·융합전공 30학점 / [방법2]마이크로전공 9학점"
            className="w-full px-2 py-1.5 border rounded text-sm h-16 resize-none" />
        </div>

        <h2 className="text-sm font-bold mt-4">전공 이수학점 (해당없으면 빈칸)</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">복수전공이수학점</label>
            <input value={form.double_major} onChange={(e) => setForm({ ...form, double_major: e.target.value })}
              placeholder="예: 30" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">융합전공이수학점</label>
            <input value={form.fusion_major} onChange={(e) => setForm({ ...form, fusion_major: e.target.value })}
              placeholder="예: 30" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">마이크로전공이수학점</label>
            <input value={form.micro_major} onChange={(e) => setForm({ ...form, micro_major: e.target.value })}
              placeholder="예: 9" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">부전공이수학점</label>
            <input value={form.minor_major} onChange={(e) => setForm({ ...form, minor_major: e.target.value })}
              placeholder="예: 18" className="w-full px-2 py-1.5 border rounded text-sm" />
          </div>
        </div>

        <button onClick={handleSave} className="w-full py-2.5 bg-accent text-white rounded-lg font-medium hover:bg-accent/90 mt-4">
          졸업요건 저장
        </button>
      </div>

      {msg && <p className="text-xs text-green-600 bg-green-50 p-2 rounded">{msg}</p>}

      {/* 학과별 졸업인증 (전공 선택 시만) */}
      {selMajor && (
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-5 space-y-4">
          <h2 className="text-sm font-bold">학과별 졸업인증 요건</h2>
          <p className="text-xs text-muted">학번 그룹/학생유형과 무관하게 학과 단위로 관리됩니다.</p>
          {certData?.node_id ? (
            <div className="text-xs text-green-600 bg-green-50 p-2 rounded">기존 졸업인증 데이터 로드: {certData.node_id}</div>
          ) : (
            <div className="text-xs text-blue-600 bg-blue-50 p-2 rounded">졸업인증 데이터가 아직 없습니다. 아래에서 입력 후 저장하세요.</div>
          )}
          <div>
            <label className="text-xs text-muted block mb-1">졸업시험·졸업인증 요건 (전체 요약)</label>
            <textarea value={certForm.cert_requirement}
              onChange={(e) => setCertForm({ ...certForm, cert_requirement: e.target.value })}
              placeholder="예: 졸업시험(정보보호개론, 암호론) 70점 이상 합격. 자격증으로 대체 가능."
              className="w-full px-2 py-1.5 border rounded text-sm h-24 resize-none" />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted block mb-1">시험 과목</label>
              <input value={certForm.cert_subjects}
                onChange={(e) => setCertForm({ ...certForm, cert_subjects: e.target.value })}
                placeholder="예: 정보보호개론, 암호론" className="w-full px-2 py-1.5 border rounded text-sm" />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">합격 기준</label>
              <input value={certForm.cert_pass_criteria}
                onChange={(e) => setCertForm({ ...certForm, cert_pass_criteria: e.target.value })}
                placeholder="예: 70점 이상" className="w-full px-2 py-1.5 border rounded text-sm" />
            </div>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">대체 방법</label>
            <textarea value={certForm.cert_alternative}
              onChange={(e) => setCertForm({ ...certForm, cert_alternative: e.target.value })}
              placeholder="예: 정보처리기사 자격증 / 취업박람회 참가 등"
              className="w-full px-2 py-1.5 border rounded text-sm h-16 resize-none" />
          </div>
          <button onClick={handleCertSave} className="w-full py-2.5 bg-accent text-white rounded-lg font-medium hover:bg-accent/90">
            졸업인증 저장
          </button>
        </div>
      )}
    </div>
  );
}
