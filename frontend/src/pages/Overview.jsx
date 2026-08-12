import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Badge } from "../components/Card";
import { DataState } from "../components/States";
import { MetricTable } from "../components/MetricTable";
import { Callout } from "../components/Callout";
import { Kpi, KpiRow } from "../components/Kpi";
import { useApi } from "../lib/useApi";
import { apiPost, buildQuery } from "../lib/api";
import { lastCompleteFiscalQuarter, lastCompleteCalendarMonth, fiscalLabel } from "../lib/fiscal";
import { formatINR, formatPct, formatNumber } from "../lib/format";

const lcq = lastCompleteFiscalQuarter();
const lastMonth = lastCompleteCalendarMonth();

function sumBy(rows, key) {
  return rows.reduce((acc, r) => acc + (r.metrics[key] ?? 0), 0);
}

export default function Overview() {
  const worstOutlets = useApi(
    "/service/fill-rate" +
      buildQuery({ group_by: "outlet", month: lastMonth, exclude_closed_outlets: true, limit: 5 }),
  );
  const otifByRegion = useApi(
    "/service/otif" + buildQuery({ group_by: "region", fiscal_year: lcq.fy, fiscal_quarter: lcq.fq, limit: 10 }),
  );
  const fillByRegion = useApi("/service/fill-rate" + buildQuery({ group_by: "region", limit: 10, month: lastMonth }));
  const returnsByCategory = useApi(
    "/money/returns-leakage" + buildQuery({ group_by: "category", fiscal_year: lcq.fy, fiscal_quarter: lcq.fq, limit: 20 }),
  );
  const freightByWarehouse = useApi(
    "/money/freight-cost-per-case" + buildQuery({ group_by: "warehouse", fiscal_year: lcq.fy, fiscal_quarter: lcq.fq, limit: 20 }),
  );
  const findings = useSystemicFindings();

  const overallFillRate = fillByRegion.data
    ? (100 * sumBy(fillByRegion.data.rows, "delivered_qty")) / sumBy(fillByRegion.data.rows, "ordered_qty")
    : null;
  const overallOnTime = otifByRegion.data
    ? weightedAvg(otifByRegion.data.rows, "on_time_pct", "n_deliveries")
    : null;
  const overallFulfilment = otifByRegion.data
    ? weightedAvg(otifByRegion.data.rows, "avg_fulfilment_pct", "n_deliveries")
    : null;
  const overallReturnsPct = returnsByCategory.data
    ? (100 * sumBy(returnsByCategory.data.rows, "return_value_inr")) /
      sumBy(returnsByCategory.data.rows, "dispatch_value_inr")
    : null;
  const overallFreightPerCase = freightByWarehouse.data
    ? sumBy(freightByWarehouse.data.rows, "freight_inr") / sumBy(freightByWarehouse.data.rows, "delivered_cases")
    : null;

  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <Badge tone="neutral">{fiscalLabel(lcq.fy, lcq.fq)} · last complete quarter</Badge>{" "}
        <span className="text-faint" style={{ fontSize: 12 }}>
          {lcq.start} to {lcq.end}
        </span>
      </div>

      <Card className="mb-0" style={{ marginBottom: 18 }}>
        <KpiRow>
          <Kpi
            label="Fill rate (eaches), last month"
            value={overallFillRate !== null ? formatPct(overallFillRate) : "…"}
          />
          <Kpi
            label="Avg. fulfilment %, last quarter"
            value={overallFulfilment !== null ? formatPct(overallFulfilment) : "…"}
            sub={`On-time ${overallOnTime !== null ? formatPct(overallOnTime) : "…"} · strict OTIF reads ~0%, see Service`}
          />
          <Kpi
            label="Returns, % of dispatch value"
            value={overallReturnsPct !== null ? formatPct(overallReturnsPct, 2) : "…"}
          />
          <Kpi
            label="Freight, ₹ per delivered case"
            value={overallFreightPerCase !== null ? formatINR(overallFreightPerCase) : "…"}
          />
        </KpiRow>
      </Card>

      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        <Card
          title="Where we're losing service"
          subtitle={`Five lowest fill-rate outlets in ${lastMonth}, excluding closed and test outlets.`}
          right={
            <Link to="/service" className="btn">
              Full view
            </Link>
          }
        >
          <DataState
            loading={worstOutlets.loading}
            error={worstOutlets.error}
            isEmpty={worstOutlets.data && worstOutlets.data.rows.length === 0}
            skeletonRows={5}
          >
            {worstOutlets.data && (
              <MetricTable
                data={worstOutlets.data}
                dimensionLabel="Outlet"
                columns={[
                  {
                    key: "fill_rate_pct",
                    label: "Fill rate",
                    numeric: true,
                    bar: { max: 100, tone: (v) => (v >= 90 ? "good" : v >= 75 ? "warn" : "bad") },
                    format: (v) => formatPct(v),
                  },
                  { key: "delivered_qty", label: "Delivered (ea)", numeric: true, format: (v) => formatNumber(v) },
                ]}
              />
            )}
          </DataState>
        </Card>

        <Card
          title="Where we're losing money"
          subtitle={`Largest return category and freight extremes, ${fiscalLabel(lcq.fy, lcq.fq)}.`}
          right={
            <Link to="/money" className="btn">
              Full view
            </Link>
          }
        >
          <DataState
            loading={returnsByCategory.loading || freightByWarehouse.loading}
            error={returnsByCategory.error || freightByWarehouse.error}
            skeletonRows={5}
          >
            {returnsByCategory.data && freightByWarehouse.data && (
              <MoneySummary returnsData={returnsByCategory.data} freightData={freightByWarehouse.data} />
            )}
          </DataState>
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Network-wide lateness">
          <DataState loading={findings.loading} error={findings.error} skeletonRows={2}>
            {findings.data?.lateRoutes && (
              <Callout variant="systemic">{shortAnswer(findings.data.lateRoutes.answer)}</Callout>
            )}
          </DataState>
          <div style={{ marginTop: 10 }}>
            <Link to="/service">See routes →</Link>
          </div>
        </Card>
        <Card title="Discontinued-SKU ordering">
          <DataState loading={findings.loading} error={findings.error} skeletonRows={2}>
            {findings.data?.discontinued && (
              <Callout variant="systemic">{shortAnswer(findings.data.discontinued.answer)}</Callout>
            )}
          </DataState>
          <div style={{ marginTop: 10 }}>
            <Link to="/money">See outlets →</Link>
          </div>
        </Card>
      </div>
    </div>
  );
}

