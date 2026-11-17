// Types of the backend's §19 JSON contracts, as the API returns them. The frontend never adds fields of its own.

export interface ReadBasis {
  batch_seq: number;
  mode: string;
  as_of: string | null;
  banner?: string;
  notes?: string[];
}

export interface Evidence {
  record_id: string;
  kind: string;
  system: string;
  source_class?: string;
  data_class: string;
  batch_seq: number;
  source_path?: string;
  locator?: Record<string, unknown>;
  excerpt: string;
  knowledge_basis?: string;
}

export interface Claim {
  text: string;
  kind?: string;
  evidence: string[];
  verification?: string;
  grade?: string;
}

export interface Disagreement {
  subject: string;
  metric_match: string;
  definition: string;
  period: string;
  claims: { record_id: string; value: string; unit: string; period: string | null; source: string }[];
}

export interface Refusal {
  policy: string;
  title?: string;
  reason: string;
  alternative?: string;
}

export type AnswerStatus = "answered" | "qualified" | "conflict" | "abstained" | "refused";

export interface Answer {
  status: AnswerStatus;
  interpretation: string;
  answer: string;
  grade: { value: "A" | "B" | "C" | "D"; label: string; reasons: string[] };
  qualifiers: string[];
  claims: Claim[];
  disagreements: Disagreement[];
  unknowns: string[];
  refusal: Refusal | null;
  read_basis: ReadBasis;
  evidence: Evidence[];
  audit_id: string;
  pipeline_version: string;
}

export interface TodayItem {
  business_object: string;
  source: string;
  title: string;
  why_shown: string;
  evidence: string[];
  unknowns: string[];
  time_context: Record<string, string>;
}

export interface Today {
  today: {
    items: TodayItem[];
    ranked: boolean;
    rule: string;
    candidate_count: number;
    candidates?: string[];
    explanation: string;
    not_shown_because_unmeasurable: string[];
  };
  top_three: null;
  ranking_mode: string;
  owner_decision_status: string;
  read_basis: ReadBasis;
  snapshot_id: string;
  full_briefing: string;
}

export interface ConflictSide {
  record_id: string;
  value: string;
  unit: string;
  period: string | null;
  source: string;
  provenance: { version: number; batch_seq: number; source_path: string } | null;
  time: {
    recorded_day: string | null;
    recorded_basis: string;
    valid_from: string | null;
    valid_to: string | null;
    valid_basis: string;
    extracted_at: string;
  } | null;
}

export interface Conflict {
  conflict_id: string;
  subject: string;
  metric_match: string;
  definition: string;
  period: string;
  status: "open";
  claims: ConflictSide[];
}

export interface ConflictRegister {
  register_version: string;
  read_basis: ReadBasis;
  conflicts: Conflict[];
  unknowns: string[];
}

export interface PackState {
  pack: string;
  release_at: string;
  released: boolean;
  admitted: boolean;
  status: string;
  record_count: number | null;
}

export interface Releases {
  now: string;
  packs: PackState[];
}

export interface AgentSummary {
  agent_id: string;
  category: "engineering" | "finance" | "people_ops" | "marketing";
  activation: "blocked" | "synthetic_test" | "active";
  config_version: number;
  assigned: boolean;
  tools: string[];
}

export interface AgentRegistry {
  runtime_version: string;
  agents: AgentSummary[];
}

export interface AgentSession {
  session_id: string;
  agent_id: string;
  config_version: number;
  batch_seq: number;
  label: string;
}

export interface AgentTurn {
  status: "answered" | "qualified" | "abstained" | "refused" | "unavailable" | "failed";
  label: string;
  execution_id: string;
  agent_id: string;
  config_version: number;
  runtime_version: string;
  read_basis: ReadBasis;
  claims: { text: string; fact_id: string; evidence: string[]; knowledge_basis: string }[];
  evidence: Evidence[];
  tools: { tool: string; decision: string; reason: string; status: string | null }[];
  unknowns: string[];
  excluded: Record<string, number>;
  refusal: Refusal | null;
  escalation: { to: string; how: string } | null;
  replayed: boolean;
}

export interface MemoryItem {
  memory_id: string;
  tier: "working" | "episodic" | "semantic" | "interview" | "reflective";
  label: string;
  content: Record<string, unknown>;
  knowledge_basis: string | null;
  source_records: string[];
  batch_seq: number;
  created_at: string;
}

export interface Memories {
  agent_id: string;
  batch_seq: number;
  withheld: number;
  items: MemoryItem[];
}

export interface RiskHistory {
  lifecycle_version: string;
  knowledge_points: number[];
  ranked: false;
  items: {
    item_id: string;
    kind: "watchlist_risk" | "evidence_signal";
    title: string;
    business_object: string;
    evidence: string[];
    unknowns: string[];
    events: { batch_seq: number; event: "opened" | "changed" | "resolved" | "reopened"; fields?: string[]; reason?: string }[];
  }[];
}
