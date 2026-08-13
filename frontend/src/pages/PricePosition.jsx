import { useState } from "react";
import { Card, Badge } from "../components/Card";
import { DataState } from "../components/States";
import { Callout, CaveatList } from "../components/Callout";
import { useApi } from "../lib/useApi";
import { buildQuery } from "../lib/api";
import { formatINR, formatPct, formatNumber } from "../lib/format";
import { useRegion } from "../lib/RegionContext";

const CITIES = [
  { value: "mumbai", label: "Mumbai" },
  { value: "delhi", label: "Delhi" },
  { value: "bengaluru", label: "Bengaluru" },
  { value: "chennai", label: "Chennai" },
];

export default function PricePosition() {
  const [city, setCity] = useState("mumbai");
  const { regionCode, regions } = useRegion();
  const activeRegionName = regions.find((r) => r.region_code === regionCode)?.region_name;

  const gap = useApi("/price-position/gap" + buildQuery({ city, top_n_skus_by_value: 20 }));
  const summary = useApi("/price-position/summary" + buildQuery({ city }));

  const rows = gap.data?.rows ?? [];
  const nMatched = rows.filter((r) => r.metrics.lowest_competitor_price_inr !== null).length;

  return (
    <div>
      <Callout variant="neutral" title="Conservative competitor matching">
        Only unambiguous SKU matches are used. Uncertain matches are excluded rather than guessed.
        <div className="callout-stat">614 / 1,137 listings · 54% matched with confidence</div>
        <details className="methodology-details">
          <summary>How matching works</summary>
          <div className="caveat-list-plain">
            Competitor listings are matched to Kestrel SKUs by brand, category and pack size. Only
            listings where exactly one SKU fits are treated as a confident match — the rest are shown as{" "}
            <strong>no confident match</strong> rather than guessed, across all 4 cities.
          </div>
        </details>
      </Callout>

      {activeRegionName && (
        <Callout variant="warn" title="Region selector doesn't apply on this page" style={{ marginTop: 12 }}>
          You have <strong>{activeRegionName}</strong> selected, but competitor listings are scraped by{" "}
          <strong>city</strong> (Mumbai/Delhi/Bengaluru/Chennai), not by Kestrel's own sales regions, and
          the two aren't mapped in this dataset. This page always shows all cities regardless of the
          region selector — use the city picker below instead.
        </Callout>
      )}

      <div className="controls-row" style={{ marginTop: 16 }}>
        <span className="control-label">City</span>
        <select className="select" value={city} onChange={(e) => setCity(e.target.value)}>
          {CITIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      <Card
        title="Top 20 SKUs by dispatch value — MRP vs. lowest observed street price"
        subtitle={`${nMatched} of ${rows.length} have a confidently-matched competitor price in ${
          CITIES.find((c) => c.value === city)?.label
        }.`}
      >
        <DataState loading={gap.loading} error={gap.error} isEmpty={rows.length === 0} skeletonRows={8}>
          <div className="table-wrap">
            <table className="dt">
              <thead>
                <tr>
                  <th>SKU</th>
                  <th className="num">Dispatch value</th>
                  <th className="num">Kestrel MRP</th>
                  <th className="num">Lowest street price</th>
                  <th className="num">Gap</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const m = r.metrics;
                  const matched = m.lowest_competitor_price_inr !== null && m.lowest_competitor_price_inr !== undefined;
                  return (
                    <tr key={r.dimension}>
                      <td>
                        <strong>{r.dimension}</strong>{" "}
                        <span className="text-faint">{r.dimension_label}</span>
                      </td>
                      <td className="num">{formatINR(m.kestrel_dispatch_value_inr, { compact: true })}</td>
                      <td className="num">{formatINR(m.kestrel_mrp_inr)}</td>
                      <td className="num">
                        {matched ? (
                          formatINR(m.lowest_competitor_price_inr)
                        ) : (
                          <Badge tone="neutral">No confident match</Badge>
                        )}
                      </td>
                      <td className="num">
                        {matched ? (
                          <Badge tone={m.gap_pct >= 0 ? "warn" : "good"}>
                            {m.gap_pct >= 0 ? "MRP higher" : "MRP lower"} {formatPct(Math.abs(m.gap_pct))}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </DataState>
        <div style={{ marginTop: 10 }}>
          <CaveatList caveats={gap.data?.caveats} />
        </div>
      </Card>

      <div style={{ height: 16 }} />

      <Card title="Gap summary by category" subtitle="Confidently-matched listings only.">
        <DataState
          loading={summary.loading}
          error={summary.error}
          isEmpty={summary.data && summary.data.rows.length === 0}
          emptyMessage="No confidently-matched listings for this city."
          skeletonRows={5}
        >
          {summary.data && (
            <div className="table-wrap">
              <table className="dt">
                <thead>
                  <tr>
                    <th>Category</th>
                    <th className="num"># matched listings</th>
                    <th className="num">Avg gap</th>
                    <th className="num">Range</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.data.rows.map((r) => (
                    <tr key={r.dimension}>
                      <td>{r.dimension}</td>
                      <td className="num">{formatNumber(r.metrics.n_matched_listings)}</td>
                      <td className="num">{formatPct(r.metrics.avg_gap_pct)}</td>
                      <td className="num text-faint">
                        {formatPct(r.metrics.min_gap_pct)} to {formatPct(r.metrics.max_gap_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DataState>
      </Card>
    </div>
  );
}
