"use client";
import { useRef, useEffect, useState, use, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { Lang, TabId, ChatMessage as ChatMessageType } from "@/lib/types";
import { useSession } from "@/hooks/useSession";
import { useChat } from "@/hooks/useChat";
import ChatHeader from "@/components/chat/ChatHeader";
import ChatMessage from "@/components/chat/ChatMessage";
import StreamingMessage from "@/components/chat/StreamingMessage";
import ThinkingAnimation from "@/components/chat/ThinkingAnimation";
import ChatInput from "@/components/chat/ChatInput";
import WelcomeScreen from "@/components/chat/WelcomeScreen";
import StarRating from "@/components/chat/StarRating";
import SourcePanel from "@/components/chat/SourcePanel";
import Sidebar from "@/components/layout/Sidebar";
import MobileBottomBar from "@/components/chat/MobileBottomBar";
import AcademicReport from "@/components/layout/AcademicReport";
import { useAuth } from "@/hooks/useAuth";
import { apiFetch } from "@/lib/api";

export default function ChatPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang: rawLang } = use(params);
  const lang = (rawLang === "en" ? "en" : "ko") as Lang;
  const router = useRouter();

  const { sessionId, session, loading, updateProfile, refreshSession, resetSession } = useSession(lang);
  const { messages, isStreaming, streamText, sendMessage, clearMessages, setMessages } = useChat(sessionId);
  const { user, isLoggedIn, logout } = useAuth();

  const searchParams = useSearchParams();
  const [sidebarOpen, setSidebarOpen] = useState(
    typeof window !== "undefined" ? window.innerWidth >= 1024 : false
  );
  const [activeTab, setActiveTab] = useState<TabId>("chat");

  // 로그아웃: JWT blacklist + 서버 세션 purge + 쿠키 삭제 + 새 세션 생성 + 로컬 메시지 초기화.
  // 이전 사용자의 성적표·대화가 UI에 남지 않도록 전 체인 정리.
  const handleLogout = useCallback(async () => {
    await logout({ sessionId });
    clearMessages();
    setActiveTab("chat");
    await resetSession();
  }, [logout, sessionId, resetSession, clearMessages]);

  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamText]);

  const handleToggleLang = () => {
    const newLang = lang === "ko" ? "en" : "ko";
    window.location.href = `/${newLang}/chat`;
  };

  const handleNewChat = () => {
    clearMessages();
    setActiveTab("chat");
  };

  const handleAskFromReport = (question: string) => {
    setActiveTab("chat");
    setTimeout(() => sendMessage(question), 100);
  };

  const handleTabChange = (tab: TabId) => {
    setActiveTab(tab);
  };

  /** 알림 Bell 클릭 → 해당 FAQ 답변을 합성 메시지로 삽입. */
  const openFaqInChat = useCallback(async (faqId: string) => {
    try {
      const f = await apiFetch<{ id: string; category: string; question: string; answer: string }>(
        `/api/chat/faq/${encodeURIComponent(faqId)}`,
      );
      if (!f.answer) return;
      const heading = lang === "ko" ? "🔔 학사지원팀의 정정 답변" : "🔔 Corrected answer from the Academic Team";
      const qLine = f.question ? `**Q.** ${f.question}\n\n` : "";
      const msg: ChatMessageType = {
        role: "assistant",
        content: `${heading}\n\n${qLine}${f.answer}`,
        rated: false,
      };
      setMessages((prev) => [...prev, msg]);
      setActiveTab("chat");
    } catch {
      // 실패해도 무소음 — 알림 읽음 처리는 이미 bell 내부에서 수행됨
    }
  }, [lang, setMessages]);

  /** ?faq=<id> 쿼리 감지 → 자동 오픈 + URL 정리 */
  useEffect(() => {
    const fid = searchParams?.get("faq");
    if (fid) {
      openFaqInChat(fid);
      // 쿼리 정리 (뒤로가기 시 재실행 방지)
      const url = new URL(window.location.href);
      url.searchParams.delete("faq");
      router.replace(url.pathname + (url.search || ""));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-white">
        <div className="flex gap-2">
          <div className="w-3 h-3 bg-blue-400 rounded-full animate-bounce" />
          <div className="w-3 h-3 bg-blue-400 rounded-full animate-bounce [animation-delay:0.2s]" />
          <div className="w-3 h-3 bg-blue-400 rounded-full animate-bounce [animation-delay:0.4s]" />
        </div>
      </div>
    );
  }

  const hasMessages = messages.length > 0 || isStreaming;
  const headerTitle = activeTab === "report" ? "report.title" : undefined;

  return (
    <div className="flex h-dvh bg-white overflow-hidden">
      {/* Sidebar — visible on lg+, slide-in overlay on mobile */}
      <Sidebar
        lang={lang}
        profile={session?.user_profile}
        sessionId={sessionId}
        hasTranscript={session?.has_transcript}
        authUser={user ? { nickname: user.nickname, student_id: user.student_id, department: user.department } : null}
        messages={messages}
        onSelectQuestion={sendMessage}
        onClearChat={clearMessages}
        onNewChat={handleNewChat}
        onTabChange={handleTabChange}
        onLogout={handleLogout}
        activeTab={activeTab}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0 h-full relative">
        {/* Header */}
        <ChatHeader
          lang={lang}
          onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
          onToggleLang={handleToggleLang}
          profile={session?.user_profile}
          authNickname={user?.nickname}
          onOpenFaq={openFaqInChat}
        />

        {/* Tab content */}
        {activeTab === "chat" && (
          <>
            <main className="flex-1 overflow-y-auto pb-32">
              <div className="max-w-4xl mx-auto p-4 md:p-6">
                {!hasMessages ? (
                  <WelcomeScreen lang={lang} onSelect={sendMessage} hasTranscript={session?.has_transcript} />
                ) : (
                  <div className="space-y-6">
                    {messages.map((msg, i) => (
                      <div key={i}>
                        <ChatMessage msg={msg} />
                        {msg.role === "assistant" && (
                          <div className="ml-13 mt-1">
                            <SourcePanel lang={lang} results={msg.results} sourceUrls={msg.sourceUrls} />
                            {sessionId && !msg.rated && (
                              <StarRating lang={lang} sessionId={sessionId} messageIndex={i} />
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                    {isStreaming && (
                      streamText ? (
                        <StreamingMessage text={streamText} />
                      ) : (
                        <ThinkingAnimation lang={lang} />
                      )
                    )}
                    <div ref={bottomRef} />
                  </div>
                )}
              </div>
            </main>
            <ChatInput lang={lang} onSend={sendMessage} disabled={isStreaming} />
          </>
        )}

        {activeTab === "report" && (
          <main className="flex-1 overflow-y-auto pb-24 lg:pb-6">
            <AcademicReport
              lang={lang}
              sessionId={sessionId}
              hasTranscript={session?.has_transcript ?? false}
              onAskAI={handleAskFromReport}
              onUploaded={refreshSession}
            />
          </main>
        )}

        {activeTab === "notifications" && (
          <main className="flex-1 overflow-y-auto flex items-center justify-center">
            <div className="text-center text-slate-400 space-y-2">
              <div className="text-4xl">🔔</div>
              <p className="font-semibold">{lang === "ko" ? "알림이 없습니다" : "No notifications"}</p>
            </div>
          </main>
        )}

        {activeTab === "profile" && (
          <main className="flex-1 overflow-y-auto flex items-center justify-center">
            <div className="text-center text-slate-400 space-y-2">
              <div className="text-4xl">👤</div>
              <p className="font-semibold">
                {session?.user_profile?.department || (lang === "ko" ? "프로필 미설정" : "Profile not set")}
              </p>
              <p className="text-xs">
                {session?.user_profile?.student_id
                  ? `${session.user_profile.student_id}${lang === "ko" ? "학번" : ""}`
                  : ""}
              </p>
            </div>
          </main>
        )}

        {/* Mobile bottom tab bar — 비활성 (채팅 입력창 겹침 방지) */}
        {/* <MobileBottomBar lang={lang} activeTab={activeTab} onTabChange={handleTabChange} /> */}
      </div>
    </div>
  );
}
