import { useState } from "react";
import { Card, Badge } from "../components/Card";
import { CaveatList } from "../components/Callout";
import { LoadingBlock } from "../components/States";
import { useApi } from "../lib/useApi";
import { apiPost } from "../lib/api";

const MODE_LABEL = {
  fast_path: "Answered",
  llm: "Answered (AI)",
  llm_not_implemented: "AI not yet implemented",
  unavailable: "AI unavailable",
};

function GenericDataTable({ rows }) {
  if (!rows || rows.length === 0) return null;
  const cols = Object.keys(rows[0]);
  return (
    <div className="table-wrap" style={{ marginTop: 12 }}>
      <table className="dt">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c.replace(/_/g, " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 25).map((row, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} className={typeof row[c] === "number" ? "num" : undefined}>
                  {row[c] === null || row[c] === undefined
                    ? "—"
                    : typeof row[c] === "number"
                      ? Number.isInteger(row[c])
                        ? row[c].toLocaleString("en-IN")
                        : row[c].toLocaleString("en-IN", { maximumFractionDigits: 2 })
                      : String(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 25 && (
        <div className="text-faint" style={{ marginTop: 6 }}>
          Showing 25 of {rows.length} rows.
        </div>
      )}
    </div>
  );
}

function AnswerCard({ item }) {
  return (
    <Card className="answer-card">
      <div className="card-header-row">
        <div className="text-muted" style={{ fontSize: 13 }}>
          {item.question}
        </div>
        <span className={`badge mode-badge-${item.mode}`}>{MODE_LABEL[item.mode] || item.mode}</span>
      </div>
      <p className="answer-text">{item.answer}</p>
      {item.sql && <pre className="sql-block">{item.sql}</pre>}
      <GenericDataTable rows={item.data} />
      <CaveatList caveats={item.caveats} />
      {item.source && (
        <div className="text-faint" style={{ marginTop: 10, fontSize: 11 }}>
          Source: {item.source}
        </div>
      )}
    </Card>
  );
}

export default function AskAnything() {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState([]);
  const [asking, setAsking] = useState(false);
  const suggested = useApi("/ask/supported-questions");

  async function ask(q) {
    const text = (q ?? question).trim();
    if (!text || asking) return;
    setAsking(true);
    setQuestion("");
    try {
      const result = await apiPost("/ask", { question: text });
      setHistory((h) => [result, ...h]);
    } catch (e) {
      setHistory((h) => [
        {
          question: text,
          mode: "unavailable",
          answer: `Something went wrong asking that: ${e.message}`,
          data: null,
          sql: null,
          caveats: [],
        },
        ...h,
      ]);
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="chat-shell">
      <Card>
        <div className="ask-input-row">
          <input
            type="text"
            placeholder='e.g. "Which five outlets had the lowest fill rate last month?"'
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
          />
          <button className="btn btn-primary" onClick={() => ask()} disabled={asking}>
            {asking ? "Asking…" : "Ask"}
          </button>
        </div>
        <div style={{ marginTop: 12 }}>
          <div className="control-label" style={{ marginBottom: 8, display: "block" }}>
            Questions this build answers deterministically
          </div>
          {suggested.loading && <LoadingBlock rows={2} />}
          {suggested.data && (
            <div className="suggested-chips">
              {suggested.data.map((q) => (
                <button key={q.id} className="chip" onClick={() => ask(q.example)}>
                  {q.example}
                </button>
              ))}
            </div>
          )}
        </div>
      </Card>

      {history.length === 0 && !asking && (
        <Card>
          <div className="state-block">
            Ask one of the questions above, or type your own. Anything outside the supported list needs
            an ANTHROPIC_API_KEY on the backend — without one, you'll get a clear "AI unavailable"
            answer, not a failure.
          </div>
        </Card>
      )}

      {asking && (
        <Card>
          <LoadingBlock rows={3} />
        </Card>
      )}

      {history.map((item, i) => (
        <AnswerCard key={i} item={item} />
      ))}
    </div>
  );
}
