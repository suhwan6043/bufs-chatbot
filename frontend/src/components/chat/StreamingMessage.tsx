"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Sparkles } from "lucide-react";

export default function StreamingMessage({ text }: { text: string }) {
  const escaped = text.replace(/(?<!\~)\~(?!\~)/g, "\\~");

  return (
    <div className="flex justify-start animate-fade-in">
      <div className="w-10 h-10 rounded-xl bg-blue-600 flex items-center justify-center shrink-0 mr-3 shadow-lg shadow-blue-200 border-2 border-white">
        <Sparkles className="w-5 h-5 text-white" />
      </div>
      <div className="max-w-[85%] lg:max-w-[75%] p-4 bg-slate-50 border border-slate-200 rounded-[1.5rem] rounded-tl-none shadow-sm">
        <div className="prose prose-sm max-w-none text-slate-800">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{escaped + " \u258C"}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