function shortAnswer(text) {
  const cut = text.indexOf(". ");
  return cut > 40 ? text.slice(0, text.indexOf(". ", cut + 1) + 1) : text;
}

function weightedAvg(rows, valueKey, weightKey) {
  const totalWeight = sumBy(rows, weightKey);
  if (!totalWeight) return null;
  const weighted = rows.reduce((acc, r) => acc + (r.metrics[valueKey] ?? 0) * (r.metrics[weightKey] ?? 0), 0);
  return weighted / totalWeight;
}

function MoneySummary({ returnsData, freightData }) {
  const topReturn = [...returnsData.rows].sort((a, b) => b.metrics.return_value_inr - a.metrics.return_value_inr)[0];
  const freightSorted = [...freightData.rows].sort(
    (a, b) => (b.metrics.freight_inr_per_case ?? 0) - (a.metrics.freight_inr_per_case ?? 0),
  );
  const highest = freightSorted[0];
  const lowest = freightSorted[freightSorted.length - 1];
  return (
    <div>
      {topReturn && (
        <div style={{ marginBottom: 14 }}>
          <div className="control-label">Largest return leakage</div>
          <div style={{ fontSize: 14, marginTop: 4 }}>
            <strong>{topReturn.dimension_label}</strong> — {formatINR(topReturn.metrics.return_value_inr, { compact: true })}{" "}
            <span className="text-faint">({formatPct(topReturn.metrics.returns_pct_of_dispatch, 2)} of dispatch value, leading reason {topReturn.metrics.leading_reason_code})</span>
          </div>
        </div>
      )}
      {highest && lowest && (
        <div>
          <div className="control-label">Freight cost spread, by warehouse</div>
          <div style={{ fontSize: 14, marginTop: 4 }}>
            Highest: <strong>{highest.dimension_label}</strong> at {formatINR(highest.metrics.freight_inr_per_case)}/case
            <br />
            Lowest: <strong>{lowest.dimension_label}</strong> at {formatINR(lowest.metrics.freight_inr_per_case)}/case
          </div>
        </div>
      )}
    </div>
  );
}

function useSystemicFindings() {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiPost("/ask", { question: "Which routes are more than two hours late on more than one delivery in ten?" }),
      apiPost("/ask", { question: "Which outlets ordered a discontinued SKU after its discontinuation date?" }),
    ])
      .then(([lateRoutes, discontinued]) => {
        if (!cancelled) setState({ data: { lateRoutes, discontinued }, loading: false, error: null });
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
