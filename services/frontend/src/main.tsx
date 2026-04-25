import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import "leaflet/dist/leaflet.css";
import "./styles/global.css";
import "./styles/user-auth.css";
import { Layout } from "./components/Layout";
import { UserAuthProvider } from "./hooks/useUserAuth";
import Lander from "./pages/Lander";
import MapPage from "./pages/MapPage";
import PoliticiansPage from "./pages/PoliticiansPage";
import PoliticianDetail from "./pages/PoliticianDetail";
import BlogListPage from "./pages/BlogListPage";
import BlogPostPage from "./pages/BlogPostPage";
import CoveragePage from "./pages/CoveragePage";
import HansardSearchPage from "./pages/HansardSearchPage";
import SpeechDetailPage from "./pages/SpeechDetailPage";
import LoginPage from "./pages/LoginPage";
import VerifyPage from "./pages/VerifyPage";
import AccountPage from "./pages/AccountPage";
import SavedSearchesPage from "./pages/SavedSearchesPage";
import CorrectionsPage from "./pages/CorrectionsPage";
import AccountCorrectionsPage from "./pages/AccountCorrectionsPage";
import CreditsPage from "./pages/CreditsPage";
import InvoicePage from "./pages/InvoicePage";
import ReportsListPage from "./pages/ReportsListPage";
import ReportViewerPage from "./pages/ReportViewerPage";
import { AdminLayout } from "./components/AdminLayout";
import AdminDashboard from "./pages/admin/AdminDashboard";
import AdminJobs from "./pages/admin/AdminJobs";
import AdminJobDetail from "./pages/admin/AdminJobDetail";
import AdminSchedules from "./pages/admin/AdminSchedules";
import AdminSocialsReview from "./pages/admin/AdminSocialsReview";
import AdminCorrections from "./pages/admin/AdminCorrections";
import AdminUsers from "./pages/admin/AdminUsers";
import AdminReports from "./pages/admin/AdminReports";

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
      <UserAuthProvider>
        <Routes>
        {/* Invoice pages render OUTSIDE the main Layout — no site nav,
            no footer, print-clean by design. See InvoicePage.tsx. */}
        <Route path="account/credits/invoice/:ledgerId" element={<InvoicePage />} />
        {/* Report viewer also renders outside the main layout — print-clean,
            same rationale as invoices. See ReportViewerPage.tsx. */}
        <Route path="reports/:id" element={<ReportViewerPage />} />
        <Route element={<Layout />}>
          <Route index element={<Lander />} />
          <Route path="map" element={<MapPage />} />
          <Route path="politicians" element={<PoliticiansPage />} />
          <Route path="politicians/:id" element={<PoliticianDetail />} />
          <Route path="politician/:id" element={<LegacyPoliticianRedirect />} />
          <Route path="search" element={<HansardSearchPage />} />
          <Route path="speeches/:id" element={<SpeechDetailPage />} />
          <Route path="blog" element={<BlogListPage />} />
          <Route path="blog/:slug" element={<BlogPostPage />} />
          <Route path="coverage" element={<CoveragePage />} />
          <Route path="login" element={<LoginPage />} />
          <Route path="auth/verify" element={<VerifyPage />} />
          <Route path="account" element={<AccountPage />} />
          <Route path="account/saved-searches" element={<SavedSearchesPage />} />
          <Route path="account/corrections" element={<AccountCorrectionsPage />} />
          <Route path="account/credits" element={<CreditsPage />} />
          <Route path="account/reports" element={<ReportsListPage />} />
          <Route path="corrections" element={<CorrectionsPage />} />
          <Route path="admin" element={<AdminLayout />}>
            <Route index element={<AdminDashboard />} />
            <Route path="jobs" element={<AdminJobs />} />
            <Route path="jobs/:id" element={<AdminJobDetail />} />
            <Route path="schedules" element={<AdminSchedules />} />
            <Route path="socials" element={<AdminSocialsReview />} />
            <Route path="corrections" element={<AdminCorrections />} />
            <Route path="users" element={<AdminUsers />} />
            <Route path="reports" element={<AdminReports />} />
          </Route>
        </Route>
        </Routes>
      </UserAuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
