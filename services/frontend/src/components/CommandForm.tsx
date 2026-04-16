import { FormEvent, useMemo, useState } from "react";
import type { CommandArg, CommandSpec } from "../types/admin";

/**
 * Generic admin-command form.
 *
 * Reads the command whitelist served by /api/v1/admin/commands, lets
 * the user pick one from a categorised dropdown, then renders typed
 * inputs for that command's args. Submits `{command, args}` to the
 * caller's `onSubmit`, which is responsible for the actual POST.
 *
 * Type handling:
 *   - "int"  → <input type="number" step="1">, parsed as Number
 *   - "str"  → <input type="text">
 *   - "date" → <input type="date">, left as ISO date string
 *   - "bool" → <input type="checkbox">
 *
 * Required args show a "*" in the label. Optional args left blank are
 * omitted from the submitted `args` object (so the scanner command
 * uses its own default).
 */
interface CommandFormProps {
  commands: CommandSpec[];
  initialCommand?: string;
  initialArgs?: Record<string, unknown>;
  submitLabel?: string;
  onSubmit(command: string, args: Record<string, unknown>): Promise<void> | void;
  busy?: boolean;
  extra?: React.ReactNode;  // lets callers (e.g. schedule form) render cron input alongside
}

function coerceValue(arg: CommandArg, raw: string | boolean): unknown {
  if (arg.type === "bool") return Boolean(raw);
  if (raw === "" || raw === null || raw === undefined) return undefined;
  if (arg.type === "int") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  }
  return String(raw);
}

export function CommandForm({
  commands, initialCommand, initialArgs, submitLabel = "Run now",
  onSubmit, busy = false, extra,
}: CommandFormProps) {
  const [command, setCommand] = useState<string>(initialCommand ?? commands[0]?.key ?? "");
  const [values, setValues] = useState<Record<string, string | boolean>>(() => {
    const init: Record<string, string | boolean> = {};
    if (initialArgs) {
      for (const [k, v] of Object.entries(initialArgs)) {
        init[k] = typeof v === "boolean" ? v : v == null ? "" : String(v);
      }
    }
    return init;
  });

  const spec = useMemo(
    () => commands.find(c => c.key === command) ?? null,
    [commands, command]
  );

  const grouped = useMemo(() => {
    const out: Record<string, CommandSpec[]> = {};
    for (const c of commands) {
      (out[c.category] ??= []).push(c);
    }
    return out;
  }, [commands]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!spec) return;
    const args: Record<string, unknown> = {};
    for (const arg of spec.args) {
      const raw = values[arg.name];
      const coerced = coerceValue(arg, raw ?? "");
      if (coerced !== undefined && coerced !== "") args[arg.name] = coerced;
    }
    for (const arg of spec.args) {
      if (arg.required && !(arg.name in args)) {
        alert(`Missing required arg: ${arg.name}`);
        return;
      }
    }
    await onSubmit(command, args);
  }

  return (
    <form className="admin__cmd-form" onSubmit={handleSubmit}>
      <label>
        <span>Command</span>
        <select value={command} onChange={e => { setCommand(e.target.value); setValues({}); }}>
          {Object.entries(grouped).map(([cat, cs]) => (
            <optgroup key={cat} label={cat}>
              {cs.map(c => (
                <option key={c.key} value={c.key}>{c.key}</option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {spec && <p className="admin__cmd-desc">{spec.description}</p>}

      {spec?.args.map(arg => {
        const val = values[arg.name];
        if (arg.type === "bool") {
          return (
            <label key={arg.name} className="admin__cmd-arg admin__cmd-arg--bool">
              <input
                type="checkbox"
                checked={typeof val === "boolean" ? val : false}
                onChange={e => setValues(v => ({ ...v, [arg.name]: e.target.checked }))}
              />
              <span>
                {arg.name}
                {arg.required && " *"}
                {arg.help && <small> — {arg.help}</small>}
              </span>
            </label>
          );
        }
        const type = arg.type === "int" ? "number" : arg.type === "date" ? "date" : "text";
        return (
          <label key={arg.name} className="admin__cmd-arg">
            <span>
              {arg.name}
              {arg.required && " *"}
              {arg.help && <small> — {arg.help}</small>}
            </span>
            <input
              type={type}
              value={typeof val === "boolean" ? "" : val ?? ""}
              placeholder={arg.default !== undefined ? String(arg.default) : ""}
              onChange={e => setValues(v => ({ ...v, [arg.name]: e.target.value }))}
              required={arg.required}
              step={arg.type === "int" ? 1 : undefined}
            />
          </label>
        );
      })}

      {extra}

      <div className="admin__cmd-actions">
        <button type="submit" disabled={busy || !spec}>
          {busy ? "Submitting…" : submitLabel}
        </button>
      </div>
    </form>
  );
}
