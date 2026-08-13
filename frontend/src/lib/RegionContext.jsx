import { createContext, useContext, useEffect, useState } from "react";
import { apiGet } from "./api";

// The "regional-manager view" (see DECISIONS.md): a plain scoping filter
// shared across pages via context, not authentication. Anyone can switch
// region from the selector in the top bar; it just narrows what's shown.
// Default is "" (All Regions) -- everything behaves exactly as before
// this was added when no region is selected.
const RegionContext = createContext({
  regionCode: "",
  setRegionCode: () => {},
  regions: [],
  regionsLoading: true,
});

export function RegionProvider({ children }) {
  const [regionCode, setRegionCode] = useState("");
  const [regions, setRegions] = useState([]);
  const [regionsLoading, setRegionsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiGet("/meta/regions")
      .then((data) => {
        if (!cancelled) setRegions(data || []);
      })
      .catch(() => {
        // Region selector degrades to "All Regions only" if this fails --
        // every page still works, it just can't offer per-region scoping.
        if (!cancelled) setRegions([]);
      })
      .finally(() => {
        if (!cancelled) setRegionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <RegionContext.Provider value={{ regionCode, setRegionCode, regions, regionsLoading }}>
      {children}
    </RegionContext.Provider>
  );
}

export function useRegion() {
  return useContext(RegionContext);
}
