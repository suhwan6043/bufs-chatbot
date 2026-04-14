export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  rated?: boolean;
  rating?: number;
  sourceUrls?: SourceURL[];
  results?: SearchResultItem[];
  intent?: string;
  durationMs?: number;
}

export interface SourceURL {
  title: string;
  url: string;
}

export interface SearchResultItem {
  text: string;
  score: number;
  source: string;
  page_number: number;
  doc_type: string;
  in_context: boolean;
}

export interface SessionInfo {
  session_id: string;
  lang: string;
  user_profile: UserProfile | null;
  has_transcript: boolean;
  messages_count: number;
}

export interface UserProfile {
  student_id: string;
  department: string;
  student_type: string;
}

export interface StreamDoneData {
  answer: string;
  source_urls: SourceURL[];
  results: SearchResultItem[];
  intent: string;
  duration_ms: number;
}

export type Lang = "ko" | "en";

// ── 신규 타입 (UI 리디자인) ──
export type TabId = "chat" | "report" | "notifications" | "profile";

export interface TranscriptStatus {
  has_transcript: boolean;
  remaining_seconds: number;
  masked_name: string;
  gpa: number;
  total_acquired: number;
  total_required: number;
  total_shortage: number;
  progress_pct: number;
  dual_major: string;
  dual_shortage: number;
}
