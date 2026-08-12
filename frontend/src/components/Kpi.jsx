export function Kpi({ label, value, sub, small }) {
  return (
    <div>
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value${small ? " small" : ""}`}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export function KpiRow({ children }) {
  return <div className="kpi-row">{children}</div>;
}
