export function LoadingBlock({ rows = 4 }) {
  return (
    <div>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="skeleton"
          style={{ height: 16, marginBottom: 10, width: `${85 - i * 8}%` }}
        />
      ))}
    </div>
  );
}

export function ErrorBlock({ error, onRetry }) {
  return (
    <div className="state-block error">
      <div className="icon">⚠</div>
      <div>
        <strong>Couldn't load this data.</strong>
        <div style={{ marginTop: 4 }}>{error?.message || "Unknown error."}</div>
      </div>
      {onRetry && (
        <div style={{ marginTop: 12 }}>
          <button className="btn" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
    </div>
  );
}

export function EmptyBlock({ message = "No data for this selection." }) {
  return (
    <div className="state-block">
      <div className="icon">–</div>
      <div>{message}</div>
    </div>
  );
}

// Wraps the loading/error/empty/data branching so pages don't repeat it.
export function DataState({ loading, error, isEmpty, onRetry, emptyMessage, skeletonRows, children }) {
  if (loading) return <LoadingBlock rows={skeletonRows} />;
  if (error) return <ErrorBlock error={error} onRetry={onRetry} />;
  if (isEmpty) return <EmptyBlock message={emptyMessage} />;
  return children;
}
