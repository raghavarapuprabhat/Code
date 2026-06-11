/** Typed REST + SSE client for the FastAPI backend (subset needed for F5). */
import { streamSse } from "./sse";

export interface CodeProject {
  id: string;
  project_path: string;
  display_name: string | null;
  last_indexed: string | null;
}

export type DocAudience = "management" | "architecture" | "developer" | string;

export interface DocSummary {
  doc_id: string;
  title: string;
  audience: DocAudience | null;
  sort_order: number;
  generated_at: string | null;
}

export type DocFormat = "markdown" | "html" | "confluence";

export interface DocContent {
  doc_id: string;
  title: string;
  audience: DocAudience | null;
  format: DocFormat;
  content: string;
  generated_at: string | null;
}

export type ChatEvent =
  | { type: "start"; conversation_id: string }
  | { type: "token"; delta: string }
  | { type: "tool"; name: string; args: Record<string, unknown> }
  | { type: "final"; content: string; conversation_id: string }
  | { type: "error"; message: string };

export interface ChatRequest {
  message: string;
  conversation_id?: string;
  scope_key?: string;
  user_id?: string;
}

// --- SRE Triage Agent (architecture §9) ------------------------------------

export interface SreHypothesis {
  id: string;
  statement: string;
  posterior?: number;
  prior?: number;
  status?: string;
  pinned?: boolean;
  source?: string;
}

export interface SreStep {
  n: number;
  thought?: string;
  action?: string;
  observation?: string;
}

export interface SreEvidence {
  id: string;
  source: string;
  citation: string;
  finding: string;
  bears_on?: string[];
}

export interface SreProbe {
  tool: string;
  target?: string;
  environment?: string;
  summary?: string;
}

export interface SreSeverity {
  level?: string;
  blast_radius?: string;
  endpoints_affected?: string[];
  hotspot_score?: number;
  rationale?: string;
}

export interface SreVerdict {
  classification: "bug" | "not_a_bug" | "needs_more_info" | "external" | string;
  confidence?: number;
  root_cause?: string;
  rationale?: string;
  citations?: string[];
  likely_files?: string[];
  questions?: string[];
  next_step?: string;
}

export interface SreQuestion {
  id?: string;
  text?: string;
  options?: string[] | null;
  blocks?: string;
}

export type SreEvent =
  | { type: "start"; conversation_id: string }
  | { type: "node"; name: string }
  | { type: "rag"; hits: Array<{ path?: string; score?: number; collection?: string }> }
  | { type: "hypothesis"; hypothesis: SreHypothesis }
  | { type: "step"; step: SreStep }
  | { type: "evidence"; evidence: SreEvidence }
  | { type: "probe"; probe: SreProbe }
  | { type: "severity"; severity: SreSeverity }
  | { type: "verdict"; verdict: SreVerdict }
  | { type: "handoff"; target: string; payload: Record<string, unknown> }
  | { type: "question"; question: SreQuestion; conversation_id: string }
  | { type: "final"; conversation_id: string; verdict?: SreVerdict; awaiting?: string }
  | { type: "error"; message: string };

export interface CalibrationStats {
  project_id: string;
  n: number;
  brier_score: number | null;
  accuracy: number | null;
  mean_confidence: number | null;
  bands?: Array<{ confidence_low: number; confidence_high: number; n: number; mean_actual: number }>;
}

// --- Code Doc Hub v0.5 (architecture §8.9) ---------------------------------

export interface EvalResult {
  project_id: string;
  score: number | null;
  total?: number;
  passed?: number;
  created_at?: string;
  items?: Array<{ question: string; grounded: boolean; citation: boolean; answer: string }>;
}

export interface DigestEntry {
  period: string;
  digest_md: string;
  created_at: string;
}

export interface RunStatus {
  project_id: string;
  last_indexed: string | null;
  status: "ok" | "error" | "never" | string;
  run: {
    mode: string;
    files_indexed: number;
    summaries: number;
    gap_count: number;
    error_count: number;
    errors: unknown[];
    model_hash: string;
    duration_ms: number;
    created_at: string;
  } | null;
}

export interface TraceRow {
  work_item_id: string;
  title: string;
  wi_type: string;
  state: string;
  components: string[];
  business_rules: string[];
  tests: string[];
  status: "implemented" | "partial" | "unimplemented" | string;
}

export type ConversationState = "running" | "paused" | "concluded" | "expired";

export interface TriageStateResponse {
  conversation_id: string;
  state: ConversationState;
  pending_question?: SreQuestion | null;
  paused_at?: string | null;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  async listProjects(): Promise<CodeProject[]> {
    const data = await getJson<{ projects: CodeProject[] }>("/agents/code_doc/projects");
    return data.projects;
  },

