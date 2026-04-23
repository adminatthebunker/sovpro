import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  userFetch,
  UserUnauthorizedError,
  UserAuthDisabledError,
} from "../api";
import { useUserAuth } from "../hooks/useUserAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

/**
 * /account/credits — buy credit packs and view ledger history.
 *
 * The purchase flow is hosted entirely by Stripe: this page POSTs to
 * /me/credits/checkout, gets a Checkout Session URL, and assigns
 * window.location to it. Stripe redirects back to
 * /account/credits?purchase=success|cancel on completion. On success
 * we show a confirmation banner; the actual balance update comes from
 * the webhook handler server-side (may arrive a second or two later),
 * so the page auto-refreshes /me/credits once shortly after landing.
 */

interface LedgerEntry {
  id: string;
  delta: number;
  state: "pending" | "held" | "committed" | "refunded";
  kind:
    | "stripe_purchase"
    | "admin_credit"
    | "report_hold"
    | "report_commit"
    | "report_refund";
  // reference_id is deliberately stripped from /me/credits responses —
  // admin endpoints retain it. See services/api/src/routes/credits.ts.
  reason: string | null;
  created_at: string;
}

interface CreditsResponse {
  balance: number;
  history: LedgerEntry[];
  stripe_enabled: boolean;
}

interface Pack {
  sku: "small" | "medium" | "large";
  credits: number;
  display_price: string;
  bonus_label: string | null;
}

interface PacksResponse {
  enabled: boolean;
  packs: Pack[];
}

const KIND_LABEL: Record<LedgerEntry["kind"], string> = {
  stripe_purchase: "Credit pack purchase",
  admin_credit: "Granted by admin",
  report_hold: "Report hold",
  report_commit: "Report charge",
  report_refund: "Report refund",
};

const STATE_LABEL: Record<LedgerEntry["state"], string> = {
  pending: "Pending",
  held: "On hold",
  committed: "Final",
  refunded: "Refunded",
};

