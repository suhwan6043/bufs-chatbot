"use client";
import { X, CheckCheck, MessageSquareText, FilePenLine, Bell } from "lucide-react";
import { useEffect } from "react";
import type { NotificationItem } from "@/hooks/useNotifications";

interface Props {
  items: NotificationItem[];
  loading: boolean;
  onClose: () => void;
  onMarkRead: (id: number) => void;
  onMarkAllRead: () => void;
  onOpenFaq?: (faqId: string) => void;
}

function formatTime(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = Date.now();
    const diff = now - d.getTime();
    const min = Math.floor(diff / 60000);
    if (min < 1) return "방금 전";
    if (min < 60) return `${min}분 전`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}시간 전`;
    const day = Math.floor(hr / 24);
    if (day < 7) return `${day}일 전`;
    return d.toLocaleDateString("ko-KR");
  } catch { return iso.slice(0, 10); }
}

function KindIcon({ kind }: { kind: string }) {
  if (kind === "faq_updated") {
    return <FilePenLine className="w-4 h-4 text-amber-500" />;
  }
  return <MessageSquareText className="w-4 h-4 text-emerald-500" />;
}

export default function NotificationPanel({
  items, loading, onClose, onMarkRead, onMarkAllRead, onOpenFaq,
}: Props) {
  // ESC 로 닫기
  useEffect(() => {
    const fn = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [onClose]);

  const handleClick = (n: NotificationItem) => {
    if (!n.read) onMarkRead(n.id);
    if (n.faq_id && onOpenFaq) {
      onOpenFaq(n.faq_id);
      onClose();
    }
  };

  const unreadExists = items.some((n) => !n.read);

  return (
    <>
      {/* 백드롭 — 모바일에서 풀스크린, 데스크톱은 투명 */}
      <div
        onClick={onClose}
        className="fixed inset-0 z-40 bg-black/30 md:bg-transparent"
        aria-hidden="true"
      />

      {/* 패널 */}
      <div
        role="dialog"
        aria-label="알림 목록"
        className={[
          "fixed z-50",
          // 모바일: 전체화면 슬라이드
          "inset-x-0 bottom-0 top-14 w-full rounded-t-2xl",
          // 데스크톱: 우측 상단 드롭다운
          "md:inset-auto md:top-16 md:right-4 md:bottom-auto md:w-96 md:rounded-2xl",
          "bg-white shadow-2xl border border-slate-200 flex flex-col overflow-hidden",
        ].join(" ")}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-slate-50">
          <h3 className="font-bold text-slate-900 text-sm">알림</h3>
          <div className="flex items-center gap-1">
            {unreadExists && (
              <button
                onClick={onMarkAllRead}
                className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium text-slate-600 hover:text-slate-900 hover:bg-white rounded-md transition-colors"
                title="전체 읽음 처리"
              >
                <CheckCheck className="w-3.5 h-3.5" />
                모두 읽음
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 hover:bg-white rounded-md"
              aria-label="닫기"
            >
              <X className="w-4 h-4 text-slate-500" />
            </button>
          </div>
        </div>

        {/* 목록 */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="p-8 text-center text-xs text-slate-400">불러오는 중...</div>
          ) : items.length === 0 ? (
            <div className="p-10 text-center">
              <Bell className="w-10 h-10 text-slate-200 mx-auto mb-2" />
              <p className="text-xs text-slate-400">아직 받은 알림이 없습니다.</p>
            </div>
          ) : (
            <ul className="divide-y divide-slate-100">
              {items.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => handleClick(n)}
                    className={[
                      "w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors flex gap-3",
                      n.read ? "opacity-70" : "bg-blue-50/40",
                    ].join(" ")}
                  >
                    <div className="shrink-0 mt-0.5">
                      <KindIcon kind={n.kind} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-2">
                        <p className={`text-xs font-semibold ${n.read ? "text-slate-700" : "text-slate-900"} line-clamp-1`}>
                          {n.title}
                        </p>
                        {!n.read && (
                          <span className="w-2 h-2 bg-blue-600 rounded-full shrink-0 mt-1.5" />
                        )}
                      </div>
                      {n.body && (
                        <p className="text-[11px] text-slate-500 mt-0.5 line-clamp-2 leading-snug">
                          {n.body}
                        </p>
                      )}
                      <p className="text-[10px] text-slate-400 mt-1">{formatTime(n.created_at)}</p>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}

