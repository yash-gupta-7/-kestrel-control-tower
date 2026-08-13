const ICONS = {
  info: "ℹ",
  warn: "▲",
  systemic: "◆",
  neutral: "·",
};

// variant: "info" | "warn" | "systemic" | "neutral"
// "systemic" is for the specific class of finding where a metric applies to
// (almost) every row in the population -- e.g. all 140 routes, all 724
// outlets -- so the UI can say "this is a network-wide pattern" instead of
// letting the number read as a broken query.
export function Callout({ variant = "info", title, children, style }) {
  return (
    <div className={`callout callout-${variant}`} style={style}>
      <div className="callout-icon">{ICONS[variant] || ICONS.info}</div>
      <div>
        {title && <div className="callout-title">{title}</div>}
        <div>{children}</div>
      </div>
    </div>
  );
}

export function CaveatList({ caveats }) {
  if (!caveats || caveats.length === 0) return null;
  return (
    <ul className="caveat-list">
      {caveats.map((c, i) => (
        <li key={i}>{c}</li>
      ))}
    </ul>
  );
}
