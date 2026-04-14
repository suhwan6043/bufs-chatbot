"use client";
import { useState } from "react";
import { MessageSquare, PlusCircle, FileText, History, User, Settings, LogOut, Send, X } from "lucide-react";
import { CalendarPlus, ClipboardList, BookOpen, GraduationCap, Monitor, BarChart3, Calendar, Megaphone } from "lucide-react";
import type { Lang, UserProfile, TabId } from "@/lib/types";
import { t } from "@/lib/i18n";
import { QUICK_FEATURES_BASE, PORTAL_LINKS } from "@/lib/constants";
import { apiFetch } from "@/lib/api";
import TranscriptUpload from "./TranscriptUpload";

const QF_ICONS: Record<string, React.ElementType> = { CalendarPlus, ClipboardList, BookOpen, GraduationCap };
const PL_ICONS: Record<string, React.ElementType> = { Monitor, BarChart3, Calendar, Megaphone };

interface SidebarProps {
  lang: Lang;
  profile?: UserProfile | null;
  sessionId?: string | null;
  hasTranscript?: boolean;
  authUser?: { nickname: string; student_id: string; department: string } | null;
  onSelectQuestion: (q: string) => void;
  onClearChat: () => void;
  onNewChat: () => void;
  onTabChange: (tab: TabId) => void;
  onLogout?: () => void;
  activeTab: TabId;
  isOpen: boolean;
  onClose: () => void;
}

