"use client";
import type { ActionItem } from "@/lib/types";
import { AlertCircle, AlertTriangle, Info, MessageSquare } from "lucide-react";

interface Props {
  actions: ActionItem[];
  onAskAI?: (question: string) => void;
}

const SEVERITY_STYLE: Record<ActionItem["severity"], {
  bg: string; border: string; icon: React.ElementType; iconColor: string; label: string;
}> = {
  error: { bg: "bg-red-50", border: "border-red-200", icon: AlertCircle, iconColor: "text-red-600", label: "필수" },
  warn: { bg: "bg-amber-50", border: "border-amber-200", icon: AlertTriangle, iconColor: "text-amber-600", label: "주의" },
  info: { bg: "bg-blue-50", border: "border-blue-200", icon: Info, iconColor: "text-blue-600", label: "정보" },
};

export default function ActionChecklist({ actions, onAskAI }: Props) {
  if (!actions.length) {
    return (
      <section className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm text-center">
        <p className="text-sm text-slate-500">확인된 액션 아이템이 없습니다.</p>
      </section>
    );
  }

  return (
    <section className="bg-white border border-slate-200 rounded-2xl p-5 md:p-6 shadow-sm">
      <div className="flex items-baseline justify-between mb-4">
        <h3 className="text-base md:text-lg font-bold text-slate-900">
          액션 체크리스트
        </h3>
        <span className="text-xs text-slate-400">객관·규정 근거</span>
      </div>

      <ul className="space-y-3">
        {actions.map((a, idx) => {
          const style = SEVERITY_STYLE[a.severity];
          const Icon = style.icon;
          return (
            <li
              key={`${a.type}-${idx}`}
              className={`p-4 rounded-xl border ${style.bg} ${style.border}`}
            >
              <div className="flex items-start gap-3">
                <Icon className={`w-5 h-5 shrink-0 mt-0.5 ${style.iconColor}`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-[10px] font-black uppercase px-1.5 py-0.5 rounded ${style.bg} ${style.iconColor} border ${style.border}`}>
                      {style.label}
                    </span>
                    <h4 className="text-sm font-bold text-slate-900 truncate">{a.title}</h4>
                  </div>
                  <p className="text-xs text-slate-600 leading-relaxed">{a.description}</p>
                  <div className="flex items-center justify-between mt-2 gap-2">
                    {a.action_label && onAskAI && (
                      <button
                        onClick={() => onAskAI(`${a.title} — ${a.description}`)}
                        className="flex items-center gap-1.5 text-[11px] font-bold text-blue-600 hover:text-blue-800"
                      >
                        <MessageSquare className="w-3.5 h-3.5" />
                        {a.action_label}
                      </button>
                    )}
                    <span className="text-[10px] text-slate-400 font-mono truncate" title={a.source}>
                      {a.source}
                    </span>
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
