import { useState } from "react";
import { Card } from "../components/Card";
import { DataState } from "../components/States";
import { MetricTable } from "../components/MetricTable";
import { CaveatList } from "../components/Callout";
import { useApi } from "../lib/useApi";
import { buildQuery } from "../lib/api";
import { formatPct, formatNumber, formatINR } from "../lib/format";
import { useRegion } from "../lib/RegionContext";

export default function ColdChain() {
  const [nearExpiryGroupBy, setNearExpiryGroupBy] = useState("category");
  const { regionCode } = useRegion();

  const excursions = useApi("/cold-chain/excursions" + buildQuery({ region_code: regionCode }));
  const nearExpiry = useApi("/cold-chain/near-expiry" + buildQuery({ group_by: nearExpiryGroupBy, region_code: regionCode }));
  const returns = useApi("/cold-chain/returns" + buildQuery({ region_code: regionCode }));

  const maxExcursion = excursions.data
    ? Math.max(...excursions.data.rows.map((r) => r.metrics.excursions_per_hundred), 1)
    : 1;

  return (
    <div className="grid grid-2">
      <Card
        title="Temperature excursions"
        subtitle="Per 100 chilled deliveries, by month."
      >
        <DataState loading={excursions.loading} error={excursions.error} skeletonRows={6}>
          {excursions.data && (
            <div>
              {excursions.data.rows.map((r) => (
                <div key={r.dimension} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <div style={{ width: 62, fontSize: 12, color: "var(--color-text-muted)" }}>{r.dimension}</div>
                  <div className="bar-track" style={{ flex: 1 }}>
                    <div
                      className="bar-fill warn"
                      style={{ width: `${(r.metrics.excursions_per_hundred / maxExcursion) * 100}%` }}
                    />
                  </div>
                  <div style={{ width: 42, textAlign: "right", fontSize: 12 }}>
                    {r.metrics.excursions_per_hundred}
                  </div>
                </div>
              ))}
            </div>
          )}
        </DataState>
        <CaveatList caveats={excursions.data?.caveats} />
      </Card>

      <Card
        title="Near-expiry stock"
        subtitle="30-day window, as of the latest weekly inventory snapshot."
        right={
          <select className="select" value={nearExpiryGroupBy} onChange={(e) => setNearExpiryGroupBy(e.target.value)}>
            <option value="category">By category</option>
            <option value="warehouse">By warehouse</option>
          </select>
        }
      >
        <DataState
          loading={nearExpiry.loading}
          error={nearExpiry.error}
          isEmpty={nearExpiry.data && nearExpiry.data.rows.length === 0}
          skeletonRows={6}
        >
          {nearExpiry.data && (
            <MetricTable
              data={nearExpiry.data}
              dimensionLabel={nearExpiryGroupBy === "category" ? "Category" : "Warehouse"}
              columns={[
                { key: "on_hand_cases", label: "On hand (cases)", numeric: true, format: (v) => formatNumber(v) },
                { key: "near_expiry_cases", label: "Near-expiry (cases)", numeric: true, format: (v) => formatNumber(v) },
                {
                  key: "near_expiry_pct",
                  label: "% near-expiry",
                  numeric: true,
                  bar: { max: 30, tone: () => "warn" },
                  format: (v) => formatPct(v),
                },
              ]}
            />
          )}
        </DataState>
        <div style={{ marginTop: 10 }}>
          <CaveatList caveats={nearExpiry.data?.caveats} />
        </div>
      </Card>

      <Card
        title="Cold-chain-linked returns"
        subtitle="Near-expiry and cold-chain-breach reason codes only."
        className="grid-span-2"
      >
        <DataState
          loading={returns.loading}
          error={returns.error}
          isEmpty={returns.data && returns.data.rows.length === 0}
          skeletonRows={6}
        >
          {returns.data && (
            <MetricTable
              data={returns.data}
              dimensionLabel="Category"
              columns={[
                { key: "return_reason_code", label: "Reason" },
                { key: "return_value_inr", label: "Value", numeric: true, format: (v) => formatINR(v, { compact: true }) },
                { key: "n_returns", label: "# returns", numeric: true, format: (v) => formatNumber(v) },
              ]}
            />
          )}
        </DataState>
        <div style={{ marginTop: 10 }}>
          <CaveatList caveats={returns.data?.caveats} />
        </div>
      </Card>
    </div>
  );
}
