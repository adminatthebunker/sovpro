import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom";
import { useUserAuth } from "../hooks/useUserAuth";
import "../styles/admin.css";

/**
 * Admin shell. Access = "signed-in user with is_admin=true". Unauthed
 * visitors are bounced to the shared /login page; signed-in non-admins
 * see a small 403 surface (not a redirect — the user has an account,
 * they just lack the role, so redirecting to /login is confusing).
 */
export function AdminLayout() {
  const { user, loading, logout } = useUserAuth();
  const loc = useLocation();

  if (loading) {
    return <section className="admin admin--login"><p>Checking session…</p></section>;
  }

  if (!user) {
    const from = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/login?from=${from}`} replace />;
  }

  if (!user.is_admin) {
    return (
      <section className="admin admin--login">
        <header className="admin__header">
          <div className="admin__brand">
            <span aria-hidden="true">⚙️</span>
            <h2>Admin</h2>
          </div>
        </header>
        <p>Your account ({user.email}) does not have admin access.</p>
        <p>
          <button className="admin__logout" onClick={() => logout()}>Sign out</button>
        </p>
      </section>
    );
  }

  return (
    <section className="admin">
      <header className="admin__header">
        <div className="admin__brand">
          <span aria-hidden="true">⚙️</span>
          <h2>Admin</h2>
          <span className="admin__who">{user.email}</span>
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
          <NavLink to="/admin/socials" className={({ isActive }) => (isActive ? "active" : "")}>
            Socials
          </NavLink>
          <NavLink to="/admin/corrections" className={({ isActive }) => (isActive ? "active" : "")}>
            Corrections
          </NavLink>
          <button className="admin__logout" onClick={() => logout()}>Sign out</button>
        </nav>
      </header>
      <Outlet />
    </section>
  );
}
