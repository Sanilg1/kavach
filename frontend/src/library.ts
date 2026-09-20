/** Device library: documents this browser has uploaded, remembered without an account.
 *  The document id is the key to everything stored in S3/DynamoDB. */
import { Doc } from "./api";

const KEY = "kavach:library";

export interface LibraryEntry {
  id: string;
  title: string;
  filename: string;
  status: string;
  pages: number;
  shorts?: number;
  language?: string;
  created: string;
  updated: string;
}

export function loadLibrary(): LibraryEntry[] {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? (JSON.parse(raw) as LibraryEntry[]) : [];
    return list.sort((a, b) => (b.updated || b.created).localeCompare(a.updated || a.created));
  } catch {
    return [];
  }
}

function save(list: LibraryEntry[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, 50)));
  } catch {
    /* storage unavailable (private mode) - the doc link still works */
  }
}

export function rememberDoc(doc: Doc) {
  const list = loadLibrary();
  const now = new Date().toISOString();
  const i = list.findIndex((e) => e.id === doc.document_id);
  const entry: LibraryEntry = {
    id: doc.document_id,
    title: doc.title || doc.filename || "Untitled notes",
    filename: doc.filename,
    status: doc.status,
    pages: doc.page_count,
    shorts: doc.reels_completed ?? doc.estimated_shorts,
    language: (doc as Doc & { language?: string }).language,
    created: i >= 0 ? list[i].created : now,
    updated: now,
  };
  if (i >= 0) list[i] = entry;
  else list.unshift(entry);
  save(list);
}

export function forgetDoc(id: string) {
  save(loadLibrary().filter((e) => e.id !== id));
}
