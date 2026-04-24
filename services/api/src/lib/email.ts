import nodemailer from "nodemailer";
import type { Transporter } from "nodemailer";
import { config } from "../config.js";

/**
 * Email adapter. Thin wrapper around nodemailer's SMTP transport so the
 * provider choice (Proton today, SES/Postmark/Resend tomorrow) stays a
 * configuration concern, not a code concern.
 *
 * Dev-stub mode: if SMTP credentials are absent, `sendMail` logs the
 * message to server logs and returns a success. This lets the rest of
 * the auth surface be smoke-tested end-to-end without provisioning an
 * SMTP account first. Magic-link URLs are fully visible in logs, which
 * is the point.
 *
 * Proton submission note: smtp.protonmail.ch:587 uses STARTTLS. That is
 * `secure: false` (STARTTLS is negotiated post-connect) with `requireTLS:
 * true` to refuse plaintext fallback.
 */

export interface SendMailArgs {
  to: string;
  subject: string;
  text: string;
  html?: string;
}

function isConfigured(): boolean {
  return Boolean(config.smtp.username && config.smtp.password && config.smtp.from);
}

let cachedTransport: Transporter | null = null;
function getTransport(): Transporter {
  if (!cachedTransport) {
    cachedTransport = nodemailer.createTransport({
      host: config.smtp.host,
      port: config.smtp.port,
      secure: false,        // STARTTLS on 587, not implicit TLS
      requireTLS: true,
      auth: {
        user: config.smtp.username,
        pass: config.smtp.password,
      },
    });
  }
  return cachedTransport;
}

async function sendMail(args: SendMailArgs, logger?: { info: (o: object, m: string) => void }): Promise<void> {
  if (!isConfigured()) {
    // Dev stub: log the message so the developer can copy-paste the link
    // out of the API container's stdout.
    const log = logger?.info.bind(logger) ?? ((o: object, m: string) => console.log(m, o));
    log(
      { to: args.to, subject: args.subject, body: args.text },
      "[email:stub] SMTP not configured; logging message instead of sending"
    );
    return;
  }
  await getTransport().sendMail({
    from: config.smtp.from,
    to: args.to,
    subject: args.subject,
    text: args.text,
    html: args.html,
  });
}

/** Send a one-time magic link. `url` already includes the nonce. */
export async function sendMagicLink(
  args: { to: string; url: string },
  logger?: { info: (o: object, m: string) => void }
): Promise<void> {
  const text =
    `Sign in to Canadian Political Data\n\n` +
    `Click the link below to complete sign-in. It expires in 15 minutes and can only be used once.\n\n` +
    `${args.url}\n\n` +
    `If you did not request this email, you can safely ignore it — no account was created.\n`;
  const html =
    `<p>Sign in to <strong>Canadian Political Data</strong></p>` +
    `<p>Click the link below to complete sign-in. It expires in 15 minutes and can only be used once.</p>` +
    `<p><a href="${args.url}">${args.url}</a></p>` +
    `<p>If you did not request this email, you can safely ignore it — no account was created.</p>`;
  await sendMail({ to: args.to, subject: "Sign in to Canadian Political Data", text, html }, logger);
}

/** Alert digest — phase 1 placeholder; full template lands in task #10. */
export async function sendAlertDigest(
  args: { to: string; subject: string; text: string; html?: string },
  logger?: { info: (o: object, m: string) => void }
): Promise<void> {
  await sendMail(args, logger);
}

/**
 * Notify a user that their correction was accepted and credits were
 * granted. Called fire-and-forget from the admin PATCH handler — a
 * failure here must not roll back the ledger grant.
 *
 * Callers suppress by checking users.email_bounced_at before invoking
 * (same discipline as the alerts worker).
 */
export async function sendCorrectionApprovedEmail(
  args: {
    to: string;
    displayName: string | null;
    correctionIssue: string;
    creditsGranted: number;
    newBalance: number;
    accountUrl: string;
  },
  logger?: { info: (o: object, m: string) => void }
): Promise<void> {
  const greeting = args.displayName?.trim()
    ? `Hi ${args.displayName.trim()},`
    : "Hi,";
  const issueSnippet =
    args.correctionIssue.length > 240
      ? `${args.correctionIssue.slice(0, 240)}…`
      : args.correctionIssue;

  const subject = `Your correction was accepted — +${args.creditsGranted} credits`;

  const text =
    `${greeting}\n\n` +
    `Thank you for helping improve Canadian Political Data.\n\n` +
    `We've reviewed and applied the correction you submitted:\n\n` +
    `  "${issueSnippet}"\n\n` +
    `As a thank-you, we've added ${args.creditsGranted} credits to your account. ` +
    `Your new balance is ${args.newBalance} credits.\n\n` +
    `Credits can be spent on premium features at ${args.accountUrl}\n\n` +
    `Keep the corrections coming — every fix makes the public record more accurate.\n\n` +
    `— Canadian Political Data\n`;

  const safeIssue = issueSnippet
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const html =
    `<p>${greeting}</p>` +
    `<p>Thank you for helping improve <strong>Canadian Political Data</strong>.</p>` +
    `<p>We've reviewed and applied the correction you submitted:</p>` +
    `<blockquote style="margin:0.75em 0;padding:0.5em 1em;border-left:3px solid #e11d48;color:#4a5a75;">` +
    safeIssue +
    `</blockquote>` +
    `<p>As a thank-you, we've added <strong>${args.creditsGranted} credits</strong> to your account. ` +
    `Your new balance is <strong>${args.newBalance} credits</strong>.</p>` +
    `<p>Credits can be spent on premium features at <a href="${args.accountUrl}">${args.accountUrl}</a>.</p>` +
    `<p>Keep the corrections coming — every fix makes the public record more accurate.</p>` +
    `<p style="color:#6a7a95;font-size:0.9em;">— Canadian Political Data</p>`;

  await sendMail({ to: args.to, subject, text, html }, logger);
}

export const emailIsConfigured = isConfigured;
