import { useEffect, useRef, useState } from "react";
import { Answer, api, fmtPages, Reel } from "../api";
import { MOBILE_QUERY, useMediaQuery } from "../useMediaQuery";
import QuickCheck from "./QuickCheck";

const FEEDBACK = [
  {
    key: "good",
    icon: "👍",
    label: "Good",
    hint: "Keep this version",
    regen: false,
  },
  {
    key: "didnt_understand",
    icon: "🤔",
    label: "Didn't understand",
    hint: "Regenerate with a simpler explanation",
    regen: true,
  },
  {
    key: "too_fast",
    icon: "🐢",
    label: "Too fast",
    hint: "Regenerate at a slower pace",
    regen: true,
  },
  {
    key: "too_difficult",
    icon: "🧠",
    label: "Too difficult",
    hint: "Regenerate with easier wording",
    regen: true,
  },
  {
    key: "explain_differently",
    icon: "🔄",
    label: "Explain differently",
    hint: "Regenerate with a new approach",
    regen: true,
  },
];

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Waiting in queue",
  PLANNING: "Brain AI is writing the lesson",
  NARRATING: "Generating narration",
  RENDERING: "Drawing the whiteboard",
  FAILED: "Generation failed",
};

type Sheet = "about" | "quiz" | "ask" | "rate";

const TAB_LABEL: Record<Sheet, string> = {
  about: "Description",
  quiz: "Quick check",
  ask: "Ask",
  rate: "Rate",
};

const SHEET_TITLE: Record<Sheet, string> = {
  about: "About this short",
  quiz: "Quick check",
  ask: "Ask a question",
  rate: "How was this explanation?",
};

// small inline icons (stroke = currentColor) so the rail needs no icon dependency
const Icon = {
  quiz: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" />
      <path d="M9.4 9.3a2.7 2.7 0 0 1 5.2 1c0 1.8-2.6 2.3-2.6 3.9" />
      <circle cx="12" cy="17.2" r="0.6" fill="currentColor" />
    </svg>
  ),
  done: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" />
      <path d="m7.8 12.4 2.8 2.8 5.6-6" />
    </svg>
  ),
  ask: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20.5 11.6a8.2 8.2 0 0 1-11.9 7.3L3.5 20.5l1.6-4.6A8.2 8.2 0 1 1 20.5 11.6Z" />
    </svg>
  ),
  rate: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7.5 10.5v9.5H4.2a.7.7 0 0 1-.7-.7v-8.1c0-.4.3-.7.7-.7h3.3Zm0 0 3.9-6.6c.3-.5.9-.8 1.5-.6 1 .3 1.6 1.3 1.4 2.3l-.7 3.4h5a2 2 0 0 1 2 2.4l-1.3 6.1a2 2 0 0 1-2 1.5H7.5" />
    </svg>
  ),
  sources: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14 3.5H7a1.5 1.5 0 0 0-1.5 1.5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8Z" />
      <path d="M14 3.5V8h4.5M9 12.5h6M9 16h4" />
    </svg>
  ),
  share: (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M13.5 5.5 20 11.8l-6.5 6.2v-3.6c-4.6 0-7.6 1.4-9.9 4.6.8-4.8 3.5-9.4 9.9-10.2Z" />
    </svg>
  ),
};

