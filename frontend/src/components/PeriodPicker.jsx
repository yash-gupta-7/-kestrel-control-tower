import { listFiscalQuarters, fiscalLabel } from "../lib/fiscal";

const quarters = listFiscalQuarters();

// Emits { fiscal_year, fiscal_quarter } or {} for "all available data".
export function PeriodPicker({ value, onChange }) {
  const current = value?.fiscal_year ? `${value.fiscal_year}-${value.fiscal_quarter}` : "all";
  return (
    <div>
      <span className="control-label">Period</span>
      <select
        className="select"
        value={current}
        onChange={(e) => {
          if (e.target.value === "all") return onChange({});
          const [fy, fq] = e.target.value.split("-").map(Number);
          onChange({ fiscal_year: fy, fiscal_quarter: fq });
        }}
      >
        <option value="all">All available data</option>
        {quarters.map((q) => (
          <option key={q.label} value={`${q.fy}-${q.fq}`}>
            {fiscalLabel(q.fy, q.fq)} ({q.start} to {q.end})
          </option>
        ))}
      </select>
    </div>
  );
}