  async listDocs(projectId: string): Promise<DocSummary[]> {
    const data = await getJson<{ docs: DocSummary[] }>(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/docs`,
    );
    return data.docs;
  },

  async getDoc(projectId: string, docId: string, format: DocFormat = "markdown"): Promise<DocContent> {
    return getJson<DocContent>(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(
        docId,
      )}?format=${format}`,
    );
  },

  async indexProject(projectPath: string, mode: "full" | "incremental", displayName?: string) {
    const res = await fetch("/agents/code_doc/index", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ project_path: projectPath, mode, display_name: displayName }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.json();
  },

  chat(agentId: string, body: ChatRequest, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
    return streamSse<ChatEvent>(`/agents/${agentId}/chat`, body, signal);
  },

  // --- SRE Triage ---
  triage(
    body: { project_id: string; message: string; conversation_id?: string },
    signal?: AbortSignal,
  ): AsyncGenerator<SreEvent> {
    return streamSse<SreEvent>("/agents/sre/triage", body, signal);
  },

  answerTriage(
    conversationId: string,
    body: { answer: string; project_id?: string },
    signal?: AbortSignal,
  ): AsyncGenerator<SreEvent> {
    return streamSse<SreEvent>(
      `/agents/sre/triage/${encodeURIComponent(conversationId)}/answer`,
      body,
      signal,
    );
  },

  steerTriage(
    conversationId: string,
    body: { action: "pin" | "inject" | "kill"; hypothesis_id?: string; statement?: string },
  ) {
    return postJson(`/agents/sre/triage/${encodeURIComponent(conversationId)}/steer`, body);
  },

  verifyFix(conversationId: string, body: { project_id: string; pr_url?: string }) {
    return postJson(`/agents/sre/triage/${encodeURIComponent(conversationId)}/verify-fix`, body);
  },

  recordOutcome(
    conversationId: string,
    body: {
      project_id: string;
      classification: string;
      confidence: number;
      outcome: "confirmed" | "overturned" | "unresolved";
      outcome_source: string;
      root_cause_final?: string;
    },
  ) {
    return postJson(`/agents/sre/verdicts/${encodeURIComponent(conversationId)}/outcome`, body);
  },

  fileAdoBug(conversationId: string, body: { project_id: string; dry_run?: boolean }) {
    return postJson(`/agents/sre/verdicts/${encodeURIComponent(conversationId)}/ado-file`, body);
  },

  getCalibration(projectId: string): Promise<CalibrationStats> {
    return getJson<CalibrationStats>(`/agents/sre/calibration/${encodeURIComponent(projectId)}`);
  },

  getTriageState(conversationId: string): Promise<TriageStateResponse> {
    return getJson<TriageStateResponse>(
      `/agents/sre/triage/${encodeURIComponent(conversationId)}`,
    );
  },

  async triageCsv(projectId: string, file: File): Promise<Blob> {
    const form = new FormData();
    form.append("project_id", projectId);
    form.append("file", file);
    const res = await fetch("/agents/sre/triage-csv", { method: "POST", body: form });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.blob();
  },

  // --- Code Doc Hub v0.5 ---
  getLatestEval(projectId: string): Promise<EvalResult> {
    return getJson<EvalResult>(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/eval/latest`,
    );
  },

  runEval(projectId: string): Promise<EvalResult & { status: string }> {
    return postJson(`/agents/code_doc/projects/${encodeURIComponent(projectId)}/eval`, {});
  },

  getDigest(projectId: string): Promise<{ project_id: string; entries: DigestEntry[] }> {
    return getJson(`/agents/code_doc/projects/${encodeURIComponent(projectId)}/digest`);
  },

  getLatestRun(projectId: string): Promise<RunStatus> {
    return getJson<RunStatus>(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/runs/latest`,
    );
  },

  getTraceability(projectId: string): Promise<{ project_id: string; matrix: TraceRow[] }> {
    return getJson(`/agents/code_doc/projects/${encodeURIComponent(projectId)}/trace`);
  },

  reportWrongTraceLink(
    projectId: string,
    body: { workitem_id: string; target_kind: string; target_ref: string; method?: string },
  ) {
    return postJson(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/trace/wrong-link`,
      body,
    );
  },

  submitDocFeedback(
    projectId: string,
    docId: string,
    body: { doc_id: string; rating: number; section?: string; comment?: string },
  ) {
    return postJson(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(docId)}/feedback`,
      body,
    );
  },

  setRequirements(projectId: string, areapath: string) {
    return postJson(
      `/agents/code_doc/projects/${encodeURIComponent(projectId)}/requirements`,
      { areapath },
    );
  },
};
