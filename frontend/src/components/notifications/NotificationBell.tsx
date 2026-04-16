"use client";
import { Bell } from "lucide-react";
import { useEffect } from "react";
import { useNotifications } from "@/hooks/useNotifications";
import NotificationPanel from "./NotificationPanel";

interface Props {
  /** 클릭 시 라우팅 콜백 — FAQ 항목 클릭 → 채팅 화면으로 이동 */
  onOpenFaq?: (faqId: string) => void;
}

export default function NotificationBell({ onOpenFaq }: Props) {
  const {
    items, unreadCount, loading, open,
    refreshCount, markRead, markAllRead, openPanel, closePanel,
    isLoggedIn,
  } = useNotifications();

  // 마운트 시 1회 뱃지 카운트 갱신 (페이지 방문 시만 정책)
  useEffect(() => {
    if (isLoggedIn) refreshCount();
  }, [isLoggedIn, refreshCount]);

  if (!isLoggedIn) return null;

  return (
    <>
      <button
        onClick={open ? closePanel : openPanel}
        aria-label="알림"
        title={unreadCount > 0 ? `미읽음 ${unreadCount}건` : "알림"}
        className="p-2 hover:bg-slate-100 rounded-full relative transition-colors"
      >
        <Bell className="w-5 h-5 text-slate-600" />
        {unreadCount > 0 && (
          <span className="absolute top-0.5 right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center border-2 border-white">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <NotificationPanel
          items={items}
          loading={loading}
          onClose={closePanel}
          onMarkRead={markRead}
          onMarkAllRead={markAllRead}
          onOpenFaq={onOpenFaq}
        />
      )}
    </>
  );
}
