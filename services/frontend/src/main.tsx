import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import "leaflet/dist/leaflet.css";
import "./styles/global.css";
import { Layout } from "./components/Layout";
import Lander from "./pages/Lander";
import MapPage from "./pages/MapPage";
import PoliticiansPage from "./pages/PoliticiansPage";
import PoliticianDetail from "./pages/PoliticianDetail";

// Legacy /politician/:id → /politicians/:id, preserving any #hash (e.g. #socials)
// so existing deep-links keep the right tab open after the redirect.
function LegacyPoliticianRedirect() {
  const { id } = useParams<{ id: string }>();
  const { hash } = useLocation();
  return <Navigate to={`/politicians/${id ?? ""}${hash}`} replace />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Lander />} />
          <Route path="map" element={<MapPage />} />
          <Route path="politicians" element={<PoliticiansPage />} />
          <Route path="politicians/:id" element={<PoliticianDetail />} />
          <Route path="politician/:id" element={<LegacyPoliticianRedirect />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
