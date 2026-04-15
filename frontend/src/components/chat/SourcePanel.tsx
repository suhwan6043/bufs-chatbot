"use client";
import { useState } from "react";
import { ChevronDown, ChevronUp, FileText, ExternalLink, Image as ImageIcon, X } from "lucide-react";
import type { Lang, SearchResultItem, SourceURL } from "@/lib/types";
import { t } from "@/lib/i18n";
import { BASE_URL } from "@/lib/api";

interface SourcePanelProps {
  lang: Lang;
  results?: SearchResultItem[];
  sourceUrls?: SourceURL[];
}

function isPdf(r: SearchResultItem) {
  return r.source && (r.source.endsWith(".pdf") || r.doc_type === "domestic" || r.doc_type === "guide");
}

function sourceLabel(r: SearchResultItem): string {
  if (r.source) {
    const name = r.source.split("/").pop() || r.source;
    return `${name}${r.page_number ? ` p.${r.page_number}` : ""}`;
  }
  return r.doc_type || "source";
}

function pdfImageUrl(r: SearchResultItem): string {
  const params = new URLSearchParams({
    file: r.source,
    page: String(r.page_number || 1),
    chunk_text: r.text || "",
  });
  return `${BASE_URL}/api/source/pdf?${params}`;
}

export default function SourcePanel({ lang, results, sourceUrls }: SourcePanelProps) {
  const [open, setOpen] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const [imgLoading, setImgLoading] = useState(false);

  const contextResults = results?.filter((r) => r.in_context)?.slice(0, 5) ?? [];
  if (contextResults.length === 0 && (!sourceUrls || sourceUrls.length === 0)) return null;

  // 중복 제거 (source+page 기준)
  const seen = new Set<string>();
  const deduped = contextResults.filter((r) => {
    const key = isPdf(r)
      ? `${r.source}:${r.page_number}`
      : r.faq_id || r.source_url || `${r.doc_type}:${(r.text || "").slice(0, 40)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

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
          {deduped.map((r, i) => (
            <div key={i} className="text-xs rounded-lg border border-slate-200 overflow-hidden">
              {/* Header */}
              <div
                className={`flex items-center justify-between p-2.5 bg-slate-50 ${isPdf(r) ? "cursor-pointer hover:bg-blue-50" : ""}`}
                onClick={() => {
                  if (isPdf(r)) {
                    if (expandedIdx === i) {
                      setExpandedIdx(null);
                    } else {
                      setExpandedIdx(i);
                      setImgLoading(true);
                    }
                  }
                }}
              >
                <div className="flex-1 min-w-0">
                  <span className="text-[10px] font-bold text-slate-400 uppercase">
                    [{r.doc_type}] {sourceLabel(r)}
                  </span>
                  {r.section_path && (
                    <span className="text-[10px] text-slate-400 ml-1.5">
                      {r.section_path}
                    </span>
                  )}
                  {/* FAQ: 질문/답변 표시 */}
                  {r.faq_question ? (
                    <div className="mt-1">
                      <p className="font-medium text-slate-700">Q. {r.faq_question}</p>
                      {r.faq_answer && <p className="text-slate-500 mt-0.5 line-clamp-3">A. {r.faq_answer}</p>}
                    </div>
                  ) : (
                    <p className="text-slate-600 mt-1 line-clamp-2">{(r.text || "").slice(0, 150)}</p>
                  )}
                </div>
                {isPdf(r) && (
                  <div className="ml-2 shrink-0">
                    {expandedIdx === i ? (
                      <X className="w-4 h-4 text-slate-400" />
                    ) : (
                      <ImageIcon className="w-4 h-4 text-blue-500" />
                    )}
                  </div>
                )}
              </div>

              {/* PDF Highlight Image (lazy load) */}
              {isPdf(r) && expandedIdx === i && (
                <div className="border-t border-slate-200 bg-white p-2">
                  {imgLoading && (
                    <div className="flex items-center justify-center py-8 text-slate-400 text-xs">
                      <div className="animate-pulse">PDF 페이지 로딩 중...</div>
                    </div>
                  )}
                  <img
                    src={pdfImageUrl(r)}
                    alt={`${sourceLabel(r)} highlighted`}
                    className={`w-full rounded shadow-sm ${imgLoading ? "hidden" : ""}`}
                    onLoad={() => setImgLoading(false)}
                    onError={() => setImgLoading(false)}
                  />
                </div>
              )}

              {/* Notice: 링크 표시 */}
              {r.source_url && (
                <div className="px-2.5 pb-2">
                  <a href={r.source_url} target="_blank" rel="noreferrer"
                    className="text-[10px] text-blue-500 hover:underline flex items-center gap-0.5">
                    <ExternalLink className="w-3 h-3" />
                    {r.title || r.source_url}
                  </a>
                </div>
              )}
            </div>
          ))}

          {/* Related URLs */}
          {sourceUrls && sourceUrls.length > 0 && (
            <div className="pt-1">
              <p className="text-[10px] font-bold text-slate-400 uppercase mb-1">{t(lang, "source.related")}</p>
              {sourceUrls.map((s, i) => (
                <a key={i} href={s.url} target="_blank" rel="noreferrer"
                  className="text-xs text-blue-600 hover:underline flex items-center gap-1 py-0.5">
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
