export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface CommandArg {
  name: string;
  type: "int" | "str" | "date" | "bool";
  required: boolean;
  default?: number | string | boolean;
  help?: string;
}

export interface CommandSpec {
  key: string;
  category: "hansard" | "bills" | "enrichment" | "maintenance";
  description: string;
  args: CommandArg[];
}

export interface CommandsResponse {
  commands: CommandSpec[];
}

export interface JobRow {
  id: string;
  command: string;
  args: Record<string, unknown>;
  status: JobStatus;
  priority: number;
  schedule_id: string | null;
  requested_by: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  stdout_snippet?: string;
  stderr_snippet?: string;
  stdout_tail?: string;
  stderr_tail?: string;
  error: string | null;
}

export interface JobsListResponse {
  jobs: JobRow[];
}

export interface ScheduleRow {
  id: string;
  name: string;
  command: string;
  args: Record<string, unknown>;
  cron: string;
  enabled: boolean;
  last_enqueued_at: string | null;
  next_run_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface SchedulesResponse {
  schedules: ScheduleRow[];
}

export interface AdminStats {
  speeches: number;
  chunks: { total: number; embedded: number; pending: number };
  jobs: { queued: number; running: number; succeeded_24h: number; failed_24h: number };
  jurisdictions: { live: number; total: number };
  recent_failures: Array<{
    id: string;
    command: string;
    finished_at: string;
    error: string | null;
  }>;
}
