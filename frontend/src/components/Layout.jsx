import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import RegionSelector from "./RegionSelector";
import { lastCompleteFiscalQuarter, fiscalLabel, monthRangeLabel } from "../lib/fiscal";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/service", label: "Service" },
  { to: "/money", label: "Money" },
  { to: "/ask", label: "Ask Anything" },
  { to: "/cold-chain", label: "Cold Chain" },
  { to: "/price-position", label: "Price Position" },
];

const lcq = lastCompleteFiscalQuarter();
const fyRangeLabel = `${fiscalLabel(lcq.fy, lcq.fq)} · ${monthRangeLabel(lcq.start, lcq.end)}`;

const TITLES = {
  "/": ["Control Tower Overview", "Service & financial performance"],
  "/service": ["Service", "Fill rate (eaches) and OTIF, by outlet, region, warehouse and route."],
  "/money": ["Money", "Freight cost per delivered case, and returns as leakage."],
  "/ask": ["Ask Anything", "Plain-English questions, answered from the same numbers as the dashboard."],
  "/cold-chain": ["Cold Chain", "Temperature excursions, near-expiry stock, cold-chain-linked returns."],
  "/price-position": ["Price Position", "Kestrel MRP vs. observed street price, confidently-matched SKUs only."],
};

export default function Layout() {
  const { pathname } = useLocation();
  const [title, sub] = TITLES[pathname] || TITLES["/"];
  const [navOpen, setNavOpen] = useState(false);

  // Close the mobile drawer whenever the route changes, so a nav click
  // doesn't leave it open over the new page.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  return (
    <div className="app-shell">
      {navOpen && <div className="sidebar-backdrop" onClick={() => setNavOpen(false)} />}
      <aside className={"sidebar" + (navOpen ? " open" : "")}>
        <div className="sidebar-brand">
          <div className="name">Kestrel Control Tower</div>
          <div className="sub">Supply Chain Ops</div>
        </div>
        <nav>
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="main">
        <div className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="burger-btn"
              aria-label="Toggle navigation"
              aria-expanded={navOpen}
              onClick={() => setNavOpen((o) => !o)}
            >
              <span />
              <span />
              <span />
            </button>
            <div>
              <h1>{title}</h1>
              <div className="topbar-sub">{sub}</div>
            </div>
          </div>
          <div className="topbar-right">
            <details className="topbar-meta">
              <summary>{fyRangeLabel}</summary>
              <div className="topbar-meta-detail">
                Data: 1 Jan 2025 – 30 Jun 2026. FY runs Apr–Mar, labelled by year-ending (Apr–Jun 2026 =
                FY2027 Q1).
              </div>
            </details>
            <RegionSelector />
          </div>
        </div>
        <div className="page">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
