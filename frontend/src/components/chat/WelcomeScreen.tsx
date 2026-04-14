"use client";
import { Sparkles } from "lucide-react";
import type { Lang } from "@/lib/types";
import { t } from "@/lib/i18n";
import QuickFeatures from "./QuickFeatures";

interface WelcomeScreenProps {
  lang: Lang;
  onSelect: (q: string) => void;
  hasTranscript?: boolean;
}

export default function WelcomeScreen({ lang, onSelect, hasTranscript }: WelcomeScreenProps) {
  return (
    <div className="py-8 md:py-12 space-y-10 animate-fade-in">
      {/* Hero section — desktop */}
      <div className="hidden md:block space-y-5 text-center lg:text-left">
        <div className="inline-flex items-center gap-2 px-4 py-2 bg-blue-50 text-blue-600 rounded-2xl font-bold text-xs uppercase tracking-widest shadow-sm border border-blue-100">
          <Sparkles className="w-4 h-4" /> Academic AI Assistant
        </div>
        <h1 className="text-4xl lg:text-5xl font-black text-slate-900 tracking-tight leading-tight">
          {t(lang, "welcome.hero_title")}
          <br />
          <span className="text-slate-400">{t(lang, "welcome.hero_sub")}</span>
        </h1>
        <p className="text-slate-500 font-semibold text-lg max-w-xl mx-auto lg:mx-0 leading-relaxed">
          {t(lang, "welcome.hero_desc")}
        </p>
        {hasTranscript && (
          <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-green-50 text-green-700 rounded-full text-xs font-bold border border-green-200">
            <span className="w-2 h-2 bg-green-500 rounded-full" />
            {t(lang, "chat.welcome_tx_title")}
          </div>
        )}
      </div>

      {/* Hero section — mobile */}
      <div className="md:hidden flex gap-3">
        <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center shrink-0">
          <Sparkles className="w-4 h-4 text-blue-600" />
        </div>
        <div className="bg-white border border-slate-100 shadow-sm p-3.5 rounded-2xl rounded-tl-none max-w-[85%]">
          <p className="text-sm leading-relaxed text-slate-800 font-medium">
            {t(lang, "chat.welcome_sub")}
          </p>
        </div>
      </div>

      {/* Feature cards — large on desktop, compact on mobile */}
      <div className="hidden md:block">
        <QuickFeatures lang={lang} onSelect={onSelect} variant="card" />
      </div>
      <div className="md:hidden">
        <QuickFeatures lang={lang} onSelect={onSelect} variant="compact" />
      </div>

      <p className="text-center text-xs text-slate-400 font-semibold">
        {t(lang, "chat.welcome_hint")}
      </p>
    </div>
  );
}
