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
import BlogListPage from "./pages/BlogListPage";
import BlogPostPage from "./pages/BlogPostPage";
import CoveragePage from "./pages/CoveragePage";
import { AdminLayout } from "./components/AdminLayout";
import AdminLogin from "./pages/admin/AdminLogin";
import AdminDashboard from "./pages/admin/AdminDashboard";
import AdminJobs from "./pages/admin/AdminJobs";
import AdminJobDetail from "./pages/admin/AdminJobDetail";
import AdminSchedules from "./pages/admin/AdminSchedules";

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
          <Route path="blog" element={<BlogListPage />} />
          <Route path="blog/:slug" element={<BlogPostPage />} />
          <Route path="coverage" element={<CoveragePage />} />
          <Route path="admin/login" element={<AdminLogin />} />
          <Route path="admin" element={<AdminLayout />}>
            <Route index element={<AdminDashboard />} />
            <Route path="jobs" element={<AdminJobs />} />
            <Route path="jobs/:id" element={<AdminJobDetail />} />
            <Route path="schedules" element={<AdminSchedules />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