export default function CreditsPage() {
  useDocumentTitle("Your credits · Canadian Political Data");
  const { user, loading: authLoading, disabled } = useUserAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const purchaseParam = searchParams.get("purchase");

  const [data, setData] = useState<CreditsResponse | null>(null);
  const [packs, setPacks] = useState<Pack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checkingOutSku, setCheckingOutSku] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [credits, packsRes] = await Promise.all([
        userFetch<CreditsResponse>("/me/credits"),
        userFetch<PacksResponse>("/me/credits/packs"),
      ]);
      setData(credits);
      setPacks(packsRes.enabled ? packsRes.packs : []);
    } catch (e) {
      if (e instanceof UserUnauthorizedError) {
        setError("Please sign in to view your credits.");
      } else if (e instanceof UserAuthDisabledError) {
        setError("Accounts are disabled on this server.");
      } else {
        setError(e instanceof Error ? e.message : "Load failed.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) void load();
  }, [user, load]);

  // After a successful Stripe redirect, the webhook may race ahead of
  // the browser. Poll once after 2s to give the balance a chance to
  // appear without a manual refresh. This is intentionally one-shot —
  // if the webhook hasn't landed by then, the user can hit reload.
  useEffect(() => {
    if (purchaseParam !== "success") return;
    const t = setTimeout(() => {
      void load();
    }, 2000);
    return () => clearTimeout(t);
  }, [purchaseParam, load]);

  const onBuy = useCallback(async (sku: Pack["sku"]) => {
    setCheckingOutSku(sku);
    setError(null);
    try {
      const res = await userFetch<{ url: string }>("/me/credits/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sku }),
      });
      // Hand off to Stripe. Back-arrow from Stripe's hosted page hits
      // our cancel_url which navigates back here with ?purchase=cancel.
      window.location.assign(res.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Checkout failed.");
      setCheckingOutSku(null);
    }
  }, []);

  const dismissPurchaseBanner = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("purchase");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  if (authLoading) {
    return <section className="cpd-auth"><p>Loading…</p></section>;
  }
  if (disabled) {
    return (
      <section className="cpd-auth">
        <h2>Accounts unavailable</h2>
        <p>User accounts are not configured on this server.</p>
      </section>
    );
  }
  if (!user) {
    return (
      <section className="cpd-auth">
        <h2>You're signed out</h2>
        <p><Link to="/login?from=/account/credits">Sign in →</Link></p>
      </section>
    );
  }

  return (
    <section className="cpd-auth cpd-auth--credits">
      <h2>Your credits</h2>
      <p className="cpd-auth__muted">
        <Link to="/account">← Back to account</Link>
      </p>

      {purchaseParam === "success" && (
        <div className="cpd-auth__ok" role="status">
          Payment complete. Your credits should appear below within a few seconds.
          <button
            type="button"
            className="cpd-auth__linkbtn"
            onClick={dismissPurchaseBanner}
            style={{ marginLeft: "0.5rem" }}
          >
            Dismiss
          </button>
        </div>
      )}
      {purchaseParam === "cancel" && (
        <div className="cpd-auth__warn" role="status">
          Checkout was cancelled. No charge was made.
          <button
            type="button"
            className="cpd-auth__linkbtn"
            onClick={dismissPurchaseBanner}
            style={{ marginLeft: "0.5rem" }}
          >
            Dismiss
          </button>
        </div>
      )}

      {error && <p className="cpd-auth__error" role="alert">{error}</p>}

      {loading || !data ? (
        <p>Loading…</p>
      ) : (
        <>
          <dl className="cpd-auth__meta">
            <dt>Current balance</dt>
            <dd>
              <strong>{data.balance}</strong>{" "}
              <span className="cpd-auth__muted">credits</span>
            </dd>
          </dl>

          <h3>Buy credits</h3>
          {!data.stripe_enabled ? (
            <p className="cpd-auth__muted">
              Credit purchases are not available on this server.
            </p>
          ) : packs.length === 0 ? (
            <p className="cpd-auth__muted">
              No credit packs are currently available. Please check back later.
            </p>
          ) : (
            <ul className="cpd-auth__packs">
              {packs.map((p) => (
                <li key={p.sku} className="cpd-auth__pack">
                  <div className="cpd-auth__pack-head">
                    <span className="cpd-auth__pack-price">{p.display_price}</span>
                    {p.bonus_label && (
                      <span className="cpd-auth__pack-bonus">{p.bonus_label}</span>
                    )}
                  </div>
                  <div className="cpd-auth__pack-credits">
                    {p.credits} credits
                  </div>
                  <button
                    type="button"
                    onClick={() => void onBuy(p.sku)}
                    disabled={checkingOutSku !== null}
                  >
                    {checkingOutSku === p.sku ? "Redirecting…" : "Buy"}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <h3>Ledger history</h3>
          {data.history.length === 0 ? (
            <p className="cpd-auth__muted">No transactions yet.</p>
          ) : (
            <table className="cpd-auth__ledger">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Kind</th>
                  <th scope="col">State</th>
                  <th scope="col" style={{ textAlign: "right" }}>Amount</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((row) => (
                  <tr key={row.id}>
                    <td>{new Date(row.created_at).toLocaleString()}</td>
                    <td>{KIND_LABEL[row.kind]}</td>
                    <td>{STATE_LABEL[row.state]}</td>
                    <td
                      style={{
                        textAlign: "right",
                        color: row.delta > 0 ? "var(--cpd-color-ok, #0a0)" : "inherit",
                      }}
                    >
                      {row.delta > 0 ? "+" : ""}{row.delta}
                    </td>
                    <td>{row.reason ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <p className="cpd-auth__muted" style={{ marginTop: "2rem" }}>
            Credits power premium features like the in-depth "full report"
            analyzer (coming soon). One-time purchases — no subscriptions,
            no auto-renewal.
          </p>
        </>
      )}
    </section>
  );
}
