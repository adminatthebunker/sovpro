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

export const emailIsConfigured = isConfigured;
