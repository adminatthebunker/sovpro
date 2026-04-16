import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAdminAuth } from "../hooks/useAdminAuth";
import "../styles/admin.css";

/**
 * Admin shell — renders the sub-nav and gates the nested routes on
 * auth. On logout we push back to /admin/login with a ?from= query so
 * the login page can bounce the user back to the page they were on.
 */
export function AdminLayout() {
  const { isAuthed, logout } = useAdminAuth();
  const loc = useLocation();
  if (!isAuthed) {
    const from = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/admin/login?from=${from}`} replace />;
  }
  return (
    <section className="admin">
      <header className="admin__header">
        <div className="admin__brand">
          <span aria-hidden="true">⚙️</span>
          <h2>Admin</h2>
        </div>
        <nav className="admin__subnav" aria-label="Admin">
          <NavLink to="/admin" end className={({ isActive }) => (isActive ? "active" : "")}>
            Dashboard
          </NavLink>
          <NavLink to="/admin/jobs" className={({ isActive }) => (isActive ? "active" : "")}>
            Jobs
          </NavLink>
          <NavLink to="/admin/schedules" className={({ isActive }) => (isActive ? "active" : "")}>
            Schedules
          </NavLink>
          <button className="admin__logout" onClick={logout}>Logout</button>
        </nav>
      </header>
      <Outlet />
    </section>
  );
}
