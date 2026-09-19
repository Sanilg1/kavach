import { useCallback, useEffect, useRef, useState } from "react";
import { api, Doc } from "./api";
import Analysis from "./components/Analysis";
import ReelFeed from "./components/ReelFeed";
import TopicMap from "./components/TopicMap";
import Upload from "./components/Upload";

type Screen = "upload" | "analysis" | "topics" | "feed";

function readHash(): { doc?: string; screen?: Screen } {
  const m = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return { doc: m.get("doc") || undefined, screen: (m.get("s") as Screen) || undefined };
}

function writeHash(doc?: string, screen?: Screen) {
  const p = new URLSearchParams();
  if (doc) p.set("doc", doc);
  if (screen) p.set("s", screen);
  const h = p.toString();
  if (window.location.hash.replace(/^#/, "") !== h) window.history.replaceState(null, "", h ? `#${h}` : window.location.pathname);
}

export default function App() {
  const initial = readHash();
  const [screen, setScreen] = useState<Screen>(initial.doc ? initial.screen || "analysis" : "upload");
  const [doc, setDoc] = useState<Doc | null>(null);
  const [docId, setDocId] = useState<string | undefined>(initial.doc);
  const poll = useRef<number | null>(null);

  const refreshDoc = useCallback(async () => {
    if (!docId) return null;
    try {
      const d = await api.doc(docId);
      setDoc(d);
      return d;
    } catch {
      return null;
    }
  }, [docId]);

  // poll the document while it is being analysed
  useEffect(() => {
    if (!docId) return;
    let alive = true;
    const tick = async () => {
      const d = await refreshDoc();
      if (!alive) return;
      const analysing = !d || ["UPLOADED", "PROCESSING", "ANALYZING"].includes(d.status);
      poll.current = window.setTimeout(tick, analysing ? 2000 : 10000);
    };
    tick();
    return () => {
      alive = false;
      if (poll.current) window.clearTimeout(poll.current);
    };
  }, [docId, refreshDoc]);

  useEffect(() => writeHash(docId, docId ? screen : undefined), [docId, screen]);

  // keep state in sync when the hash is edited or the back button is used
  useEffect(() => {
    const onHash = () => {
      const h = readHash();
      if (h.doc !== docId) setDocId(h.doc);
      setScreen(h.doc ? h.screen || "analysis" : "upload");
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [docId]);

  function restart() {
    setDoc(null);
    setDocId(undefined);
    setScreen("upload");
  }

  const order: Screen[] = ["upload", "analysis", "topics", "feed"];
  const labels: Record<Screen, string> = { upload: "Upload", analysis: "Analyze", topics: "Topics", feed: "Shorts" };

  // a stage is reachable once the current document has got that far
  function reachable(s: Screen): boolean {
    if (s === screen || s === "upload") return true;
    if (!docId || !doc) return s === "analysis" && !!docId;
    const analysed = (doc.concept_count ?? 0) > 0;
    if (s === "analysis") return true;
    if (s === "topics") return analysed;
    return analysed && (["GENERATING", "COMPLETED", "FAILED"].includes(doc.status) || (doc.reels_completed ?? 0) > 0);
  }
  const furthest = [...order].reverse().find((s) => s !== "upload" && reachable(s));

  return (
    <div className={`app${screen === "feed" && docId ? " app-shorts" : ""}`}>
      <div className="topbar">
        <a className="brand" href="#" onClick={(e) => { e.preventDefault(); restart(); }}>
          Kavach
          <span>compress the delivery, not the knowledge</span>
        </a>
        <div className="steps">
          {order.map((s) => {
            const ok = reachable(s);
            const done = s !== screen && ok && (s === "upload" ? !!docId : order.indexOf(s) <= order.indexOf(furthest ?? "upload"));
            return (
              <button
                key={s}
                type="button"
                className={`step ${s === screen ? "active" : done ? "done" : ""}`}
                disabled={!ok}
                aria-current={s === screen ? "step" : undefined}
                title={ok ? `Go to ${labels[s]}` : "Not available yet"}
                onClick={() => ok && setScreen(s)}
              >
                {labels[s]}
              </button>
            );
          })}
        </div>
        {docId && (
          <button type="button" className="new-project" title="Start over with a new PDF" onClick={restart}>
            + New notes
          </button>
        )}
      </div>

      {screen === "upload" && docId && doc && (
        <div className="card current-doc">
          <div>
            <div className="muted small">Current PDF</div>
            <strong>{doc.title || doc.filename}</strong>
          </div>
          <button type="button" className="btn ghost sm" onClick={() => setScreen(furthest ?? "analysis")}>
            Back to {labels[furthest ?? "analysis"]} →
          </button>
        </div>
      )}

      {screen === "upload" && (
        <Upload
          onUploaded={(d) => {
            setDoc(d);
            setDocId(d.document_id);
            setScreen("analysis");
          }}
        />
      )}

      {screen === "analysis" && docId && (
        doc ? <Analysis doc={doc} onContinue={() => setScreen("topics")} onRestart={restart} /> : <div className="card">Loading…</div>
      )}

      {screen === "topics" && docId && <TopicMap docId={docId} onGenerate={() => setScreen("feed")} />}

      {screen === "feed" && docId && (
        doc ? <ReelFeed doc={doc} onBack={() => setScreen("topics")} /> : <div className="card">Loading…</div>
      )}
    </div>
  );
}
