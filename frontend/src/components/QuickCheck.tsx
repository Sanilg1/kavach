import { useState } from "react";
import { QuickCheck as QC } from "../api";

export default function QuickCheck({ qc, onDone }: { qc: QC; onDone?: (correct: boolean) => void }) {
  const [picked, setPicked] = useState<number | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const letters = ["A", "B", "C", "D", "E"];

  return (
    <div className="quiz">
      <div className="kicker">Quick check</div>
      <div className="q">{qc.question}</div>
      {qc.options.map((o, i) => {
        const cls = submitted ? (i === qc.answer ? "correct" : i === picked ? "wrong" : "") : "";
        return (
          <label key={i} className={cls}>
            <input type="radio" name={`qc-${qc.question.slice(0, 12)}`} disabled={submitted} checked={picked === i} onChange={() => setPicked(i)} />
            <span>
              <strong>{letters[i]}.</strong> {o}
            </span>
          </label>
        );
      })}
      {!submitted ? (
        <button
          className="btn sm"
          disabled={picked === null}
          onClick={() => {
            setSubmitted(true);
            onDone?.(picked === qc.answer);
          }}
        >
          Submit
        </button>
      ) : (
        <div className="result" style={{ color: picked === qc.answer ? "var(--green)" : "var(--red)" }}>
          {picked === qc.answer ? "Correct!" : `Not quite — the answer is ${letters[qc.answer]}.`}
          {qc.explanation && <div className="muted small" style={{ fontWeight: 400, marginTop: 4 }}>{qc.explanation}</div>}
        </div>
      )}
    </div>
  );
}
