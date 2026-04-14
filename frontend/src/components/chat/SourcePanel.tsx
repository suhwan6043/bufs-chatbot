"use client";
import { useState } from "react";
import { ChevronDown, ChevronUp, FileText, ExternalLink } from "lucide-react";
import type { Lang, SearchResultItem, SourceURL } from "@/lib/types";
import { t } from "@/lib/i18n";

interface SourcePanelProps {
  lang: Lang;
  results?: SearchResultItem[];
  sourceUrls?: SourceURL[];
}

export default function SourcePanel({ lang, results, sourceUrls }: SourcePanelProps) {
  const [open, setOpen] = useState(false);
  const contextResults = results?.filter((r) => r.in_context)?.slice(0, 5) ?? [];
  if (contextResults.length === 0 && (!sourceUrls || sourceUrls.length === 0)) return null;

  return (
    <div className="mt-2 ml-13">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-blue-600 hover:text-blue-800 font-semibold flex items-center gap-1 transition-colors"
      >
        <FileText className="w-3.5 h-3.5" />
        {t(lang, "source.panel")}
        {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
      </button>

      {open && (
        <div className="mt-2 space-y-2 border-t border-slate-200 pt-2 animate-fade-in">
          {contextResults.map((r, i) => (
            <div key={i} className="text-xs p-2.5 bg-slate-50 rounded-lg border border-slate-200">
              <span className="text-[10px] font-bold text-slate-400 uppercase">
                [{r.doc_type}] {r.source} p.{r.page_number}
              </span>
              <p className="text-slate-600 mt-1 line-clamp-3">{r.text}</p>
            </div>
          ))}
          {sourceUrls && sourceUrls.length > 0 && (
            <div className="pt-1">
              <p className="text-[10px] font-bold text-slate-400 uppercase mb-1">{t(lang, "source.related")}</p>
              {sourceUrls.map((s, i) => (
                <a
                  key={i}
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-600 hover:underline flex items-center gap-1 py-0.5"
                >
                  <ExternalLink className="w-3 h-3" />
                  {s.title}
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
