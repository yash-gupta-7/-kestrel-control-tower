import { NavLink, Outlet, useLocation } from "react-router-dom";
import RegionSelector from "./RegionSelector";

const NAV = [
  { to: "/", label: "Overview", weight: null, end: true },
  { to: "/service", label: "Service", weight: "30%" },
  { to: "/money", label: "Money", weight: "25%" },
  { to: "/ask", label: "Ask Anything", weight: "30%" },
  { to: "/cold-chain", label: "Cold Chain", weight: "10%" },
  { to: "/price-position", label: "Price Position", weight: "5%" },
];

const TITLES = {
  "/": ["Control Tower Overview", "Where we're losing service, and where we're losing money — today."],
  "/service": ["Service", "Fill rate (eaches) and OTIF, by outlet, region, warehouse and route."],
  "/money": ["Money", "Freight cost per delivered case, and returns as leakage."],
  "/ask": ["Ask Anything", "Plain-English questions, answered from the same numbers as the dashboard."],
  "/cold-chain": ["Cold Chain", "Temperature excursions, near-expiry stock, cold-chain-linked returns."],
  "/price-position": ["Price Position", "Kestrel MRP vs. observed street price, confidently-matched SKUs only."],
};

export default function Layout() {
  const { pathname } = useLocation();
  const [title, sub] = TITLES[pathname] || TITLES["/"];

  return (
    <div className="app-shell">
      <aside className="sidebar">
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
              {item.weight && <span className="nav-weight">{item.weight}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          Data: 1 Jan 2025 – 30 Jun 2026
          <br />
          FY runs Apr–Mar, labelled by year-ending (Apr–Jun 2026 = FY2027 Q1).
        </div>
      </aside>
      <div className="main">
        <div className="topbar">
          <div>
            <h1>{title}</h1>
            <div className="topbar-sub">{sub}</div>
          </div>
          <RegionSelector />
        </div>
        <div className="page">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