export default function ReelCard({
  reel,
  index,
  total,
  aiEnhanced,
  active,
  onChanged,
  onSheetChange,
}: {
  reel: Reel;
  index: number;
  total: number;
  aiEnhanced: boolean;
  active: boolean;
  onChanged: () => void;
  onSheetChange: (open: boolean) => void;
}) {
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [quizDone, setQuizDone] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(reel.feedback || null);
  const [regenMsg, setRegenMsg] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  // shorts render portrait (9:16); older 16:9 renders get the letterboxed layout
  const [landscape, setLandscape] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const sheetTouchY = useRef<number | null>(null);
  const sheetEl = useRef<HTMLElement>(null);
  // phones: overlay rail + bottom sheets; desktop: an always-open side panel
  const mobile = useMediaQuery(MOBILE_QUERY);

  const done = reel.status === "COMPLETED" && !!reel.video_url;
  const busy = ["PLANNING", "NARRATING", "RENDERING", "PENDING"].includes(reel.status);
  const pages = reel.sources && reel.sources.length > 0 ? fmtPages(reel.sources) : "";
  const description = reel.script || "";
  const view: Sheet | null = mobile ? sheet : (sheet ?? "about");
  const tabs: Sheet[] = done ? ["about", ...(reel.quick_check ? (["quiz"] as Sheet[]) : []), "ask", "rate"] : ["about"];

  // Keep the video src stable across polls (S3 presigned URLs change every call and
  // would restart playback), but bust the cache after a regeneration.
  const srcRef = useRef<{ base: string; regen: number; src: string } | null>(null);
  if (reel.video_url) {
    const base = reel.video_url.split("?")[0];
    const regen = reel.regenerated || 0;
    if (!srcRef.current || srcRef.current.base !== base || srcRef.current.regen !== regen) {
      const bust = regen && !reel.video_url.includes("?") ? `?v=${regen}` : "";
      srcRef.current = { base, regen, src: reel.video_url + bust };
    }
  }
  const videoSrc = srcRef.current?.src;

  // only the short on screen plays; leaving it rewinds it, like Reels/Shorts
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (active) {
      v.play().catch(() => setPlaying(false)); // autoplay blocked: the play button shows
    } else {
      v.pause();
      v.currentTime = 0;
      setProgress(0);
    }
  }, [active, videoSrc]);

  useEffect(() => {
    if (!active) setSheet(null);
  }, [active]);

  useEffect(() => {
    onSheetChange(mobile && sheet !== null);
    // a closed sheet stays in the DOM for its slide animation; keep it out of tab order
    if (sheetEl.current) sheetEl.current.inert = mobile && sheet === null;
  }, [sheet, mobile, onSheetChange]);

  useEffect(() => {
    if (!sheet || !mobile) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSheet(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheet, mobile]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(t);
  }, [toast]);

  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const v = videoRef.current;
    if (!v || !v.duration) return;
    const r = e.currentTarget.getBoundingClientRect();
    v.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * v.duration;
  }

  async function share() {
    const url = `${window.location.origin}${window.location.pathname}#doc=${reel.document_id}&s=feed&r=${reel.reel_id}`;
    const data = {
      title: reel.title,
      text: `${reel.title} · Kavach revision short`,
      url,
    };
    try {
      if (navigator.share) {
        await navigator.share(data);
        return;
      }
      await navigator.clipboard.writeText(url);
      setToast("Link copied");
    } catch (err) {
      if ((err as Error).name !== "AbortError") setToast("Couldn't share this short");
    }
  }

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
        setRegenMsg("Regenerating this short with a different approach…");
        onChanged();
      } else {
        await api.feedback(reel.reel_id, key);
        setRegenMsg("Thanks for the feedback!");
      }
    } catch (err) {
      setRegenMsg((err as Error).message);
    }
  }

  const pickedIcon = FEEDBACK.find((f) => f.key === picked)?.icon;

  return (
    <article className={`short${landscape ? " is-landscape" : ""}`} aria-label={`Short ${index + 1} of ${total}: ${reel.title}`}>
      <div className="short-stage">
        {reel.thumb_url && <div className="short-backdrop" style={{ backgroundImage: `url("${reel.thumb_url}")` }} aria-hidden="true" />}

        <div className="short-video">
          <div className="short-screen">
            {done ? (
              <video
                ref={videoRef}
                playsInline
                preload={active ? "auto" : "metadata"}
                poster={reel.thumb_url || undefined}
                src={videoSrc}
                onClick={togglePlay}
                onLoadedMetadata={(e) => setLandscape(e.currentTarget.videoWidth > e.currentTarget.videoHeight)}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onTimeUpdate={(e) => {
                  const v = e.currentTarget;
                  if (v.duration) setProgress(v.currentTime / v.duration);
                }}
                // spec §17: offer the quick check right after the short
                onEnded={() => reel.quick_check && !quizDone && setSheet("quiz")}
              />
            ) : (
              <div className="short-status">
                {busy && <div className="spinner" />}
                <div>{STATUS_LABEL[reel.status] || reel.status}</div>
                {reel.status === "FAILED" && reel.error && <div className="small">{reel.error}</div>}
              </div>
            )}
            {done && !playing && (
              <button type="button" className="short-play" aria-label="Play" onClick={togglePlay}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M8 5.5v13l10.5-6.5Z" />
                </svg>
              </button>
            )}
          </div>
        </div>

        <div className="short-shade" aria-hidden="true" />

        {/* minimal caption (phones): the full description only opens when asked for */}
        {mobile && (
          <div className="short-info">
            <button type="button" className="short-caption" onClick={() => setSheet("about")} aria-label="Show full description">
              <span className="short-avatar" aria-hidden="true">
                {reel.learning_order || index + 1}
              </span>
              <span className="cap-text">
                <strong>{reel.title}</strong>
                <span>
                  {reel.topic_name} · Part {reel.part}
                </span>
              </span>
              <span className="more">more</span>
            </button>
          </div>
        )}

        {done && (
          <div className="short-progress" onClick={seek} role="presentation">
            <div style={{ width: `${progress * 100}%` }} />
          </div>
        )}

        {toast && (
          <div className="short-toast" role="status">
            {toast}
          </div>
        )}
      </div>

      {mobile && <div className={`sheet-backdrop${sheet ? " open" : ""}`} onClick={() => setSheet(null)} aria-hidden="true" />}
      <section
        ref={sheetEl}
        className={`sheet${view ? " open" : ""}${mobile ? "" : " side"}`}
        role={mobile ? "dialog" : "complementary"}
        aria-modal={mobile ? true : undefined}
        aria-label={view ? SHEET_TITLE[view] : undefined}
      >
        {mobile ? (
          <div
            className="sheet-grab"
            onTouchStart={(e) => (sheetTouchY.current = e.touches[0]?.clientY ?? null)}
            onTouchEnd={(e) => {
              const start = sheetTouchY.current;
              const end = e.changedTouches[0]?.clientY;
              if (start != null && end != null && end - start > 50) setSheet(null);
              sheetTouchY.current = null;
            }}
          >
            <span className="handle" />
            <div className="sheet-title">{sheet ? SHEET_TITLE[sheet] : ""}</div>
            <button type="button" className="sheet-close" aria-label="Close" onClick={() => setSheet(null)}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
        ) : (
          <header className="side-head">
            <div className="short-creator in-sheet">
              <span className="short-avatar" aria-hidden="true">
                {reel.learning_order || index + 1}
              </span>
              <span className="short-topic">{reel.topic_name}</span>
              <span className="short-pill">Part {reel.part}</span>
              {done && (
                <button type="button" className="side-share" onClick={share} aria-label="Share this short" title="Share">
                  {Icon.share}
                </button>
              )}
            </div>
            <h3>{reel.title}</h3>
            <div className="sheet-meta">
              {reel.duration ? <span>{Math.round(reel.duration)}s</span> : null}
              {pages && <span>PDF {pages.toLowerCase()}</span>}
              {reel.ai_enhanced ? <span className="chip purple">AI-enhanced</span> : <span className="chip green">PDF only</span>}
              {reel.regenerated ? <span className="chip blue">regenerated ×{reel.regenerated}</span> : null}
            </div>
            {tabs.length > 1 && (
              <div className="side-tabs" role="tablist">
                {tabs.map((t) => (
                  <button key={t} type="button" role="tab" aria-selected={view === t} className={view === t ? "on" : ""} onClick={() => setSheet(t)}>
                    {TAB_LABEL[t]}
                    {t === "quiz" && quizDone ? " ✓" : ""}
                  </button>
                ))}
              </div>
            )}
          </header>
        )}

        <div className="sheet-body">
          {view === "about" && (
            <>
              {mobile && (
                <>
                  <div className="short-creator in-sheet">
                    <span className="short-avatar" aria-hidden="true">
                      {reel.learning_order || index + 1}
                    </span>
                    <span className="short-topic">{reel.topic_name}</span>
                    <span className="short-pill">Part {reel.part}</span>
                  </div>
                  <h3>{reel.title}</h3>
                  <div className="sheet-meta">
                    {reel.duration ? <span>{Math.round(reel.duration)}s</span> : null}
                    {reel.ai_enhanced ? <span className="chip purple">AI-enhanced</span> : <span className="chip green">PDF only</span>}
                    {reel.regenerated ? <span className="chip blue">regenerated ×{reel.regenerated}</span> : null}
                  </div>
                </>
              )}
              {pages && (
                <div className="sources">
                  <strong>Sources</strong> · PDF — {pages}
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
              {description && (
                <div className="transcript">
                  <div className="label">Transcript</div>
                  <p>{description}</p>
                </div>
              )}
            </>
          )}

          {/* kept mounted so the chosen answer survives closing the sheet */}
          {reel.quick_check && (
            <div hidden={view !== "quiz"}>
              <QuickCheck qc={reel.quick_check} onDone={() => setQuizDone(true)} />
            </div>
          )}

          {view === "ask" && (
            <div className="ask">
              <form onSubmit={ask}>
                <input placeholder="Ask anything about this short…" value={question} onChange={(e) => setQuestion(e.target.value)} />
                <button className="btn sm" disabled={asking || !question.trim()}>
                  {asking ? "Thinking…" : "Ask"}
                </button>
              </form>
              {askError && <div className="error">{askError}</div>}
              {!answer && !asking && !askError && <p className="muted small">Answers are grounded in your PDF and cite the pages they use.</p>}
              {answer && (
                <div className="answer">
                  <div>{answer.answer}</div>
                  {answer.sources.length > 0 && (
                    <div className="small muted" style={{ marginTop: 8 }}>
                      Sources: PDF {fmtPages(answer.sources).toLowerCase()}
                    </div>
                  )}
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
                  {answer.uncertainty && (
                    <div className="uncertain">{typeof answer.uncertainty === "string" ? answer.uncertainty : answer.uncertainty.reason}</div>
                  )}
                </div>
              )}
            </div>
          )}

          {view === "rate" && (
            <div className="rate-list">
              {FEEDBACK.map((f) => (
                <button key={f.key} type="button" className={picked === f.key ? "picked" : ""} onClick={() => feedback(f.key, f.regen)}>
                  <span className="emoji" aria-hidden="true">
                    {f.icon}
                  </span>
                  <span className="txt">
                    <strong>{f.label}</strong>
                    <small>{f.hint}</small>
                  </span>
                </button>
              ))}
              {regenMsg && <p className="muted small">{regenMsg}</p>}
            </div>
          )}
        </div>
      </section>

      {/* action rail: phones only (desktop uses the side panel) */}
      {mobile && done && (
        <div className="short-rail">
          {reel.quick_check && (
            <button type="button" className={quizDone ? "is-done" : ""} onClick={() => setSheet("quiz")}>
              <span className="ico">{quizDone ? Icon.done : Icon.quiz}</span>
              <small>{quizDone ? "Done" : "Quiz"}</small>
            </button>
          )}
          <button type="button" onClick={() => setSheet("ask")}>
            <span className="ico">{Icon.ask}</span>
            <small>Ask</small>
          </button>
          <button type="button" className={picked ? "is-picked" : ""} onClick={() => setSheet("rate")}>
            <span className="ico">{pickedIcon ? <em>{pickedIcon}</em> : Icon.rate}</span>
            <small>Rate</small>
          </button>
          <button type="button" onClick={() => setSheet("about")}>
            <span className="ico">{Icon.sources}</span>
            <small>{reel.sources && reel.sources.length > 0 ? `p. ${reel.sources[0]}${reel.sources.length > 1 ? "+" : ""}` : "Info"}</small>
          </button>
          <button type="button" onClick={share}>
            <span className="ico">{Icon.share}</span>
            <small>Share</small>
          </button>
        </div>
      )}
    </article>
  );
}
