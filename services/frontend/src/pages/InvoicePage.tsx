import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  userFetch,
  UserUnauthorizedError,
  UserAuthDisabledError,
} from "../api";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import "../styles/invoice.css";

/**
 * /account/credits/invoice/:ledgerId — standalone printable invoice.
 *
 * Registered OUTSIDE the <Layout /> route in main.tsx so there's no
 * site nav, no footer, nothing but the invoice. Paired with print
 * styles in styles/invoice.css that further hide screen-only controls
 * (the Back link, the Print button) when the user hits Ctrl+P.
 *
 * The invoice is derived entirely from server data — the only
 * client-side logic here is formatting. If the ledger row isn't a
 * stripe_purchase or isn't owned by the caller, the API 404s and we
 * show a friendly error.
 */

interface InvoiceData {
  invoice_number: string;
  issued_at: string;
  status: string;
  customer: {
    email: string;
    display_name: string | null;
  };
  line_item: {
    description: string;
    credits: number;
    amount_cents: number;
    currency: string;
  };
  totals: {
    subtotal_cents: number;
    tax_cents: number;
    total_cents: number;
    currency: string;
  };
  payment: {
    method: string;
    processor: string;
    checkout_session_id: string;
    payment_intent_id: string | null;
  };
  issuer: {
    name: string;
    domain: string;
  };
}

function formatMoney(cents: number, currency: string): string {
  const amount = cents / 100;
  const code = currency.toUpperCase();
  // toLocaleString gets us thousand separators + exact decimal.
  // Currency code appended explicitly so users see "CAD 5.00" not
  // the browser's localized symbol guess.
  return `${amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${code}`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

export default function InvoicePage() {
  const { ledgerId } = useParams<{ ledgerId: string }>();
  const [data, setData] = useState<InvoiceData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useDocumentTitle(
    data
      ? `${data.invoice_number} · Invoice · Canadian Political Data`
      : "Invoice · Canadian Political Data"
  );

  useEffect(() => {
    if (!ledgerId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await userFetch<InvoiceData>(`/me/credits/invoice/${ledgerId}`);
        if (!cancelled) setData(res);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof UserUnauthorizedError) {
          setError("You need to be signed in to view this invoice.");
        } else if (e instanceof UserAuthDisabledError) {
          setError("Accounts are disabled on this server.");
        } else if (e instanceof Error && /^404\b/.test(e.message)) {
          setError("Invoice not found — or this transaction doesn't have one.");
        } else {
          setError(e instanceof Error ? e.message : "Failed to load invoice.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ledgerId]);

  const onPrint = useCallback(() => {
    window.print();
  }, []);

  if (loading) {
    return (
      <main className="invoice invoice--state">
        <p>Loading invoice…</p>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="invoice invoice--state">
        <p className="invoice__error">{error ?? "Unknown error."}</p>
        <p>
          <Link to="/account/credits">← Back to credits</Link>
        </p>
      </main>
    );
  }

  return (
    <main className="invoice">
      {/* Screen-only toolbar — hidden by @media print */}
      <nav className="invoice__toolbar no-print">
        <Link to="/account/credits" className="invoice__backlink">
          ← Back to credits
        </Link>
        <button
          type="button"
          className="invoice__print-btn"
          onClick={onPrint}
        >
          Print / Save as PDF
        </button>
      </nav>

      <article className="invoice__doc">
        <header className="invoice__header">
          <div className="invoice__brand">
            <h1 className="invoice__brand-name">{data.issuer.name}</h1>
            <p className="invoice__brand-domain">{data.issuer.domain}</p>
          </div>
          <div className="invoice__stamp">
            <div className="invoice__stamp-label">Invoice</div>
            <div className="invoice__stamp-number">{data.invoice_number}</div>
            <div className="invoice__stamp-date">{formatDate(data.issued_at)}</div>
          </div>
        </header>

        <section className="invoice__parties">
          <div>
            <h2>Billed to</h2>
            <p>
              {data.customer.display_name && (
                <>
                  {data.customer.display_name}
                  <br />
                </>
              )}
              {data.customer.email}
            </p>
          </div>
          <div>
            <h2>Payment</h2>
            <p>
              Card via {data.payment.processor === "stripe" ? "Stripe" : data.payment.processor}
              <br />
              Status: <span className="invoice__status">{data.status}</span>
            </p>
          </div>
        </section>

        <section className="invoice__items">
          <table>
            <thead>
              <tr>
                <th scope="col">Description</th>
                <th scope="col" className="invoice__col-qty">Qty</th>
                <th scope="col" className="invoice__col-amount">Amount</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <div className="invoice__item-title">{data.line_item.description}</div>
                  <div className="invoice__item-sub">
                    One-time purchase · credits do not expire
                  </div>
                </td>
                <td className="invoice__col-qty">1</td>
                <td className="invoice__col-amount">
                  {formatMoney(data.line_item.amount_cents, data.line_item.currency)}
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <section className="invoice__totals">
          <dl>
            <div>
              <dt>Subtotal</dt>
              <dd>{formatMoney(data.totals.subtotal_cents, data.totals.currency)}</dd>
            </div>
            <div>
              <dt>Tax</dt>
              <dd>{formatMoney(data.totals.tax_cents, data.totals.currency)}</dd>
            </div>
            <div className="invoice__totals-grand">
              <dt>Total paid</dt>
              <dd>{formatMoney(data.totals.total_cents, data.totals.currency)}</dd>
            </div>
          </dl>
        </section>

        <section className="invoice__details">
          <h2>Transaction details</h2>
          <dl>
            <dt>Stripe checkout session</dt>
            <dd className="invoice__mono">{data.payment.checkout_session_id}</dd>
            {data.payment.payment_intent_id && (
              <>
                <dt>Payment intent</dt>
                <dd className="invoice__mono">{data.payment.payment_intent_id}</dd>
              </>
            )}
          </dl>
        </section>

        <footer className="invoice__footer">
          <p>
            Credits power premium features on {data.issuer.domain}. This invoice
            documents a one-time credit purchase processed by Stripe. Canadian
            Political Data is not currently registered for GST-HST collection;
            no tax has been charged on this transaction.
          </p>
          <p>
            Questions about this purchase? Reply to the confirmation email or
            contact <span className="invoice__mono">support@{data.issuer.domain}</span>.
          </p>
        </footer>
      </article>
    </main>
  );
}
