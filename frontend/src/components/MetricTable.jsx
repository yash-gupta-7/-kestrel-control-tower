// Renders MetricResponse-shaped data: { rows: [{ dimension, dimension_label, metrics: {...} }] }
// columns: [{ key, label, format(v, row), numeric, bar: { max, tone(v,row) } }]
export function MetricTable({ data, columns, dimensionLabel = "Name" }) {
  const rows = data?.rows ?? [];
  return (
    <div className="table-wrap">
      <table className="dt">
        <thead>
          <tr>
            <th>{dimensionLabel}</th>
            {columns.map((c) => (
              <th key={c.key} className={c.numeric ? "num" : undefined}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.dimension + i}>
              <td>
                <strong>{row.dimension_label || row.dimension}</strong>
                {row.dimension_label && row.dimension_label !== row.dimension && (
                  <div className="text-faint" style={{ fontSize: 11 }}>
                    {row.dimension}
                  </div>
                )}
              </td>
              {columns.map((c) => {
                const v = row.metrics ? row.metrics[c.key] : row[c.key];
                if (c.bar) {
                  const max = c.bar.max ?? 100;
                  const pct = v === null || v === undefined ? 0 : Math.max(0, Math.min(100, (v / max) * 100));
                  const tone = c.bar.tone ? c.bar.tone(v, row) : "";
                  return (
                    <td key={c.key} className="num">
                      <div className="bar-cell">
                        <span style={{ minWidth: 46, textAlign: "right" }}>
                          {c.format ? c.format(v, row) : v}
                        </span>
                        <div className="bar-track">
                          <div className={`bar-fill ${tone}`} style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    </td>
                  );
                }
                return (
                  <td key={c.key} className={c.numeric ? "num" : undefined}>
                    {c.format ? c.format(v, row) : v ?? "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
