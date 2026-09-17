import { useEffect, useMemo, useState } from "react";
import { api, fmtDuration, fmtPages, Topic, TopicsResponse } from "../api";

interface Props {
  docId: string;
  onGenerate: () => void;
}

export default function TopicMap({ docId, onGenerate }: Props) {
  const [data, setData] = useState<TopicsResponse | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [newName, setNewName] = useState("");
  const [newPages, setNewPages] = useState("");
  const [aiEnhanced, setAiEnhanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .topics(docId)
      .then((d) => {
        setData(d);
        setTopics(d.topics);
      })
      .catch((e) => setError((e as Error).message));
  }, [docId]);

  const byId = useMemo(() => Object.fromEntries(topics.map((t) => [t.topic_id, t])), [topics]);
  const selected = topics.filter((t) => t.selected);
  const estShorts = selected.reduce((a, t) => a + t.estimated_shorts, 0);
  const estDur = selected.reduce((a, t) => a + t.estimated_duration, 0);

  function toggle(id: string) {
    setTopics((ts) => ts.map((t) => (t.topic_id === id ? { ...t, selected: !t.selected } : t)));
  }

  function move(idx: number, dir: -1 | 1) {
    setTopics((ts) => {
      const j = idx + dir;
      if (j < 0 || j >= ts.length) return ts;
      const copy = [...ts];
      [copy[idx], copy[j]] = [copy[j], copy[idx]];
      return copy.map((t, i) => ({ ...t, learning_order: i + 1 }));
    });
  }

  function addTopic() {
    const name = newName.trim();
    if (!name) return;
    const pages = newPages
      .split(/[,\s]+/)
      .map((x) => parseInt(x, 10))
      .filter((n) => !Number.isNaN(n));
    setTopics((ts) => [
      ...ts,
      {
        topic_id: `new-${Date.now()}`,
        name,
        description: "",
        source_pages: pages,
        prerequisites: [],
        difficulty: "medium",
        estimated_shorts: 1,
        estimated_duration: 45,
        learning_order: ts.length + 1,
        recommended: false,
        selected: true,
        user_added: true,
      },
    ]);
    setNewName("");
    setNewPages("");
  }

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const added = topics.filter((t) => t.topic_id.startsWith("new-")).map((t) => ({ name: t.name, source_pages: t.source_pages }));
      const kept = topics.filter((t) => !t.topic_id.startsWith("new-"));
      await api.updateTopics(docId, {
        selected_topic_ids: kept.filter((t) => t.selected).map((t) => t.topic_id),
        order: kept.map((t) => t.topic_id),
        added_topics: added,
      });
      await api.generate(docId, aiEnhanced);
      onGenerate();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <div className="card">{error ? <div className="error">{error}</div> : "Loading topic map…"}</div>;

  return (
    <>
      <div className="card">
        <div className="row spread">
          <div>
            <h2>Your revision path</h2>
            <p className="muted" style={{ margin: 0 }}>
              {data.title}
            </p>
          </div>
          <span className="chip">{topics.length} concepts</span>
        </div>
        {data.summary && (
          <p className="muted small" style={{ marginTop: 10 }}>
            {data.summary}
          </p>
        )}
        {data.uncertainties?.length > 0 && (
          <div className="notice">
            <strong>Source quality warnings</strong>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {data.uncertainties.map((u, i) => (
                <li key={i}>
                  {u.reason} {u.source_pages?.length ? `(${fmtPages(u.source_pages)})` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="muted small">The AI recommended this order from prerequisites and difficulty. Untick topics to skip them, reorder with the arrows, or add your own.</p>

        <div>
          {topics.map((t, i) => (
            <div key={t.topic_id} className={`topic ${t.selected ? "" : "off"}`}>
              <input type="checkbox" checked={t.selected} onChange={() => toggle(t.topic_id)} />
              <div>
                <div className="row" style={{ gap: 8 }}>
                  <span className="order">{String(i + 1).padStart(2, "0")}.</span>
                  <span className="name">{t.name}</span>
                  {t.user_added && <span className="chip purple">added by you</span>}
                  {!t.recommended && !t.user_added && <span className="chip">optional</span>}
                  <span className={`chip ${t.difficulty === "easy" ? "green" : t.difficulty === "hard" ? "red" : "orange"}`}>{t.difficulty}</span>
                </div>
                {t.description && <div className="small muted">{t.description}</div>}
                <div className="meta">
                  <span>
                    {t.estimated_shorts} short{t.estimated_shorts > 1 ? "s" : ""}
                  </span>
                  <span>{fmtDuration(t.estimated_duration)}</span>
                  {t.source_pages.length > 0 && <span>{fmtPages(t.source_pages)}</span>}
                  {t.prerequisites.length > 0 && <span>after: {t.prerequisites.map((p) => byId[p]?.name || p).join(", ")}</span>}
                </div>
              </div>
              <div className="ctrl">
                <button title="Move up" onClick={() => move(i, -1)} disabled={i === 0}>
                  ↑
                </button>
                <button title="Move down" onClick={() => move(i, 1)} disabled={i === topics.length - 1}>
                  ↓
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="addrow">
          <input placeholder="+ Add a topic (e.g. TCP vs UDP)" value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTopic()} />
          <input placeholder="pages, e.g. 24, 25" value={newPages} onChange={(e) => setNewPages(e.target.value)} style={{ maxWidth: 150 }} />
          <button className="btn secondary sm" onClick={addTopic}>
            Add topic
          </button>
        </div>
      </div>

      <div className="sticky-footer">
        <div>
          <div className="bigstat">
            {estShorts} shorts · {fmtDuration(estDur)}
          </div>
          <label className="toggle">
            <input type="checkbox" checked={aiEnhanced} onChange={(e) => setAiEnhanced(e.target.checked)} />
            AI-enhanced mode <span className="muted small">(adds general knowledge, clearly marked)</span>
          </label>
        </div>
        <button className="btn" onClick={generate} disabled={busy || selected.length === 0}>
          {busy ? "Starting…" : "Generate shorts →"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
    </>
  );
}
