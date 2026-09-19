import { Doc } from "../api";

const STEPS: { key: string; label: string; reached: (d: Doc) => number }[] = [
  { key: "read", label: "Reading document", reached: (d) => rank(d) },
  { key: "concepts", label: "Identifying concepts", reached: (d) => rank(d) - 1 },
  { key: "relations", label: "Building concept relationships", reached: (d) => rank(d) - 1 },
  { key: "path", label: "Creating learning path", reached: (d) => rank(d) - 2 },
];

// FAILED after concepts were found means generation failed, not analysis
const analysisFailed = (d: Doc) => d.status === "FAILED" && !d.concept_count;

function rank(d: Doc): number {
  // UPLOADED=0 PROCESSING=1 ANALYZING=2 READY+=3
  if (analysisFailed(d)) return -1;
  return { UPLOADED: 0, PROCESSING: 1, ANALYZING: 2, READY: 3, GENERATING: 3, COMPLETED: 3, FAILED: 3 }[d.status] ?? 0;
}

export default function Analysis({ doc, onContinue, onRestart }: { doc: Doc; onContinue: () => void; onRestart: () => void }) {
  const failed = analysisFailed(doc);
  const ready = rank(doc) >= 3;
  const suit = doc.suitability;

  return (
    <div className="card">
      <h2>{ready ? "Your PDF is ready" : "Analyzing your PDF…"}</h2>
      <p className="muted">
        {doc.filename} · {doc.page_count} pages
      </p>
      <ul className="checklist">
        {STEPS.map((s, i) => {
          const r = rank(doc);
          // step i is done when analysis has progressed past it
          const done = ready || r > i;
          const active = !ready && !failed && r === i;
          return (
            <li key={s.key} className={done ? "done" : active ? "active" : ""}>
              <span className="tick">{done ? "✓" : ""}</span>
              {s.label}
            </li>
          );
        })}
      </ul>

      {ready && doc.brain && (
        <p className="small muted" style={{ margin: "0 0 6px" }}>
          {doc.brain.startsWith("bedrock") ? (
            <span className="chip purple">Analysed by Claude on Amazon Bedrock</span>
          ) : (
            <span className="chip orange">Offline analysis · Bedrock quota pending</span>
          )}
        </p>
      )}
      {ready && (
        <div className="bigstat">
          {doc.concept_count} concepts found · {doc.estimated_shorts} shorts · ~{Math.max(1, Math.round((doc.estimated_duration || 0) / 60))} min
        </div>
      )}

      {suit && (
        <div className={suit.quality === "good" ? "info" : "notice"}>
          <strong>PDF quality: {suit.quality}</strong>
          {suit.warnings.length > 0 && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {suit.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          {suit.recommendations.length > 0 && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {suit.recommendations.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          {!suit.suitable && <p style={{ margin: "6px 0 0" }}>This PDF may be difficult to process. You can still continue, but expect gaps.</p>}
        </div>
      )}

      {failed && <div className="error">Analysis failed: {doc.error || "unknown error"}</div>}

      <div className="row actions-stack" style={{ marginTop: 18 }}>
        {ready && (
          <button className="btn" onClick={onContinue}>
            See your revision path →
          </button>
        )}
        {(failed || ready) && (
          <button className="btn ghost" onClick={onRestart}>
            Upload a different PDF
          </button>
        )}
      </div>
    </div>
  );
}
