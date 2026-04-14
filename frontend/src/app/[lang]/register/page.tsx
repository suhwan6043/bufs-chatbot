"use client";
import { useState, use } from "react";
import { useRouter } from "next/navigation";
import { MessageSquare, UserPlus } from "lucide-react";
import type { Lang } from "@/lib/types";
import { t } from "@/lib/i18n";
import { useAuth } from "@/hooks/useAuth";
import { COLLEGES, YEARS } from "@/lib/constants";

const STUDENT_TYPES = ["type_domestic", "type_intl", "type_transfer"] as const;
const STYPE_MAP: Record<string, string> = { type_domestic: "내국인", type_intl: "외국인", type_transfer: "편입생" };

export default function RegisterPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang: rawLang } = use(params);
  const lang = (rawLang === "en" ? "en" : "ko") as Lang;
  const router = useRouter();
  const { register } = useAuth();

  const [nickname, setNickname] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [year, setYear] = useState("2023");
  const [dept, setDept] = useState("");
  const [stype, setStype] = useState("type_domestic");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const validate = (): string | null => {
    if (nickname.length < 2) return lang === "ko" ? "닉네임은 2자 이상이어야 합니다" : "Nickname must be at least 2 characters";
    if (!/^[a-zA-Z0-9_]{4,20}$/.test(username)) return lang === "ko" ? "아이디는 영숫자 4-20자여야 합니다" : "Username must be 4-20 alphanumeric characters";
    if (password.length < 8) return t(lang, "auth.pw_min");
    if (password !== passwordConfirm) return t(lang, "auth.pw_mismatch");
    if (!dept) return lang === "ko" ? "전공을 선택해주세요" : "Please select a major";
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    const validationError = validate();
    if (validationError) { setError(validationError); return; }

    setLoading(true);
    const result = await register({
      username,
      nickname,
      password,
      student_id: year,
      department: dept,
      student_type: STYPE_MAP[stype] || "내국인",
    });
    setLoading(false);

    if (result.ok) {
      window.location.href = `/${lang}/chat`;
    } else {
      setError(result.error || "Registration failed");
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-4 py-8">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-6">
          <div className="w-14 h-14 bg-blue-600 rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-lg shadow-blue-200">
            <MessageSquare className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-black text-slate-900 tracking-tight">CamChat</h1>
          <p className="text-sm text-slate-500 mt-1">{t(lang, "auth.register")}</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-4">
          {/* Nickname */}
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">{t(lang, "auth.nickname")}</label>
            <input type="text" value={nickname} onChange={(e) => setNickname(e.target.value)} placeholder={lang === "ko" ? "표시될 이름" : "Display name"} required
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:border-blue-400 focus:ring-4 focus:ring-blue-100 outline-none transition-all" />
          </div>

          {/* Username */}
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">{t(lang, "auth.username")}</label>
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} placeholder={lang === "ko" ? "영문, 숫자 4-20자" : "4-20 alphanumeric"} required autoComplete="username"
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:border-blue-400 focus:ring-4 focus:ring-blue-100 outline-none transition-all" />
          </div>

          {/* Password */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">{t(lang, "auth.password")}</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="8+" required autoComplete="new-password"
                className="w-full px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:border-blue-400 focus:ring-4 focus:ring-blue-100 outline-none transition-all" />
            </div>
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">{t(lang, "auth.password_confirm")}</label>
              <input type="password" value={passwordConfirm} onChange={(e) => setPasswordConfirm(e.target.value)} placeholder="8+" required autoComplete="new-password"
                className="w-full px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:border-blue-400 focus:ring-4 focus:ring-blue-100 outline-none transition-all" />
            </div>
          </div>

          {/* Year */}
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">{t(lang, "auth.student_id")}</label>
            <select value={year} onChange={(e) => setYear(e.target.value)}
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:border-blue-400 outline-none bg-white">
              {YEARS.map((y) => (
                <option key={y} value={String(y)}>{t(lang, "onboard.year_fmt", { y })}</option>
              ))}
            </select>
          </div>

          {/* Department */}
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">{t(lang, "auth.department")}</label>
            <select value={dept} onChange={(e) => setDept(e.target.value)}
              className="w-full px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:border-blue-400 outline-none bg-white">
              <option value="">{t(lang, "onboard.dept_none")}</option>
              {COLLEGES.map((college) => (
                <optgroup key={college.ko} label={lang === "en" ? college.en : college.ko}>
                  {college.depts.map((d) => (
                    <option key={d.ko} value={d.ko}>{lang === "en" ? d.en : d.ko}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {/* Student type */}
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">{t(lang, "auth.student_type")}</label>
            <div className="flex gap-2">
              {STUDENT_TYPES.map((st) => (
                <button key={st} type="button" onClick={() => setStype(st)}
                  className={`flex-1 py-2 rounded-xl text-sm font-semibold border transition-all ${
                    stype === st ? "bg-blue-600 text-white border-blue-600" : "bg-white text-slate-500 border-slate-200 hover:border-blue-300"
                  }`}>
                  {t(lang, `onboard.${st}`)}
                </button>
              ))}
            </div>
          </div>

          {error && (
            <p className="text-sm text-red-500 font-semibold bg-red-50 px-3 py-2 rounded-lg">{error}</p>
          )}

          <button type="submit" disabled={loading}
            className="w-full py-3 bg-blue-600 text-white font-bold rounded-xl hover:bg-blue-700 disabled:bg-slate-300 transition-all shadow-lg shadow-blue-200 active:scale-[0.98]">
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />...
              </span>
            ) : (
              <span className="flex items-center justify-center gap-2">
                <UserPlus className="w-4 h-4" />
                {t(lang, "auth.register_btn")}
              </span>
            )}
          </button>

          <p className="text-center text-sm text-slate-500">
            {t(lang, "auth.has_account")}{" "}
            <a href={`/${lang}/login`} className="text-blue-600 font-semibold hover:underline">
              {t(lang, "auth.login")}
            </a>
          </p>
        </form>
      </div>
    </div>
  );
}
