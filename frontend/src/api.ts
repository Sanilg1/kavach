export const API_URL = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") || "http://localhost:8000";

export type DocStatus = "UPLOADED" | "PROCESSING" | "ANALYZING" | "READY" | "GENERATING" | "COMPLETED" | "FAILED";
export type ReelStatus = "PENDING" | "PLANNING" | "NARRATING" | "RENDERING" | "COMPLETED" | "FAILED";

export interface Suitability {
  suitable: boolean;
  page_count: number;
  quality: "good" | "fair" | "poor";
  warnings: string[];
  recommendations: string[];
}

export interface Uncertainty {
  uncertainty?: boolean;
  reason: string;
  source_pages: number[];
}

export interface Doc {
  document_id: string;
  filename: string;
  page_count: number;
  status: DocStatus;
  progress?: string;
  title?: string;
  summary?: string;
  quality?: string;
  suitability?: Suitability;
  concept_count?: number;
  estimated_shorts?: number;
  estimated_duration?: number;
  uncertainties?: Uncertainty[];
  error?: string;
  reels_completed?: number;
  reels_failed?: number;
}

export interface Topic {
  topic_id: string;
  name: string;
  description: string;
  source_pages: number[];
  prerequisites: string[];
  difficulty: "easy" | "medium" | "hard";
  estimated_shorts: number;
  estimated_duration: number;
  learning_order: number;
  recommended: boolean;
  selected: boolean;
  user_added?: boolean;
}

export interface TopicsResponse {
  document_id: string;
  title?: string;
  summary?: string;
  status: DocStatus;
  topics: Topic[];
  uncertainties: Uncertainty[];
  estimated_shorts: number;
  estimated_duration: number;
}

export interface QuickCheck {
  question: string;
  options: string[];
  answer: number;
  explanation?: string;
}

export interface Reel {
  reel_id: string;
  document_id: string;
  topic_id: string;
  topic_name: string;
  learning_order: number;
  part: number;
  title: string;
  status: ReelStatus;
  duration?: number;
  sources?: number[];
  sources_label?: string;
  ai_added_context?: string[];
  uncertainty?: Uncertainty | null;
  quick_check?: QuickCheck | null;
  script?: string;
  video_url?: string | null;
  thumb_url?: string | null;
  error?: string;
  feedback?: string;
  regenerated?: number;
  ai_enhanced?: boolean;
}

export interface ReelsResponse {
  document_id: string;
  status: DocStatus;
  progress?: string;
  reels: Reel[];
}

export interface Answer {
  answer: string;
  sources: number[];
  ai_added_context: string[];
  uncertainty?: string | { reason: string } | null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_URL}${path}`, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = j.detail || msg;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  upload(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    return req<Doc>("/documents", { method: "POST", body: fd });
  },
  doc: (id: string) => req<Doc>(`/documents/${id}`),
  topics: (id: string) => req<TopicsResponse>(`/documents/${id}/topics`),
  updateTopics: (id: string, body: { selected_topic_ids: string[]; order: string[]; added_topics: { name: string; source_pages: number[] }[] }) =>
    req<TopicsResponse>(`/documents/${id}/topics`, json(body)),
  generate: (id: string, ai_enhanced: boolean) => req<{ status: string }>(`/documents/${id}/generate`, json({ ai_enhanced })),
  reels: (id: string) => req<ReelsResponse>(`/documents/${id}/reels`),
  reel: (id: string) => req<Reel>(`/reels/${id}`),
  ask: (id: string, question: string, ai_enhanced: boolean) => req<Answer>(`/reels/${id}/ask`, json({ question, ai_enhanced })),
  regenerate: (id: string, feedback: string, ai_enhanced: boolean) => req<{ status: string }>(`/reels/${id}/regenerate`, json({ feedback, ai_enhanced })),
  feedback: (id: string, feedback: string) => req<{ ok: boolean }>(`/reels/${id}/feedback`, json({ feedback })),
};

export function fmtDuration(sec: number): string {
  if (!sec) return "0s";
  if (sec < 90) return `~${Math.round(sec)}s`;
  return `~${Math.round(sec / 60)} min`;
}

export function fmtPages(pages: number[] | undefined): string {
  if (!pages || pages.length === 0) return "";
  const p = [...new Set(pages)].sort((a, b) => a - b);
  const runs: string[] = [];
  let s = p[0];
  let prev = p[0];
  for (const x of p.slice(1)) {
    if (x === prev + 1) {
      prev = x;
      continue;
    }
    runs.push(s === prev ? `${s}` : `${s}–${prev}`);
    s = prev = x;
  }
  runs.push(s === prev ? `${s}` : `${s}–${prev}`);
  return (p.length === 1 ? "Page " : "Pages ") + runs.join(", ");
}
