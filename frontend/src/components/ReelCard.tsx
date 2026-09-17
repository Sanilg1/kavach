import { useState } from "react";
import { Answer, api, fmtPages, Reel } from "../api";
import QuickCheck from "./QuickCheck";

const FEEDBACK = [
  { key: "good", label: "👍 Good", regen: false },
  { key: "didnt_understand", label: "🤔 Didn't understand", regen: true },
  { key: "too_fast", label: "🐢 Too fast", regen: true },
  { key: "too_difficult", label: "🧠 Too difficult", regen: true },
  { key: "explain_differently", label: "🔄 Explain differently", regen: true },
];

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Waiting in queue",
  PLANNING: "Brain AI is writing the lesson",
  NARRATING: "Generating narration",
  RENDERING: "Drawing the whiteboard",
  FAILED: "Generation failed",
};

export default function ReelCard({ reel, index, aiEnhanced, onChanged }: { reel: Reel; index: number; aiEnhanced: boolean; onChanged: () => void }) {
  const [showQuiz, setShowQuiz] = useState(false);
  const [showAsk, setShowAsk] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(reel.feedback || null);
  const [regenMsg, setRegenMsg] = useState<string | null>(null);

  const done = reel.status === "COMPLETED" && reel.video_url;
  const busy = ["PLANNING", "NARRATING", "RENDERING", "PENDING"].includes(reel.status);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setAskError(null);
    setAnswer(null);
    try {
      setAnswer(await api.ask(reel.reel_id, question.trim(), aiEnhanced));
    } catch (err) {
      setAskError((err as Error).message);
    } finally {
      setAsking(false);
    }
  }

  async function feedback(key: string, regen: boolean) {
    setPicked(key);
    try {
      if (regen) {
        await api.regenerate(reel.reel_id, key, aiEnhanced);
        setRegenMsg("Regenerating this short with a different teaching approach…");
        onChanged();
      } else {
        await api.feedback(reel.reel_id, key);
        setRegenMsg("Thanks for the feedback!");
      }
    } catch (err) {
      setRegenMsg((err as Error).message);
    }
  }

  return (
    <div className="card reel">
      <div className="video">
        {done ? (
          <video controls playsInline preload="metadata" poster={reel.thumb_url || undefined} src={reel.video_url || undefined} />
        ) : (
          <div className="placeholder">
            {busy && <div className="spinner" />}
            <div>{STATUS_LABEL[reel.status] || reel.status}</div>
            {reel.status === "FAILED" && reel.error && <div className="small" style={{ maxWidth: 480, textAlign: "center" }}>{reel.error}</div>}
          </div>
        )}
      </div>
      <div className="body">
        <div className="kicker">
          #{index + 1} · {reel.topic_name}
          {reel.part > 1 || reel.title !== reel.topic_name ? ` · part ${reel.part}` : ""}
        </div>
        <h3 style={{ marginTop: 4 }}>{reel.title}</h3>
        <div className="row small muted">
          {reel.duration ? <span>{Math.round(reel.duration)} seconds</span> : null}
          {reel.regenerated ? <span className="chip blue">regenerated ×{reel.regenerated}</span> : null}
          {reel.ai_enhanced ? <span className="chip purple">AI-enhanced</span> : <span className="chip green">PDF only</span>}
        </div>

        {reel.sources && reel.sources.length > 0 && (
          <div className="sources">
            <strong>Sources</strong> · PDF — {fmtPages(reel.sources)}
          </div>
        )}
        {reel.ai_added_context && reel.ai_added_context.length > 0 && (
          <div className="ai-added">
            <strong>AI-added context</strong> (not in your PDF)
            <ul>
              {reel.ai_added_context.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        )}
        {reel.uncertainty && reel.uncertainty.reason && (
          <div className="uncertain">
            <strong>Source uncertainty</strong> · {reel.uncertainty.reason}
            {reel.uncertainty.source_pages?.length ? ` (${fmtPages(reel.uncertainty.source_pages)})` : ""}
          </div>
        )}

        {done && (
          <div className="actions">
            {reel.quick_check && (
              <button className="btn secondary sm" onClick={() => setShowQuiz((v) => !v)}>
                {showQuiz ? "Hide quick check" : "Quick check"}
              </button>
            )}
            <button className="btn ghost sm" onClick={() => setShowAsk((v) => !v)}>
              Ask a question
            </button>
          </div>
        )}

        {showQuiz && reel.quick_check && <QuickCheck qc={reel.quick_check} />}

        {showAsk && (
          <div className="ask">
            <form onSubmit={ask}>
              <input placeholder="e.g. Why does the server send SYN-ACK instead of just ACK?" value={question} onChange={(e) => setQuestion(e.target.value)} />
              <button className="btn sm" disabled={asking || !question.trim()}>
                {asking ? "Thinking…" : "Ask"}
              </button>
            </form>
            {askError && <div className="error">{askError}</div>}
            {answer && (
              <div className="answer">
                <div>{answer.answer}</div>
                {answer.sources.length > 0 && <div className="small muted" style={{ marginTop: 6 }}>Sources: PDF {fmtPages(answer.sources).toLowerCase()}</div>}
                {answer.ai_added_context.length > 0 && (
                  <div className="ai-added">
                    <strong>AI-added context</strong>
                    <ul>
                      {answer.ai_added_context.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {answer.uncertainty && <div className="uncertain">{typeof answer.uncertainty === "string" ? answer.uncertainty : answer.uncertainty.reason}</div>}
              </div>
            )}
          </div>
        )}

        {reel.script && (
          <details>
            <summary>Show transcript</summary>
            <div className="script">{reel.script}</div>
          </details>
        )}

        {done && (
          <div className="feedback">
            <div className="small muted" style={{ marginBottom: 6 }}>
              How was this explanation?
            </div>
            <div className="row">
              {FEEDBACK.map((f) => (
                <button key={f.key} className={picked === f.key ? "picked" : ""} onClick={() => feedback(f.key, f.regen)}>
                  {f.label}
                </button>
              ))}
            </div>
            {regenMsg && <div className="small muted" style={{ marginTop: 6 }}>{regenMsg}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
