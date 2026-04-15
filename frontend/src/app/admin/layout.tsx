"use client";
import { useState, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAdmin } from "@/hooks/useAdmin";

const MENU = [
  { key: "dashboard", icon: "\uD83D\uDCCA", label: "\uB300\uC2DC\uBCF4\uB4DC" },
  { key: "logs",      icon: "\uD83D\uDCC4", label: "\uB300\uD654 \uB85C\uADF8" },
  { key: "faq",       icon: "\uD83D\uDCAC", label: "FAQ \uAD00\uB9AC" },
  { key: "crawler",   icon: "\uD83D\uDD77\uFE0F", label: "\uD06C\uB864\uB7EC \uAD00\uB9AC" },
  { key: "graph",     icon: "\uD83D\uDCC8", label: "\uADF8\uB798\uD504 \uD604\uD669" },
  { key: "contacts",  icon: "\uD83D\uDCDE", label: "\uC5F0\uB77D\uCC98 \uAD00\uB9AC" },
  { key: "graduation",icon: "\uD83D\uDCCB", label: "\uC878\uC5C5\uC694\uAC74 \uAD00\uB9AC" },
  { key: "early-grad",icon: "\uD83C\uDF93", label: "\uC870\uAE30\uC878\uC5C5 \uAD00\uB9AC" },
  { key: "schedule",  icon: "\uD83D\uDCC5", label: "\uD559\uC0AC\uC77C\uC815 \uAD00\uB9AC" },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { token, logout } = useAdmin();
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);

  const isLoginPage = pathname === "/admin" || pathname === "/admin/";

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (!mounted || token) return;
    if (!isLoginPage) router.replace("/admin");
  }, [mounted, token, isLoginPage, router]);

  if (!mounted) return null;

  // 로그인 페이지: 항상 사이드바 없이 렌더
  if (isLoginPage) {
    if (token) { router.replace("/admin/dashboard"); return null; }
    return <>{children}</>;
  }

  // 서브페이지: 토큰 없으면 차단 (useEffect에서 /admin으로 리다이렉트)
  if (!token) return null;

  const current = pathname.split("/admin/")[1]?.split("/")[0] || "dashboard";

  return (
    <div className="min-h-screen bg-main flex">
      {open && <div className="fixed inset-0 bg-black/30 z-30 md:hidden" onClick={() => setOpen(false)} />}
      <aside className={`fixed md:static z-40 w-56 h-screen bg-white border-r border-border flex flex-col transition-transform ${open ? "translate-x-0" : "-translate-x-full"} md:translate-x-0`}>
        <div className="px-4 py-4 border-b border-border">
          <h1 className="text-sm font-bold text-navy">{"\uD83C\uDF93"} CAMCHAT Admin</h1>
        </div>
        <nav className="flex-1 py-2 overflow-y-auto">
          {MENU.map((m) => (
            <a key={m.key} href={`/admin/${m.key}`} onClick={() => setOpen(false)}
              className={`flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors ${current === m.key ? "bg-blue-50 text-accent font-medium border-r-2 border-accent" : "text-text-sub hover:bg-gray-50"}`}>
              <span className="text-base">{m.icon}</span>{m.label}
            </a>
          ))}
        </nav>
        <div className="px-4 py-3 border-t border-border">
          <button onClick={() => { logout(); window.location.href = "/admin"; }}
            className="w-full py-2 text-xs text-red-500 border border-red-200 rounded-lg hover:bg-red-50 transition-colors">로그아웃</button>
        </div>
      </aside>
      <div className="flex-1 flex flex-col min-h-screen">
        <div className="md:hidden bg-white border-b border-border px-4 py-3 flex items-center gap-3">
          <button onClick={() => setOpen(!open)} className="text-xl">{"\u2630"}</button>
          <span className="text-sm font-bold text-navy">{"\uD83C\uDF93"} CAMCHAT Admin</span>
        </div>
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
