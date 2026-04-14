"use client";
import { useRef, useEffect, useState, use } from "react";
import { useRouter } from "next/navigation";
import type { Lang, TabId } from "@/lib/types";
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

export default function ChatPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang: rawLang } = use(params);
  const lang = (rawLang === "en" ? "en" : "ko") as Lang;
  const router = useRouter();

  const { sessionId, session, loading, updateProfile } = useSession(lang);
  const { messages, isStreaming, streamText, sendMessage, clearMessages, setMessages } = useChat(sessionId);
  const { user, isLoggedIn, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(
    typeof window !== "undefined" ? window.innerWidth >= 1024 : false
  );
  const [activeTab, setActiveTab] = useState<TabId>("chat");

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
    <div className="flex h-screen bg-white overflow-hidden">
      {/* Sidebar — visible on lg+, slide-in overlay on mobile */}
      <Sidebar
        lang={lang}
        profile={session?.user_profile}
        sessionId={sessionId}
        hasTranscript={session?.has_transcript}
        authUser={user ? { nickname: user.nickname, student_id: user.student_id, department: user.department } : null}
        onSelectQuestion={sendMessage}
        onClearChat={clearMessages}
        onNewChat={handleNewChat}
        onTabChange={handleTabChange}
        onLogout={logout}
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
        />

        {/* Tab content */}
        {activeTab === "chat" && (
          <>
            <main className="flex-1 overflow-y-auto pb-48 lg:pb-32">
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

        {/* Mobile bottom tab bar */}
        <MobileBottomBar lang={lang} activeTab={activeTab} onTabChange={handleTabChange} />
      </div>
    </div>
  );
}
