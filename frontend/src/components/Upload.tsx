import { useRef, useState } from "react";
import { api, Doc } from "../api";

export default function Upload({ onUploaded }: { onUploaded: (d: Doc) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handle(file: File | undefined) {
    if (!file) return;
    setError(null);
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Please choose a PDF file.");
      return;
    }
    setBusy(true);
    try {
      const doc = await api.upload(file);
      onUploaded(doc);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="hero">
      <h1>Turn your PDF into revision shorts</h1>
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
        <input ref={inputRef} type="file" accept="application/pdf,.pdf" onChange={(e) => handle(e.target.files?.[0])} />
        {busy ? (
          <div className="row" style={{ justifyContent: "center" }}>
            <div className="spinner" /> Uploading…
          </div>
        ) : (
          <>
            <div className="btn" style={{ pointerEvents: "none" }}>Upload PDF</div>
            <p className="muted small" style={{ marginBottom: 0 }}>Drag & drop or click · Max 60 pages · Text-based PDFs work best</p>
          </>
        )}
      </div>
      {error && <div className="error">{error}</div>}
      <p className="muted small" style={{ marginTop: 28 }}>
        Kavach reads your PDF, builds a learning path, and turns each concept into a 30–60 second whiteboard lesson with narration, source pages and a quick check.
      </p>
    </div>
  );
}
