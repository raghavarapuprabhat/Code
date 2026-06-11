import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Bug,
  CheckCircle2,
  CircleHelp,
  FlaskConical,
  HelpCircle,
  Pin,
  Plus,
  Search,
  Send,
  Skull,
  Upload,
} from "lucide-react";
import { useProjects } from "@/hooks/useDocs";
import { useSreTriage } from "@/hooks/useSreTriage";
import { MarkdownView } from "@/components/chat/MarkdownView";
import { api, type SreHypothesis, type SreVerdict } from "@/lib/api";
import { cn } from "@/lib/utils";

type Tab = "single" | "batch";

export function SrePage() {
  const projectsQ = useProjects();
  const projects = projectsQ.data ?? [];
  const [projectId, setProjectId] = useState<string>("");
  const [tab, setTab] = useState<Tab>("single");

  // Default to the first indexed project once loaded.
  const effectiveProject = projectId || projects[0]?.id || "";

  return (
    <div className="mx-auto max-w-6xl p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">SRE Triage Agent</h1>
          <p className="mt-1 text-sm text-muted">
            Agentic root-cause investigation over the indexed codebase — hypotheses, evidence,
            read-only probes and a cited verdict.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted">Project</label>
          <select
            value={effectiveProject}
            onChange={(e) => setProjectId(e.target.value)}
            className="rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus:border-primary"
          >
            {projects.length === 0 && <option value="">No projects indexed</option>}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name || p.id}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex gap-1 border-b">
        <TabButton active={tab === "single"} onClick={() => setTab("single")}>
          <Search className="h-3.5 w-3.5" /> Single issue
        </TabButton>
        <TabButton active={tab === "batch"} onClick={() => setTab("batch")}>
          <Upload className="h-3.5 w-3.5" /> CSV batch
        </TabButton>
      </div>

      {tab === "single" ? (
        <SingleTriage projectId={effectiveProject} />
      ) : (
        <BatchTriage projectId={effectiveProject} />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors -mb-px",
        active
          ? "border-primary font-medium text-foreground"
          : "border-transparent text-muted hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Single-issue triage
// ---------------------------------------------------------------------------

function SingleTriage({ projectId }: { projectId: string }) {
  const { state, start, answer, steer, reset } = useSreTriage();
  const [issue, setIssue] = useState("");
  const [answerText, setAnswerText] = useState("");
  const [injectText, setInjectText] = useState("");

  const canSubmit = !!projectId && !!issue.trim() && !state.loading;

  const submit = () => {
    if (!canSubmit) return;
    start(projectId, issue.trim());
  };

  const sortedHyps = useMemo(
    () => [...state.hypotheses].sort((a, b) => (b.posterior ?? 0) - (a.posterior ?? 0)),
    [state.hypotheses],
  );

  const started = state.loading || state.steps.length > 0 || !!state.verdict || !!state.question;

  // Conversation lifecycle chip (§13B.4 v0.7): running while streaming, paused when a
  // question is open, concluded once a verdict lands.
  const convState: "running" | "paused" | "concluded" | undefined = state.loading
    ? "running"
    : state.question
      ? "paused"
      : state.verdict
        ? "concluded"
        : undefined;

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {/* Left: issue input + verdict */}
      <div className="space-y-5">
        <div className="rounded-lg border bg-background p-4 shadow-card space-y-3">
          <label className="text-sm font-medium">Describe the issue</label>
          <textarea
            value={issue}
            onChange={(e) => setIssue(e.target.value)}
            rows={6}
            placeholder="Paste a stack trace, error message, or describe the bug. Include the environment if known."
            className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
          <div className="flex items-center justify-between">
            <button
              onClick={reset}
              disabled={state.loading}
              className="text-xs text-muted hover:text-foreground disabled:opacity-40"
            >
              Clear
            </button>
            <button
              onClick={submit}
              disabled={!canSubmit}
              className="flex items-center gap-1.5 rounded-md bg-primary px-3.5 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            >
              <Search className="h-4 w-4" />
              {state.loading ? "Investigating…" : "Triage"}
            </button>
          </div>
          {!projectId && (
            <p className="text-xs text-amber-600">Index a project first to enable triage.</p>
          )}
        </div>

        {state.error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {state.error}
          </div>
        )}

        {/* Mid-loop question (interrupt) */}
        {state.question && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 space-y-3">
            <p className="flex items-center gap-1.5 text-sm font-medium text-amber-800">
              <HelpCircle className="h-4 w-4" /> The agent needs your input
            </p>
            <p className="text-sm text-amber-900">{state.question.text}</p>
            {state.question.options && state.question.options.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {state.question.options.map((opt) => (
                  <button
                    key={opt}
                    onClick={() => answer(projectId, opt)}
                    className="rounded-md border border-amber-400 bg-background px-3 py-1.5 text-sm hover:bg-amber-100"
                  >
                    {opt}
                  </button>
                ))}
              </div>
            ) : (
              <div className="flex items-end gap-2">
                <textarea
                  value={answerText}
                  onChange={(e) => setAnswerText(e.target.value)}
                  rows={2}
                  className="flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
                  placeholder="Type your answer…"
                />
                <button
                  onClick={() => {
                    answer(projectId, answerText);
                    setAnswerText("");
                  }}
                  disabled={!answerText.trim()}
                  className="flex h-9 w-9 items-center justify-center rounded-md bg-primary text-primary-foreground disabled:opacity-40"
                >
                  <Send className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>
        )}

        {convState && <ConversationChip state={convState} />}

        {state.verdict && <VerdictCard verdict={state.verdict} severity={state.severity} />}

        {state.handoff && (
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
            <p className="flex items-center gap-1.5 font-medium">
              <FlaskConical className="h-4 w-4" /> Handed off to the SRE Fixer Agent
            </p>
            <p className="mt-1 text-xs">
              A failing repro test and bug packet were forwarded for an automated fix attempt.
            </p>
            {state.conversationId && <VerifyFixButton conversationId={state.conversationId} projectId={projectId} />}
          </div>
        )}
      </div>

      {/* Right: live investigation */}
      <div className="space-y-5">
        {!started && (
          <div className="rounded-lg border border-dashed bg-surface p-10 text-center text-sm text-muted">
            The hypothesis board, evidence ledger and probe log will stream here as the agent
            investigates.
          </div>
        )}

        {state.hypotheses.length > 0 && (
          <HypothesisBoard
            hypotheses={sortedHyps}
            onSteer={steer}
            injectText={injectText}
            setInjectText={setInjectText}
            disabled={!state.conversationId}
          />
        )}

        {state.steps.length > 0 && <Scratchpad steps={state.steps} />}
        {state.evidence.length > 0 && <EvidenceLedger evidence={state.evidence} />}
        {state.probes.length > 0 && <ProbeLog probes={state.probes} />}
      </div>
    </div>
  );
}

function ConversationChip({ state }: { state: "running" | "paused" | "concluded" | "expired" }) {
  const meta = {
    running: { label: "Running", cls: "bg-blue-100 text-blue-700" },
    paused: { label: "Paused — awaiting answer", cls: "bg-amber-100 text-amber-700" },
    concluded: { label: "Concluded", cls: "bg-green-100 text-green-700" },
    expired: { label: "Expired", cls: "bg-surface-2 text-muted" },
  }[state];
  return (
    <span className={cn("inline-block rounded-full px-2 py-0.5 text-xs font-medium", meta.cls)}>
      {meta.label}
    </span>
  );
}

function VerifyFixButton({ conversationId, projectId }: { conversationId: string; projectId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>();
  const run = async () => {
    setBusy(true);
    try {
      const r = (await api.verifyFix(conversationId, { project_id: projectId })) as { message?: string };
      setResult(r.message ?? "Verification triggered.");
    } catch (e) {
      setResult(`Error: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2">
      <button
        onClick={run}
        disabled={busy}
        className="rounded-md border border-blue-300 bg-background px-2.5 py-1 text-xs text-blue-700 hover:bg-blue-100 disabled:opacity-40"
      >
        {busy ? "Verifying…" : "Verify fix now"}
      </button>
      {result && <p className="mt-1 text-xs text-blue-800">{result}</p>}
    </div>
  );
}

function VerdictCard({
  verdict,
  severity,
}: {
  verdict: SreVerdict;
  severity?: { level?: string; blast_radius?: string };
}) {
  const cls = verdict.classification;
  const meta = verdictMeta(cls);
  return (
    <div className="rounded-lg border bg-background p-4 shadow-card space-y-3">
      <div className="flex items-center justify-between">
        <span className={cn("flex items-center gap-1.5 text-sm font-semibold", meta.color)}>
          {meta.icon}
          {meta.label}
        </span>
        {typeof verdict.confidence === "number" && (
          <span className="text-xs text-muted">
            {Math.round(verdict.confidence * 100)}% confidence
          </span>
        )}
      </div>

      {severity?.level && (
        <span
          className={cn(
            "inline-block rounded-full px-2 py-0.5 text-xs font-medium",
            severityColor(severity.level),
          )}
        >
          severity: {severity.level}
        </span>
      )}

      {verdict.root_cause && (
        <div>
          <p className="text-xs font-medium text-muted">Root cause</p>
          <p className="text-sm">{verdict.root_cause}</p>
        </div>
      )}
      {severity?.blast_radius && (
        <p className="text-xs text-muted">{severity.blast_radius}</p>
      )}
      {verdict.rationale && (
        <div className="text-sm text-muted">
          <MarkdownView markdown={verdict.rationale} />
        </div>
      )}
      {verdict.citations && verdict.citations.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {verdict.citations.slice(0, 10).map((c, i) => (
            <code key={i} className="rounded bg-surface-2 px-1.5 py-0.5 text-xs">
              {c}
            </code>
          ))}
        </div>
      )}
      {verdict.questions && verdict.questions.length > 0 && (
        <div className="text-sm">
          <p className="text-xs font-medium text-muted">Follow-up questions</p>
          <ul className="ml-4 list-disc">
            {verdict.questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function HypothesisBoard({
  hypotheses,
  onSteer,
  injectText,
  setInjectText,
  disabled,
}: {
  hypotheses: SreHypothesis[];
  onSteer: (a: "pin" | "inject" | "kill", id?: string, statement?: string) => void;
  injectText: string;
  setInjectText: (s: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="rounded-lg border bg-background shadow-card">
      <div className="border-b bg-surface px-4 py-2.5 text-sm font-semibold">Hypothesis board</div>
      <div className="divide-y">
        {hypotheses.map((h) => (
          <div key={h.id} className="flex items-center gap-3 px-4 py-2.5">
            <div className="w-12 shrink-0">
              <PosteriorBar value={h.posterior ?? 0} />
            </div>
            <div className="min-w-0 flex-1">
              <p
                className={cn(
                  "truncate text-sm",
                  h.status === "refuted" && "text-muted line-through",
                )}
              >
                {h.pinned && <Pin className="mr-1 inline h-3 w-3 text-primary" />}
                {h.statement}
              </p>
              <p className="text-[11px] text-muted">
                {h.id} · {h.status ?? "open"}
                {h.source === "user" && " · yours"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <SteerBtn title="Pin" onClick={() => onSteer("pin", h.id)} disabled={disabled}>
                <Pin className="h-3.5 w-3.5" />
              </SteerBtn>
              <SteerBtn title="Kill" onClick={() => onSteer("kill", h.id)} disabled={disabled}>
                <Skull className="h-3.5 w-3.5" />
              </SteerBtn>
            </div>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 border-t p-3">
        <input
          value={injectText}
          onChange={(e) => setInjectText(e.target.value)}
          placeholder="Inject your own hypothesis…"
          disabled={disabled}
          className="flex-1 rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus:border-primary disabled:opacity-50"
        />
        <button
          onClick={() => {
            if (injectText.trim()) {
              onSteer("inject", undefined, injectText.trim());
              setInjectText("");
            }
          }}
          disabled={disabled || !injectText.trim()}
          className="flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs hover:bg-surface-2 disabled:opacity-40"
        >
          <Plus className="h-3.5 w-3.5" /> Add
        </button>
      </div>
    </div>
  );
}

function SteerBtn({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="flex h-7 w-7 items-center justify-center rounded-md border text-muted hover:bg-surface-2 hover:text-foreground disabled:opacity-30"
    >
      {children}
    </button>
  );
}

function PosteriorBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="flex items-center gap-1">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-6 text-right text-[10px] tabular-nums text-muted">{pct}</span>
    </div>
  );
}

function Scratchpad({ steps }: { steps: Array<{ n: number; thought?: string; action?: string; observation?: string }> }) {
  return (
    <details open className="rounded-lg border bg-background shadow-card">
      <summary className="cursor-pointer border-b bg-surface px-4 py-2.5 text-sm font-semibold">
        Investigation trace ({steps.length})
      </summary>
      <div className="max-h-64 space-y-2 overflow-y-auto p-4 text-xs">
        {steps.map((s) => (
          <div key={s.n} className="space-y-0.5">
            <p className="font-medium">
              Step {s.n}
              {s.action ? ` · ${s.action}` : ""}
            </p>
            {s.thought && <p className="text-muted">{s.thought}</p>}
            {s.observation && (
              <p className="rounded bg-surface-2 px-2 py-1 font-mono text-[11px] text-muted">
                {s.observation.slice(0, 400)}
              </p>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

function EvidenceLedger({
  evidence,
}: {
  evidence: Array<{ id: string; source: string; citation: string; finding: string }>;
}) {
  return (
    <details className="rounded-lg border bg-background shadow-card">
      <summary className="cursor-pointer border-b bg-surface px-4 py-2.5 text-sm font-semibold">
        Evidence ledger ({evidence.length})
      </summary>
      <div className="divide-y">
        {evidence.map((e) => (
          <div key={e.id} className="px-4 py-2 text-xs">
            <p className="flex items-center gap-1.5">
              <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] uppercase">
                {e.source}
              </span>
              <code className="text-muted">{e.citation}</code>
            </p>
            <p className="mt-0.5">{e.finding}</p>
          </div>
        ))}
      </div>
    </details>
  );
}

function ProbeLog({ probes }: { probes: Array<{ tool: string; target?: string; environment?: string; summary?: string }> }) {
  return (
    <details className="rounded-lg border bg-background shadow-card">
      <summary className="cursor-pointer border-b bg-surface px-4 py-2.5 text-sm font-semibold">
        Read-only probes ({probes.length})
      </summary>
      <div className="divide-y">
        {probes.map((p, i) => (
          <div key={i} className="px-4 py-2 text-xs">
            <p className="font-medium">
              {p.tool}
              {p.target ? ` → ${p.target}` : ""}
              {p.environment ? ` (${p.environment})` : ""}
            </p>
            {p.summary && <p className="mt-0.5 text-muted">{p.summary}</p>}
          </div>
        ))}
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// CSV batch triage
// ---------------------------------------------------------------------------

function BatchTriage({ projectId }: { projectId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [done, setDone] = useState(false);

  const run = async () => {
    if (!file || !projectId) return;
    setBusy(true);
    setError(undefined);
    setDone(false);
    try {
      const blob = await api.triageCsv(projectId, file);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "triaged.csv";
      a.click();
      URL.revokeObjectURL(url);
      setDone(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-xl space-y-4 rounded-lg border bg-background p-5 shadow-card">
      <div>
        <h2 className="text-sm font-medium">Batch triage a CSV of issues</h2>
        <p className="mt-1 text-xs text-muted">
          Upload a CSV (columns: id, title, description, stack_trace, environment). The agent
          clusters them by signature, investigates one representative per cluster, and returns a
          triaged CSV with verdicts, root causes and cluster ids.
        </p>
      </div>

      <label className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed bg-surface px-3 py-4 text-sm text-muted hover:bg-surface-2">
        <Upload className="h-4 w-4" />
        {file ? file.name : "Choose a CSV file…"}
        <input
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </label>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {done && (
        <p className="flex items-center gap-1.5 text-sm text-green-700">
          <CheckCircle2 className="h-4 w-4" /> Triaged CSV downloaded.
        </p>
      )}

      <button
        onClick={run}
        disabled={!file || !projectId || busy}
        className="flex items-center gap-1.5 rounded-md bg-primary px-3.5 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
      >
        <Upload className="h-4 w-4" />
        {busy ? "Triaging…" : "Run batch triage"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function verdictMeta(cls: string): { label: string; color: string; icon: React.ReactNode } {
  switch (cls) {
    case "bug":
      return { label: "Bug confirmed", color: "text-red-600", icon: <Bug className="h-4 w-4" /> };
    case "not_a_bug":
      return {
        label: "Not a bug",
        color: "text-green-700",
        icon: <CheckCircle2 className="h-4 w-4" />,
      };
    case "external":
      return {
        label: "External cause",
        color: "text-amber-600",
        icon: <AlertTriangle className="h-4 w-4" />,
      };
    default:
      return {
        label: "Needs more info",
        color: "text-muted",
        icon: <CircleHelp className="h-4 w-4" />,
      };
  }
}

function severityColor(level: string): string {
  switch (level) {
    case "critical":
      return "bg-red-100 text-red-700";
    case "high":
      return "bg-orange-100 text-orange-700";
    case "medium":
      return "bg-amber-100 text-amber-700";
    default:
      return "bg-surface-2 text-muted";
  }
}