export default function Sidebar({
  lang, profile, sessionId, hasTranscript, authUser, onSelectQuestion, onClearChat, onNewChat, onTabChange, onLogout, activeTab, isOpen, onClose,
}: SidebarProps) {
  const [fbText, setFbText] = useState("");
  const [fbSent, setFbSent] = useState(false);
  const [fbOpen, setFbOpen] = useState(false);

  const submitFeedback = async () => {
    if (!fbText.trim() || !sessionId) return;
    try {
      await apiFetch("/api/feedback", { method: "POST", body: JSON.stringify({ session_id: sessionId, text: fbText }) });
      setFbSent(true);
      setFbText("");
      setTimeout(() => setFbSent(false), 3000);
    } catch {}
  };

  return (
    <>
      {/* Overlay (all screen sizes) */}
      {isOpen && <div className="fixed inset-0 bg-black/30 z-30" onClick={onClose} />}

      <aside
        className={`fixed top-0 left-0 h-full w-72 bg-slate-50 border-r border-slate-200 z-40 flex flex-col transition-transform duration-300 ${
          isOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Brand */}
        <div className="p-5 flex items-center justify-between">
          <div className="flex items-center gap-3 cursor-pointer" onClick={() => { onTabChange("chat"); onClose(); }}>
            <div className="w-10 h-10 bg-blue-600 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200">
              <MessageSquare className="w-6 h-6 text-white" />
            </div>
            <span className="font-bold text-xl tracking-tight">CamChat</span>
          </div>
          <button onClick={onClose} className="lg:hidden p-1.5 hover:bg-slate-200 rounded-lg text-slate-400">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* New Chat */}
        <div className="px-4 mb-3">
          <button
            onClick={() => { onNewChat(); onClose(); }}
            className="w-full py-2.5 px-4 bg-white border border-slate-200 rounded-xl flex items-center gap-3 hover:border-blue-400 hover:text-blue-600 transition-all shadow-sm font-semibold text-sm"
          >
            <PlusCircle className="w-5 h-5" />
            {t(lang, "sidebar.new_chat")}
          </button>
        </div>

        {/* Scrollable nav */}
        <nav className="flex-grow px-4 space-y-1 overflow-y-auto">
          {/* Academic Report */}
          <div className="mb-3">
            <button
              onClick={() => { onTabChange("report"); onClose(); }}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm font-bold rounded-xl transition-all ${
                activeTab === "report"
                  ? "bg-blue-600 text-white shadow-md shadow-blue-200"
                  : "bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-100"
              }`}
            >
              <FileText className="w-5 h-5" />
              {t(lang, "sidebar.academic_report")}
              {hasTranscript && activeTab !== "report" && (
                <span className="ml-auto w-2 h-2 bg-green-500 rounded-full" />
              )}
            </button>
          </div>

          {/* Transcript upload (inline) */}
          {sessionId && (
            <div className="mb-3">
              <TranscriptUpload lang={lang} sessionId={sessionId} />
            </div>
          )}

          {/* Quick Access */}
          <div className="mb-3 space-y-0.5">
            <p className="px-4 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-widest">
              {t(lang, "sidebar.quick_features")}
            </p>
            {QUICK_FEATURES_BASE.map((f, idx) => {
              const Icon = QF_ICONS[f.iconName] || BookOpen;
              return (
                <button
                  key={idx}
                  onClick={() => { onSelectQuestion(t(lang, f.questionKey)); onTabChange("chat"); onClose(); }}
                  className="w-full flex items-center gap-3 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-white hover:text-blue-600 rounded-xl transition-all group"
                >
                  <div className={`p-1.5 rounded-lg ${f.bgColor} group-hover:scale-110 transition-transform`}>
                    <Icon className={`w-4 h-4 ${f.iconColor}`} />
                  </div>
                  {t(lang, f.labelKey)}
                </button>
              );
            })}
          </div>

          {/* Portal Links */}
          <div className="mb-3 space-y-0.5">
            <p className="px-4 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-widest">
              {t(lang, "sidebar.portal_links")}
            </p>
            {PORTAL_LINKS.map((link, idx) => {
              const Icon = PL_ICONS[link.iconName] || BookOpen;
              return (
                <a
                  key={idx}
                  href={link.url}
                  target="_blank"
                  rel="noreferrer"
                  className="w-full flex items-center gap-3 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-white hover:text-blue-600 rounded-xl transition-all"
                >
                  <Icon className="w-4 h-4 opacity-60" />
                  {t(lang, link.key)}
                </a>
              );
            })}
          </div>

          {/* History placeholder */}
          <div className="space-y-0.5">
            <p className="px-4 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-widest">
              {t(lang, "sidebar.history")}
            </p>
            {[t(lang, "qf.register"), t(lang, "qf.grades"), t(lang, "qf.schedule")].map((item, i) => (
              <button
                key={i}
                onClick={() => { onSelectQuestion(item); onTabChange("chat"); onClose(); }}
                className="w-full flex items-center gap-3 px-4 py-2 text-sm font-medium text-slate-500 hover:bg-white hover:text-blue-600 rounded-xl transition-all group"
              >
                <History className="w-4 h-4 opacity-50 group-hover:opacity-100" />
                <span className="truncate">{item}</span>
              </button>
            ))}
          </div>
        </nav>

        {/* Bottom section */}
        <div className="p-4 border-t border-slate-200 space-y-1">
          {/* Profile info */}
          {authUser ? (
            <div className="flex items-center gap-3 px-4 py-2.5 bg-blue-50 rounded-xl border border-blue-100">
              <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white font-bold text-xs shrink-0">
                {authUser.nickname.slice(0, 1)}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-bold text-slate-900 truncate">{authUser.nickname}</p>
                <p className="text-[11px] text-slate-500 truncate">{authUser.student_id}{t(lang, "sidebar.year_suffix")} · {authUser.department}</p>
              </div>
            </div>
          ) : (
            <a href={`/${lang}/login`} className="flex items-center gap-3 px-4 py-2 text-sm font-medium text-blue-600 hover:bg-blue-50 rounded-xl transition-all">
              <User className="w-5 h-5" />
              {t(lang, "auth.login_required")}
            </a>
          )}

          {/* Feedback toggle */}
          <button
            onClick={() => setFbOpen(!fbOpen)}
            className="w-full flex items-center gap-3 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-white rounded-xl transition-all"
          >
            <Send className="w-5 h-5 opacity-70" />
            {t(lang, "sidebar.feedback")}
          </button>

          {fbOpen && (
            <div className="px-2 pb-2 animate-fade-in">
              <textarea
                value={fbText}
                onChange={(e) => setFbText(e.target.value)}
                placeholder={t(lang, "sidebar.feedback_ph")}
                className="w-full h-20 p-2 border border-slate-200 rounded-lg text-xs resize-none focus:border-blue-400 outline-none"
              />
              <button
                onClick={submitFeedback}
                disabled={!fbText.trim()}
                className="w-full mt-1 py-1.5 bg-blue-600 text-white text-xs font-semibold rounded-lg hover:bg-blue-700 disabled:bg-slate-300 transition-all"
              >
                {t(lang, "sidebar.feedback_submit")}
              </button>
              {fbSent && <p className="text-xs text-green-600 mt-1 font-semibold">{t(lang, "sidebar.feedback_ok")}</p>}
            </div>
          )}

          {/* Clear chat */}
          <button
            onClick={() => { onClearChat(); onClose(); }}
            className="w-full flex items-center gap-3 px-4 py-2 text-sm font-medium text-slate-500 hover:bg-slate-100 rounded-xl transition-all"
          >
            <X className="w-5 h-5 opacity-70" />
            {t(lang, "sidebar.clear_chat")}
          </button>

          {/* Logout */}
          {authUser && onLogout && (
            <button
              onClick={() => { onLogout(); onClose(); }}
              className="w-full flex items-center gap-3 px-4 py-2 text-sm font-medium text-red-500 hover:bg-red-50 rounded-xl transition-all"
            >
              <LogOut className="w-5 h-5 opacity-70" />
              {t(lang, "sidebar.logout")}
            </button>
          )}

          <p className="text-center text-[10px] text-slate-400 pt-1">v0.3.0</p>
        </div>
      </aside>
    </>
  );
}
