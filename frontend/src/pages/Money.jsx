import { useEffect, useState } from "react";
import { Card } from "../components/Card";
import { DataState } from "../components/States";
import { MetricTable } from "../components/MetricTable";
import { Callout, CaveatList } from "../components/Callout";
import { PeriodPicker } from "../components/PeriodPicker";
import { useApi } from "../lib/useApi";
import { apiPost, buildQuery } from "../lib/api";
import { lastCompleteFiscalQuarter } from "../lib/fiscal";
import { formatINR, formatPct, formatNumber } from "../lib/format";
import { useRegion } from "../lib/RegionContext";

const lcq = lastCompleteFiscalQuarter();

export default function Money() {
  const [period, setPeriod] = useState({ fiscal_year: lcq.fy, fiscal_quarter: lcq.fq });
  const [freightGroupBy, setFreightGroupBy] = useState("warehouse");
  const { regionCode } = useRegion();

  const freightPath = "/money/freight-cost-per-case" + buildQuery({ group_by: freightGroupBy, limit: 15, region_code: regionCode, ...period });
  const returnsPath = "/money/returns-leakage" + buildQuery({ group_by: "category", limit: 15, region_code: regionCode, ...period });

  const freight = useApi(freightPath);
  const returns = useApi(returnsPath);
  const discontinued = useDiscontinuedFinding(regionCode);

  return (
    <div>
      <div className="controls-row">
        <PeriodPicker value={period} onChange={setPeriod} />
      </div>

      <div className="grid grid-2" style={{ marginBottom: 16 }}>
        <Card
          title="Freight cost per delivered case"
          subtitle="The only source of actual billed freight cost (not driver-entered fuel_cost_inr)."
          right={
            <select className="select" value={freightGroupBy} onChange={(e) => setFreightGroupBy(e.target.value)}>
              <option value="warehouse">By warehouse</option>
              <option value="carrier">By carrier</option>
            </select>
          }
        >
          <DataState
            loading={freight.loading}
            error={freight.error}
            isEmpty={freight.data && freight.data.rows.length === 0}
            emptyMessage="No freight data for this selection."
            skeletonRows={6}
          >
            {freight.data &&
              (freightGroupBy === "warehouse" ? (
                <MetricTable
                  data={freight.data}
                  dimensionLabel="Warehouse"
                  columns={[
                    { key: "freight_inr", label: "Freight billed", numeric: true, format: (v) => formatINR(v, { compact: true }) },
                    { key: "delivered_cases", label: "Cases delivered", numeric: true, format: (v) => formatNumber(v) },
                    { key: "freight_inr_per_case", label: "₹ / case", numeric: true, format: (v) => formatINR(v) },
                  ]}
                />
              ) : (
                <MetricTable
                  data={freight.data}
                  dimensionLabel="Carrier"
                  columns={[
                    { key: "freight_inr", label: "Freight billed", numeric: true, format: (v) => formatINR(v, { compact: true }) },
                    { key: "n_invoices", label: "Invoices", numeric: true, format: (v) => formatNumber(v) },
                    { key: "avg_invoice_inr", label: "Avg invoice", numeric: true, format: (v) => formatINR(v) },
                  ]}
                />
              ))}
          </DataState>
          <div style={{ marginTop: 10 }}>
            <CaveatList caveats={freight.data?.caveats} />
          </div>
        </Card>

        <Card title="Returns as leakage, by category" subtitle="Value and leading reason code.">
          <DataState
            loading={returns.loading}
            error={returns.error}
            isEmpty={returns.data && returns.data.rows.length === 0}
            emptyMessage="No returns for this selection."
            skeletonRows={6}
          >
            {returns.data && (
              <MetricTable
                data={returns.data}
                dimensionLabel="Category"
                columns={[
                  { key: "return_value_inr", label: "Return value", numeric: true, format: (v) => formatINR(v, { compact: true }) },
                  {
                    key: "returns_pct_of_dispatch",
                    label: "% of dispatch value",
                    numeric: true,
                    bar: { max: 5, tone: () => "warn" },
                    format: (v) => formatPct(v, 2),
                  },
                  { key: "leading_reason_code", label: "Leading reason" },
                ]}
              />
            )}
          </DataState>
          <div style={{ marginTop: 10 }}>
            <CaveatList caveats={returns.data?.caveats} />
          </div>
        </Card>
      </div>

      <Card title="Discontinued-SKU ordering — catalog/process finding">
        <DataState loading={discontinued.loading} error={discontinued.error} skeletonRows={2}>
          {discontinued.data && (
            <>
              <Callout variant="systemic" title="Systemic, not an individual-outlet problem">
                {discontinued.data.answer}
              </Callout>
              {discontinued.data.data && discontinued.data.data.length > 0 && (
                <div style={{ marginTop: 14 }} className="table-wrap">
                  <table className="dt">
                    <thead>
                      <tr>
                        <th>Outlet</th>
                        <th>SKU</th>
                        <th className="num">Order lines</th>
                        <th>First / last order</th>
                      </tr>
                    </thead>
                    <tbody>
                      {discontinued.data.data.slice(0, 10).map((r, i) => (
                        <tr key={i}>
                          <td>
                            <strong>{r.outlet_name}</strong>{" "}
                            <span className="text-faint">{r.outlet_code}</span>
                          </td>
                          <td>
                            {r.sku_code} <span className="text-faint">{r.product_name}</span>
                          </td>
                          <td className="num">{formatNumber(r.n_order_lines)}</td>
                          <td className="text-faint">
                            {r.first_order_after_discontinuation} → {r.last_order_after_discontinuation}
                          </td>
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

function useDiscontinuedFinding(regionCode) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true }));
    apiPost("/ask", {
      question: "Which outlets ordered a discontinued SKU after its discontinuation date?",
      region_code: regionCode || undefined,
    })
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((error) => {
        if (!cancelled) setState({ data: null, loading: false, error });
      });
    return () => {
      cancelled = true;
    };
  }, [regionCode]);
  return state;
}
