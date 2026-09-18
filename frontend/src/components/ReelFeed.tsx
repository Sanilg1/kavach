import { useCallback, useEffect, useRef, useState } from "react";
import { api, Doc, ReelsResponse } from "../api";
import LessonTools from "./LessonTools";
import ReelCard from "./ReelCard";

function storageKey(docId: string) {
  return `kavach:short:${docId}`;
}

// a shared link (#…&r=<reel_id>) wins over the remembered position
function initialShort(docId: string): string | null {
  const fromLink = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("r");
  if (fromLink) return fromLink;
  try {
    return localStorage.getItem(storageKey(docId));
  } catch {
    return null;
  }
}

export default function ReelFeed({ doc, onBack }: { doc: Doc; onBack: () => void }) {
  const [data, setData] = useState<ReelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(() => initialShort(doc.document_id));
  const [sheetOpen, setSheetOpen] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  const restored = useRef(false);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await api.reels(doc.document_id);
      setData(d);
      setError(null);
      return d;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, [doc.document_id]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const d = await refresh();
      if (!alive) return;
      const working = !d || d.status === "GENERATING" || d.reels.some((r) => ["PENDING", "PLANNING", "NARRATING", "RENDERING"].includes(r.status));
      timer.current = window.setTimeout(tick, working ? 3000 : 15000);
    };
    tick();
    return () => {
      alive = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [refresh]);

  const reels = data?.reels ?? [];
  const found = reels.findIndex((r) => r.reel_id === activeId);
  const index = found >= 0 ? found : 0;
  const ids = reels.map((r) => r.reel_id).join(",");

  // jump (without animation) to the remembered/shared short once the list first arrives
  useEffect(() => {
    if (restored.current || !reels.length || !feedRef.current) return;
    restored.current = true;
    const el = feedRef.current.querySelector<HTMLElement>(`[data-id="${activeId}"]`);
    if (el) feedRef.current.scrollTo({ top: el.offsetTop });
    else setActiveId(reels[0].reel_id);
  }, [ids]); // eslint-disable-line react-hooks/exhaustive-deps

  // the short that fills most of the viewport is the active one (plays, others pause)
  useEffect(() => {
    const root = feedRef.current;
    if (!root) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const en of entries) {
          if (en.isIntersecting) setActiveId((en.target as HTMLElement).dataset.id || null);
        }
      },
      { root, threshold: 0.6 },
    );
    root.querySelectorAll("[data-id]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [ids]);

  useEffect(() => {
    if (!activeId || !restored.current) return;
    try {
      localStorage.setItem(storageKey(doc.document_id), activeId);
    } catch {
      /* private mode etc. - position just isn't remembered */
    }
  }, [doc.document_id, activeId]);

  const indexRef = useRef(index);
  indexRef.current = index;
  const sheetRef = useRef(sheetOpen);
  sheetRef.current = sheetOpen;

  const go = useCallback((delta: number) => {
    const root = feedRef.current;
    if (!root) return;
    const items = root.querySelectorAll<HTMLElement>("[data-id]");
    const next = items[Math.max(0, Math.min(items.length - 1, indexRef.current + delta))];
    next?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // keyboard: arrows / page keys / j-k, but never while typing or while a sheet is open
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey || sheetRef.current) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (e.key === "ArrowDown" || e.key === "PageDown" || e.key === "j") {
        e.preventDefault();
        go(1);
      } else if (e.key === "ArrowUp" || e.key === "PageUp" || e.key === "k") {
        e.preventDefault();
        go(-1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);

  const total = reels.length;
  const done = reels.filter((r) => r.status === "COMPLETED").length;
  const generating = data?.status === "GENERATING";
  const aiEnhanced = !!reels.find((r) => r.ai_enhanced);

  return (
    <div className="shorts">
      <div className="shorts-viewport">
        {total > 0 ? (
          <div ref={feedRef} className={`shorts-feed${sheetOpen ? " locked" : ""}`}>
            {reels.map((r, i) => (
              <div key={r.reel_id} className="short-item" data-id={r.reel_id}>
                <ReelCard reel={r} index={i} total={total} aiEnhanced={aiEnhanced} active={i === index} onChanged={refresh} onSheetChange={setSheetOpen} />
              </div>
            ))}
          </div>
        ) : (
          <div className="shorts-empty">
            {!data && !error && <div className="spinner" />}
            <p>
              {!data
                ? error || "Loading your shorts…"
                : generating
                  ? data.progress || "Preparing your first short…"
                  : "No shorts yet. Go back and pick some topics."}
            </p>
          </div>
        )}

        <header className="shorts-top">
          <button type="button" className="shorts-back" onClick={onBack} aria-label="Back to topics" title="Back to topics">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M15 5 8 12l7 7" />
            </svg>
          </button>
          <div className="shorts-heading">
            <strong>{doc.title || "Your shorts"}</strong>
            {data && (generating || done < total) && <span>{generating ? data.progress || "Generating…" : `${done} of ${total} ready`}</span>}
          </div>
          {total > 0 && (
            <span className="shorts-counter">
              {index + 1}/{total}
            </span>
          )}
          {done > 0 && <LessonTools doc={doc} ready={!generating && done === total} variant="bar" />}
        </header>

        {data?.status === "FAILED" && (
          <div className="shorts-alert" role="alert">
            Generation failed. {doc.error || ""} Try again from the topic map.
          </div>
        )}
        {data && error && (
          <div className="shorts-alert" role="alert">
            {error}
          </div>
        )}
      </div>

      {total > 1 && (
        <nav className="shorts-nav" aria-label="Shorts navigation">
          <button type="button" aria-label="Previous short" title="Previous (↑)" disabled={index === 0} onClick={() => go(-1)}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m6 15 6-6 6 6" />
            </svg>
          </button>
          <button type="button" aria-label="Next short" title="Next (↓)" disabled={index === total - 1} onClick={() => go(1)}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
          <span className="count">
            {index + 1}
            <small>/{total}</small>
          </span>
          <span className="hint">↑ ↓ keys</span>
          {done > 0 && <LessonTools doc={doc} ready={!generating && done === total} variant="rail" />}
        </nav>
      )}
    </div>
  );
}
