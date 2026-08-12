export function Card({ title, subtitle, right, children, className = "", style }) {
  return (
    <div className={`card ${className}`} style={style}>
      {(title || right) && (
        <div className="card-header-row">
          <div>
            {title && <div className="card-title">{title}</div>}
            {subtitle && <div className="card-subtitle">{subtitle}</div>}
          </div>
          {right}
        </div>
      )}
      {!title && subtitle && <div className="card-subtitle">{subtitle}</div>}
      {children}
    </div>
  );
}

export function Badge({ tone = "neutral", children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
