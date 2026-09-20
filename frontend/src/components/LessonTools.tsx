import { useEffect, useRef, useState } from "react";
import { api, Doc, fmtDuration } from "../api";

/** "Full lesson" (all shorts stitched into one MP4) and "Notes" (markdown export). */
export default function LessonTools({ doc, ready, variant }: { doc: Doc; ready: boolean; variant: "bar" | "rail" }) {
  const [combined, setCombined] = useState<{ status?: string; url?: string | null; download?: string | null; duration?: number }>({
    status: doc.combined_status,
    url: doc.combined_url,
    download: doc.combined_download_url,
    duration: doc.combined_duration,
  });
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    setCombined({ status: doc.combined_status, url: doc.combined_url, download: doc.combined_download_url, duration: doc.combined_duration });
  }, [doc.combined_status, doc.combined_url, doc.combined_download_url, doc.combined_duration]);

  // poll while the combined lesson is being stitched
  useEffect(() => {
    if (combined.status !== "BUILDING") return;
    let alive = true;
    const tick = async () => {
      try {
        const d = await api.doc(doc.document_id);
        if (!alive) return;
        setCombined({ status: d.combined_status, url: d.combined_url, download: d.combined_download_url, duration: d.combined_duration });
        if (d.combined_status === "BUILDING") timer.current = window.setTimeout(tick, 2500);
      } catch {
        if (alive) timer.current = window.setTimeout(tick, 4000);
      }
    };
    timer.current = window.setTimeout(tick, 2500);
    return () => {
      alive = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [combined.status, doc.document_id]);

  async function build() {
    setError(null);
    try {
      await api.combine(doc.document_id);
      setCombined({ status: "BUILDING" });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const building = combined.status === "BUILDING";
  const cls = `lesson-tools ${variant}`;

  return (
    <div className={cls}>
      {combined.status === "COMPLETED" && combined.url ? (
        <a className="tool" href={combined.url} target="_blank" rel="noreferrer" title="Watch every short as one lesson">
          ▶ {variant === "bar" ? "Lesson" : `Full lesson${combined.duration ? ` · ${fmtDuration(combined.duration)}` : ""}`}
        </a>
      ) : (
        <button type="button" className="tool" onClick={build} disabled={!ready || building} title="Stitch all shorts into one lesson video">
          {building ? "Building…" : variant === "bar" ? "Lesson" : "Full lesson"}
        </button>
      )}
      {combined.status === "COMPLETED" && combined.download && (
        <a className="tool" href={combined.download} download title="Download the full lesson as MP4">
          {variant === "bar" ? "↓ MP4" : "↓ Lesson MP4"}
        </a>
      )}
      <a className="tool" href={api.notesUrl(doc.document_id)} download title="Download revision notes (Markdown)">
        {variant === "bar" ? "Notes" : "Notes ↓"}
      </a>
      {error && <span className="tool-error">{error}</span>}
    </div>
  );
}
