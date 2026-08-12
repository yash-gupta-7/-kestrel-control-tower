import { useEffect, useState } from "react";
import { Card } from "../components/Card";
import { DataState } from "../components/States";
import { MetricTable } from "../components/MetricTable";
import { Callout, CaveatList } from "../components/Callout";
import { PeriodPicker } from "../components/PeriodPicker";
import { useApi } from "../lib/useApi";
import { apiPost, buildQuery } from "../lib/api";
import { lastCompleteFiscalQuarter, fiscalLabel } from "../lib/fiscal";
import { formatPct, formatNumber } from "../lib/format";

const lcq = lastCompleteFiscalQuarter();

function fillRateTone(pct) {
  if (pct === null || pct === undefined) return "";
  if (pct >= 90) return "good";
  if (pct >= 75) return "warn";
  return "bad";
}

export default function Service() {
  const [period, setPeriod] = useState({ fiscal_year: lcq.fy, fiscal_quarter: lcq.fq });
  const [fillGroupBy, setFillGroupBy] = useState("outlet");
  const [otifGroupBy, setOtifGroupBy] = useState("region");

  const fillPath = "/service/fill-rate" + buildQuery({ group_by: fillGroupBy, limit: 15, ...period });
  const otifPath = "/service/otif" + buildQuery({ group_by: otifGroupBy, limit: 15, ...period });

  const fillRate = useApi(fillPath);
  const otif = useApi(otifPath);
  const lateRoutes = useLateRoutesFinding();

  return (
    <div>
      <div className="controls-row">
        <PeriodPicker value={period} onChange={setPeriod} />
      </div>

      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <Card
          title="Fill rate (eaches)"
          subtitle="Worst performers first. Reported in eaches only — see note below."
          right={
            <select className="select" value={fillGroupBy} onChange={(e) => setFillGroupBy(e.target.value)}>
              <option value="outlet">By outlet</option>
              <option value="region">By region</option>
              <option value="warehouse">By warehouse</option>
              <option value="route">By route</option>
            </select>
          }
        >
          <DataState
            loading={fillRate.loading}
            error={fillRate.error}
            isEmpty={fillRate.data && fillRate.data.rows.length === 0}
            emptyMessage="No delivered/partial orders for this selection."
            skeletonRows={6}
          >
            {fillRate.data && (
              <MetricTable
                data={fillRate.data}
                dimensionLabel={fillGroupBy === "outlet" ? "Outlet" : fillGroupBy[0].toUpperCase() + fillGroupBy.slice(1)}
                columns={[
                  { key: "ordered_qty", label: "Ordered (ea)", numeric: true, format: (v) => formatNumber(v) },
                  { key: "delivered_qty", label: "Delivered (ea)", numeric: true, format: (v) => formatNumber(v) },
                  {
                    key: "fill_rate_pct",
                    label: "Fill rate",
                    numeric: true,
                    bar: { max: 100, tone: (v) => fillRateTone(v) },
                    format: (v) => formatPct(v),
                  },
                ]}
              />
            )}
          </DataState>
          <div style={{ marginTop: 10 }}>
            <CaveatList caveats={fillRate.data?.caveats} />
          </div>
        </Card>

        <Card
          title="OTIF"
          subtitle={`On-time in full, strict definition. ${fiscalLabel(period.fiscal_year ?? lcq.fy, period.fiscal_quarter ?? lcq.fq)}`}
          right={
            <select className="select" value={otifGroupBy} onChange={(e) => setOtifGroupBy(e.target.value)}>
              <option value="region">By region</option>
              <option value="warehouse">By warehouse</option>
              <option value="route">By route</option>
            </select>
          }
        >
          <Callout variant="warn" title="Why OTIF reads near-zero here">
            Strict OTIF requires every case ordered to arrive on time <em>and</em> in full, with no
            tolerance. In this dataset, essentially no order is ever delivered at 100% of what was
            ordered — every order line carries some shortfall. That makes strict OTIF read as ~0%
            everywhere. This is a genuine finding about the data, not a broken dashboard — use{" "}
            <strong>Avg. fulfilment %</strong> below as the working signal until Kestrel defines a real
            shrinkage-tolerance policy.
          </Callout>
          <div style={{ height: 12 }} />
          <DataState
            loading={otif.loading}
            error={otif.error}
            isEmpty={otif.data && otif.data.rows.length === 0}
            emptyMessage="No deliveries for this selection."
            skeletonRows={5}
          >
            {otif.data && (
              <MetricTable
                data={otif.data}
                dimensionLabel={otifGroupBy[0].toUpperCase() + otifGroupBy.slice(1)}
                columns={[
                  { key: "on_time_pct", label: "On-time %", numeric: true, format: (v) => formatPct(v) },
                  {
                    key: "avg_fulfilment_pct",
                    label: "Avg. fulfilment %",
                    numeric: true,
                    bar: { max: 100, tone: (v) => fillRateTone(v) },
                    format: (v) => formatPct(v),
                  },
                  { key: "otif_pct_strict", label: "Strict OTIF", numeric: true, format: (v) => formatPct(v, 2) },
                ]}
              />
            )}
          </DataState>
          <div style={{ marginTop: 10 }}>
            <CaveatList caveats={otif.data?.caveats} />
          </div>
        </Card>
      </div>

      <Card title="Delivery lateness — network-wide finding">
        <DataState loading={lateRoutes.loading} error={lateRoutes.error} skeletonRows={2}>
          {lateRoutes.data && (
            <>
              <Callout variant="systemic" title="Systemic, not route-specific">
                {lateRoutes.data.answer}
              </Callout>
              {lateRoutes.data.data && lateRoutes.data.data.length > 0 && (
                <div style={{ marginTop: 14 }} className="table-wrap">
                  <table className="dt">
                    <thead>
                      <tr>
                        <th>Route</th>
                        <th className="num">Deliveries</th>
                        <th className="num">&gt;2hr late</th>
                        <th className="num">% very late</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lateRoutes.data.data.slice(0, 10).map((r) => (
                        <tr key={r.route_code}>
                          <td>
                            <strong>{r.route_code}</strong>{" "}
                            <span className="text-faint">{r.route_name}</span>
                          </td>
                          <td className="num">{formatNumber(r.n_deliveries)}</td>
                          <td className="num">{formatNumber(r.n_very_late)}</td>
                          <td className="num">{formatPct(r.pct_very_late)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </DataState>
      </Card>
    </div>
  );
}

function useLateRoutesFinding() {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    let cancelled = false;
    apiPost("/ask", { question: "Which routes are more than two hours late on more than one delivery in ten?" })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}
