// Response shapes of the workspace, insights, threads, agent-update, data-room and evidence APIs.
import type { Answer } from "./types";

export interface Me {
  tenant: { tenant_id: string; slug: string; display_name: string; time_zone: string; synthetic: boolean;
            admission_mode: string; evidence_access: string; status: string };
  principal: { principal_id: string; display_name: string | null; email: string | null; status: string };
  relations: { tuple_id: string; resource: string; relation: string }[];
  access: { mode: string; company: boolean; sources: string[]; departments: string[] };
}
export interface Kpi { key: string; label: string; value: string | number; unit: string | null; basis: string;
                       evidence: string[] }
export interface Overview { kpis: Kpi[]; notes: string[]; synthetic: boolean;
                            read_basis: { batch_seq: number; banner: string } }
export interface ChartSource { system: string; rows: number; file: string; columns: string[] }
export interface Series { system: string; x: string; y: string | null; agg: string; split: string | null;
                          points: { x: string; split: string | null; value: number }[]; records: number;
                          sources: string[]; basis: string }
export interface AgentUpdate { update_id: string; session_id: string; agent_id: string; department: string;
  kind: string; title: string; summary: string; status: string; project: string | null; evidence: string[];
  suggested_follow_up: string | null; synthetic: boolean; created_at: string }
export interface ThreadSummary { thread_id: string; title: string; archived: boolean; answers: number;
                                 created_at: string; last_at: string | null; context: Record<string, unknown> }
export interface Thread extends ThreadSummary { turns: { seq: number; question: string; asked_at: string;
                                                         answer: Answer }[] }
export interface ThreadTurn { thread_id: string; seq: number; asked: string; answer: Answer }
export interface EvidenceDetail { record_id: string; file_name: string; kind: string; system: string;
  data_class: string; locator: Record<string, unknown>; version: number; batch_seq: number; extracted_at: string;
  parser_version: string; content_sha256: string; content: Record<string, unknown>;
  versions: { version: number; batch_seq: number; extracted_at: string }[]; admission_state: string;
  access: string; original: { available: boolean; href: string | null; note: string | null } }
export interface Department { department_id: string; name: string; proposed_from: string | null; role_count: number }
export interface SourceType { source_type: string; version: number; title: string; department_id: string;
  kind: string; system: string; data_class: string; records: string; file_glob: string; key_columns: string[] }
export interface Submission { submission_id: string; department_id: string; source_type: string; title: string;
  data_period: string | null; submitted_by: string; submitted_at: string; state: string; state_at: string;
  validation_ok: boolean | null }
export interface SubmissionList { submissions: Submission[]; can_review: string[]; can_authorise: boolean }
export interface SubmissionDetail extends Submission {
  files: { file_name: string; sha256: string; size_bytes: number; media_type: string }[];
  events: { state: string; actor: string; reason: string | null; at: string }[];
  validation: { ok: boolean; report: { records?: number; errors: { reason: string; detail: string }[];
    warnings: string[]; duplicates: unknown[]; classification: Record<string, unknown>;
    by_file?: Record<string, { records: number; data_class: string; sheets: string[];
                               preview: { locator: string; fields: Record<string, string | null> }[] }> } } | null }
export interface Role { resource_type: "tenant" | "department"; resource_id: string; relation: string }
export interface Members { members: { principal_id: string; display_name: string; email: string | null;
  status: string; relations: { tuple_id: string; resource: string; relation: string }[] }[] }
export interface Clarification { request_id: string; department_id: string; question: string; state: string;
  suggested_recipient: string | null; created_by: string; created_at: string }
export interface ClarificationCreated { request_id: string; state: string;
  suggested_recipient: { principal_id: string; display_name: string } | null; delivery: string }

// Company intelligence (0021): the 14 agent people, the ranked worry list, findings, conflicts, connectors.
export interface Person {
  agent_id: string; grounded: boolean; person_record?: string; name?: string; title?: string; department?: string;
  status?: string; verified_facts?: { fact: string; value: string; record: string }[];
  workload?: { open_tasks: number | null; open_points: number | null; repositories: { repo: string; first: string;
    last: string }[]; tasks: { key: string; summary: string | null; status: string | null }[]; rota_weeks: string[];
    incidents_responded: string[]; requisitions_owned: string[]; basis: string };
  assumptions?: { assumption_id: string; category: string; value: string; label: string }[];
  unknowns: string[]; knowledge_point?: number;
}
export interface Criterion { status: string; measure: string | null; low: number | null; high: number | null;
  v_low: number; v_high: number; weight: number; contribution_low: number; contribution_high: number; reason: string }
export interface WorryItem { rank: number; tied: boolean; item_id: string; title: string; department: string | null;
  project: string | null; severity: { low: number; high: number; measured_weight: number;
    criteria: Record<string, Criterion> }; confidence: string; evidence: string[]; unknowns: string[];
  sensitive: boolean; follow_up: string; why: string }
export interface Worry { ranking_mode: "weighted" | "pending"; policy: Record<string, unknown>;
  read_basis: { batch_seq: number; banner: string }; ranked: WorryItem[];
  unrankable?: { item_id: string; title: string; reasons: string[]; evidence: string[]; unknowns: string[] }[];
  unranked?: { item_id: string; title: string; evidence: string[] }[]; hidden_for_access: number; note?: string }
export interface Finding { finding_id: string; title: string; description: string; department: string | null;
  project: string | null; trigger: string; evidence: string[]; confidence: string; unknowns: string[]; state: string;
  created_at: string; updated_at: string; history: { state: string; actor: string; note: string | null; at: string }[];
  notified: { recipient: string; basis: string; delivered_at: string }[] }
export interface ConnectorStatus { connectors: { connector: string; state: string; reason?: string }[];
  runs: { connector: string; mode: string; status: string; fetched: number; changed: number; submitted: number;
    error: string | null; started_at: string }[];
  model: Record<string, unknown>; gemini_experiment: { key_configured: boolean; synthetic_tenants: string[];
    scope: string } }
export interface Workforce { state: "ok" | "denied" | "no_data" | string; total?: number;
  by_department?: { label: string; count: number }[]; by_status?: { label: string; count: number }[];
  basis?: string; evidence_records?: number; read_basis: { batch_seq: number; banner: string } }
export interface Wording { mode: "experimental_model" | "facts_only"; reason?: string; label?: string; model?: string;
  summary?: string; claims?: Record<string, string>; rejections?: string[]; latency_ms?: number | null; fallback?: boolean }
export interface Brief { read_basis: { batch_seq: number; banner: string }; lines: { text: string; evidence: string[] }[];
  method: string; wording?: Wording }
