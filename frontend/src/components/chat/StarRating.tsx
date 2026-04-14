"use client";
import { useState } from "react";
import { apiFetch } from "@/lib/api";
import type { Lang } from "@/lib/types";
import { t } from "@/lib/i18n";

interface Props { lang: Lang; sessionId: string; messageIndex: number; onRated?: () => void }

export default function StarRating({ lang, sessionId, messageIndex, onRated }: Props) {
  const [rating, setRating] = useState(0);
  const [submitted, setSubmitted] = useState(false);
  const [hover, setHover] = useState(0);

  const submit = async (star: number) => {
    setRating(star);
    setSubmitted(true);
    try {
      await apiFetch("/api/rating", { method: "POST", body: JSON.stringify({ session_id: sessionId, message_index: messageIndex, rating: star }) });
    } catch { /* ignore */ }
    onRated?.();
  };

  if (submitted) return <p className="text-xs text-muted mt-1">{t(lang, "rating.done")} {"★".repeat(rating)}{"☆".repeat(5 - rating)}</p>;

  return (
    <div className="flex items-center gap-0.5 mt-1">
      <span className="text-xs text-muted mr-1">{t(lang, "rating.prompt")}</span>
      {[1, 2, 3, 4, 5].map((s) => (
        <button key={s} onClick={() => submit(s)} onMouseEnter={() => setHover(s)} onMouseLeave={() => setHover(0)}
          className="text-base bg-transparent border-none cursor-pointer transition-colors"
          style={{ color: s <= (hover || rating) ? "#f59e0b" : "#d1d5db" }}>★</button>
      ))}
    </div>
  );
}
