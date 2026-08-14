import { useState } from "react";
import { Card, Badge } from "../components/Card";
import { CaveatList } from "../components/Callout";
import { LoadingBlock } from "../components/States";
import { useApi } from "../lib/useApi";
import { apiPost } from "../lib/api";
import { useRegion } from "../lib/RegionContext";

const MODE_LABEL = {
  fast_path: "Answered",
  llm: "Answered (AI)",
  llm_error: "AI answer unavailable",
  unavailable: "AI unavailable",
  blocked: "Not available",
};

let historyIdCounter = 0;
function makeHistoryId() {
  historyIdCounter += 1;
  return `ask-${Date.now()}-${historyIdCounter}`;
}

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
  const [sqlVisible, setSqlVisible] = useState(false);
  // SQL stays available for auditability (fast-path and Groq-generated
  // answers both carry it, unchanged backend contract) but is secondary to
  // the business answer, so it's collapsed by default behind a toggle.
  // Never shown at all for mode="blocked" -- a blocked/privacy response
  // has nothing worth auditing here, and some blocked responses do carry
  // the SQL that got blocked (see ask.py's privacy layer) purely for
  // server-side/API-level audit, not for display.
  const canShowSql = Boolean(item.sql) && item.mode !== "blocked";

  return (
    <Card className="answer-card">
      <div className="card-header-row">
        <div className="text-muted" style={{ fontSize: 13 }}>
          {item.question}
        </div>
        <span className={`badge mode-badge-${item.mode}`}>{MODE_LABEL[item.mode] || item.mode}</span>
      </div>
      <p className="answer-text">{item.answer}</p>
      <GenericDataTable rows={item.data} />
      <CaveatList caveats={item.caveats} />
      {canShowSql && (
        <>
          <button
            type="button"
            className="btn-sql-toggle"
            onClick={() => setSqlVisible((v) => !v)}
            aria-expanded={sqlVisible}
          >
            {sqlVisible ? "Hide SQL" : "Show SQL"}
          </button>
          {sqlVisible && <pre className="sql-block">{item.sql}</pre>}
        </>
      )}
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
  const { regionCode, regions } = useRegion();
  const activeRegionName = regions.find((r) => r.region_code === regionCode)?.region_name;

  async function ask(q) {
    const text = (q ?? question).trim();
    if (!text || asking) return;
    setAsking(true);
    setQuestion("");
    try {
      const result = await apiPost("/ask", { question: text, region_code: regionCode || undefined });
      setHistory((h) => [{ ...result, _id: makeHistoryId() }, ...h]);
    } catch (e) {
      setHistory((h) => [
        {
          _id: makeHistoryId(),
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
        {activeRegionName && (
          <div className="text-faint" style={{ marginTop: 8, fontSize: 12 }}>
            Scoped to <strong>{activeRegionName}</strong> region where the question supports it (see the
            region selector, top right).
          </div>
        )}
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
            Ask a question about your supply chain. Choose a suggested question or ask your own in plain
            English.
          </div>
        </Card>
      )}

      {asking && (
        <Card>
          <LoadingBlock rows={3} />
        </Card>
      )}

      {history.map((item) => (
        <AnswerCard key={item._id} item={item} />
      ))}
    </div>
  );
}
