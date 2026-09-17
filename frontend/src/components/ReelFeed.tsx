import { useCallback, useEffect, useRef, useState } from "react";
import { api, Doc, ReelsResponse } from "../api";
import ReelCard from "./ReelCard";

export default function ReelFeed({ doc, onBack }: { doc: Doc; onBack: () => void }) {
  const [data, setData] = useState<ReelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
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
      const active = !d || d.status === "GENERATING" || d.reels.some((r) => ["PENDING", "PLANNING", "NARRATING", "RENDERING"].includes(r.status));
      timer.current = window.setTimeout(tick, active ? 3000 : 15000);
    };
    tick();
    return () => {
      alive = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [refresh]);

  if (!data) return <div className="card">{error ? <div className="error">{error}</div> : "Loading your shorts…"}</div>;

  const total = data.reels.length;
  const done = data.reels.filter((r) => r.status === "COMPLETED").length;
  const generating = data.status === "GENERATING";
  const aiEnhanced = !!data.reels.find((r) => r.ai_enhanced);

  return (
    <>
      <div className="card">
        <div className="row spread">
          <div>
            <h2>{doc.title || "Your shorts"}</h2>
            <p className="muted" style={{ margin: 0 }}>
              {generating ? data.progress || "Generating…" : `${done} of ${total} shorts ready`}
            </p>
          </div>
          <button className="btn ghost sm" onClick={onBack}>
            ← Edit topics
          </button>
        </div>
        {total > 0 && (
          <div className="progressbar">
            <div style={{ width: `${Math.round((done / total) * 100)}%` }} />
          </div>
        )}
        {data.status === "FAILED" && <div className="error">Generation failed. {doc.error || ""} Try again from the topic map.</div>}
        {error && <div className="error">{error}</div>}
      </div>

      {data.reels.map((r, i) => (
        <ReelCard key={r.reel_id} reel={r} index={i} aiEnhanced={aiEnhanced} onChanged={refresh} />
      ))}

      {total === 0 && !generating && <div className="card muted">No shorts yet. Go back and pick some topics.</div>}
    </>
  );
}
