"use client";
import { CalendarPlus, ClipboardList, BookOpen, GraduationCap, Target, RotateCcw } from "lucide-react";
import type { Lang } from "@/lib/types";
import { t } from "@/lib/i18n";
import { QUICK_FEATURES_BASE } from "@/lib/constants";

const ICON_MAP: Record<string, React.ElementType> = {
  CalendarPlus, ClipboardList, BookOpen, GraduationCap, Target, RotateCcw,
};

interface QuickFeaturesProps {
  lang: Lang;
  onSelect: (question: string) => void;
  disabled?: boolean;
  variant?: "compact" | "card";
}

export default function QuickFeatures({ lang, onSelect, disabled, variant = "compact" }: QuickFeaturesProps) {
  if (variant === "card") {
    return (
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {QUICK_FEATURES_BASE.map((f, idx) => {
          const Icon = ICON_MAP[f.iconName] || BookOpen;
          return (
            <button
              key={idx}
              onClick={() => onSelect(t(lang, f.questionKey))}
              disabled={disabled}
              className={`flex flex-col items-start p-6 bg-white border border-slate-100 rounded-[1.5rem] hover:shadow-xl hover:shadow-blue-100 hover:border-blue-200 transition-all group text-left shadow-sm disabled:opacity-50 animate-slide-up stagger-${idx + 1}`}
            >
              <div className={`w-12 h-12 ${f.bgColor} rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 group-hover:rotate-3 transition-all`}>
                <Icon className={`w-6 h-6 ${f.iconColor}`} />
              </div>
              <span className="font-bold text-slate-900 text-base mb-0.5">{t(lang, f.labelKey)}</span>
              <span className="text-[10px] text-slate-400 font-semibold uppercase tracking-widest">{t(lang, "welcome.card_open")}</span>
            </button>
          );
        })}
      </div>
    );
  }

  // compact variant (sidebar / mobile)
  return (
    <div className="grid grid-cols-2 gap-2">
      {QUICK_FEATURES_BASE.map((f, idx) => {
        const Icon = ICON_MAP[f.iconName] || BookOpen;
        return (
          <button
            key={idx}
            onClick={() => onSelect(t(lang, f.questionKey))}
            disabled={disabled}
            className="flex items-center gap-2 px-3 py-2.5 bg-white border border-slate-200 rounded-xl text-sm font-semibold text-slate-700 hover:bg-blue-50 hover:border-blue-200 hover:text-blue-600 transition-all disabled:opacity-50 group"
          >
            <div className={`p-1.5 ${f.bgColor} rounded-lg group-hover:scale-110 transition-transform`}>
              <Icon className={`w-4 h-4 ${f.iconColor}`} />
            </div>
            {t(lang, f.labelKey)}
          </button>
        );
      })}
    </div>
  );
}
