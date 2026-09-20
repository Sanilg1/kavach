import { useRef, useState } from "react";
import { api, Doc } from "../api";
import { forgetDoc, loadLibrary, LibraryEntry, rememberDoc } from "../library";

export default function Upload({ onUploaded }: { onUploaded: (d: Doc) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [library, setLibrary] = useState<LibraryEntry[]>(() => loadLibrary());

  async function handle(file: File | undefined) {
    if (!file) return;
    setError(null);
    if (!/\.(pdf|docx|pptx|txt|md|markdown)$/i.test(file.name)) {
      setError("Please choose a PDF, DOCX, PPTX, TXT or Markdown file.");
      return;
    }
    setBusy(true);
    try {
      const doc = await api.upload(file);
      rememberDoc(doc);
      onUploaded(doc);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="hero">
      <h1>Turn your notes into revision shorts</h1>
      <p className="tag">Compress the delivery, not the knowledge.</p>
      <div
        className={`dropzone ${over ? "over" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          handle(e.dataTransfer.files?.[0]);
        }}
      >
        <input ref={inputRef} type="file" accept=".pdf,.docx,.pptx,.txt,.md,.markdown,application/pdf" onChange={(e) => handle(e.target.files?.[0])} />
        {busy ? (
          <div className="row" style={{ justifyContent: "center" }}>
            <div className="spinner" /> Uploading…
          </div>
        ) : (
          <>
            <div className="btn" style={{ pointerEvents: "none" }}>Upload notes</div>
            <p className="muted small" style={{ marginBottom: 0 }}><span className="hover-only">Drag & drop or click · </span>
              <span className="touch-only">Tap to choose a file · </span>
              PDF, DOCX, PPTX, TXT or Markdown · up to 60 pages</p>
          </>
        )}
      </div>
      {error && <div className="error">{error}</div>}
      {library.length > 0 && (
        <div className="library">
          <div className="library-head">
            <h3>Your recent notes</h3>
            <span className="muted small">saved on this device · no account needed</span>
          </div>
          {library.map((e) => (
            <div key={e.id} className="library-item">
              <a href={`#doc=${e.id}&s=${e.status === "COMPLETED" ? "feed" : e.status === "READY" ? "topics" : "analysis"}`}>
                <strong>{e.title}</strong>
                <span className="muted small">
                  {e.pages} pages{e.shorts ? ` · ${e.shorts} shorts` : ""} · {new Date(e.updated).toLocaleDateString()}
                </span>
              </a>
              <button
                type="button"
                className="library-forget"
                aria-label="Remove from this device"
                title="Remove from this device"
                onClick={() => {
                  forgetDoc(e.id);
                  setLibrary(loadLibrary());
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      <p className="muted small" style={{ marginTop: 28 }}>
        Kavach reads your PDF, Word, PowerPoint or text notes, builds a learning path, and turns each concept into a 30–60 second whiteboard lesson with narration, source pages and a quick check.
      </p>
    </div>
  );
}
