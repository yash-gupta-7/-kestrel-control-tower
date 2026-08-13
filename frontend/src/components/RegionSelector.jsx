import { useRegion } from "../lib/RegionContext";

// Visible on every page (top bar) so the active regional-manager scope is
// never hidden state -- see DECISIONS.md, "regional-manager view".
export default function RegionSelector() {
  const { regionCode, setRegionCode, regions, regionsLoading } = useRegion();

  return (
    <div className="region-selector">
      <span className="control-label">Region</span>
      <select
        className="select"
        value={regionCode}
        onChange={(e) => setRegionCode(e.target.value)}
        disabled={regionsLoading}
        title="Scopes Service, Money, Cold Chain and Ask Anything to one region. Price Position is unaffected (scraped by city, not region)."
      >
        <option value="">All Regions</option>
        {regions.map((r) => (
          <option key={r.region_code} value={r.region_code}>
            {r.region_name}
            {r.regional_manager ? ` — ${r.regional_manager}` : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
