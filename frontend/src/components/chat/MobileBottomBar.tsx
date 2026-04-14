"use client";
import { MessageSquare, GraduationCap, Bell, User } from "lucide-react";
import type { Lang, TabId } from "@/lib/types";
import { t } from "@/lib/i18n";

const TABS: { id: TabId; Icon: React.ElementType; labelKey: string }[] = [
  { id: "chat", Icon: MessageSquare, labelKey: "tab.chat" },
  { id: "report", Icon: GraduationCap, labelKey: "tab.report" },
  { id: "notifications", Icon: Bell, labelKey: "tab.notifications" },
  { id: "profile", Icon: User, labelKey: "tab.profile" },
];

interface MobileBottomBarProps {
  lang: Lang;
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

export default function MobileBottomBar({ lang, activeTab, onTabChange }: MobileBottomBarProps) {
  return (
    <div className="lg:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-slate-100 flex justify-around py-3 px-2 z-50 shadow-[0_-4px_12px_rgba(0,0,0,0.04)] rounded-t-2xl">
      {TABS.map(({ id, Icon, labelKey }) => {
        const isActive = activeTab === id;
        return (
          <button
            key={id}
            onClick={() => onTabChange(id)}
            className={`flex flex-col items-center gap-1 transition-colors ${
              isActive ? "text-blue-600" : "text-slate-400"
            }`}
          >
            <div className={`p-1 rounded-lg ${isActive ? "bg-blue-50" : ""}`}>
              <Icon className="w-6 h-6" />
            </div>
            <span className="text-[10px] font-bold uppercase tracking-tighter">
              {t(lang, labelKey)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
