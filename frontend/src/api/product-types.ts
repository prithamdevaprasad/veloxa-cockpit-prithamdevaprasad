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
