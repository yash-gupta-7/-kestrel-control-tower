import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Overview from "./pages/Overview";
import Service from "./pages/Service";
import Money from "./pages/Money";
import AskAnything from "./pages/AskAnything";
import ColdChain from "./pages/ColdChain";
import PricePosition from "./pages/PricePosition";
import { RegionProvider } from "./lib/RegionContext";

export default function App() {
  return (
    <RegionProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="service" element={<Service />} />
            <Route path="money" element={<Money />} />
            <Route path="ask" element={<AskAnything />} />
            <Route path="cold-chain" element={<ColdChain />} />
            <Route path="price-position" element={<PricePosition />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </RegionProvider>
  );
}
