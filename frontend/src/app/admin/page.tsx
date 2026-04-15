"use client";
import { useState} from "react";
import { useRouter } from "next/navigation";
import { useAdmin } from "@/hooks/useAdmin";

export default function AdminLoginPage() {
  const router = useRouter();
  const { login, error } = useAdmin();
  const [pw, setPw] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pw.trim()) return;
    setLoading(true);
    const ok = await login(pw);
    setLoading(false);
    if (ok) window.location.href = `/admin/dashboard`;
  };

  return (
    <div className="min-h-screen bg-main flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-6">
          <div className="text-4xl mb-2">{"\uD83D\uDD10"}</div>
          <h1 className="text-xl font-bold text-navy">{"관리자 로그인"}</h1>
        </div>
        <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-border p-6 shadow-sm space-y-4">
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus
            placeholder={"관리자 비밀번호 입력"}
            className="w-full px-3 py-2.5 border border-border rounded-lg text-sm focus:border-accent outline-none" />
          {error && <p className="text-xs text-red-500">{error}</p>}
          <button type="submit" disabled={loading}
            className="w-full py-2.5 bg-accent text-white rounded-lg font-medium hover:bg-accent/90 disabled:opacity-50 transition-colors">
            {loading ? "..." : "로그인"}
          </button>
        </form>
      </div>
    </div>
  );
}
