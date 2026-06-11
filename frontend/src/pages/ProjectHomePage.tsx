import { useNavigate, useParams } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock,
  FileWarning,
  GitCompareArrows,
  ListChecks,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { useDigest, useLatestEval, useLatestRun } from "@/hooks/useHubInsights";
import { MarkdownView } from "@/components/chat/MarkdownView";
import { cn } from "@/lib/utils";

/**
 * Project landing page (architecture §13B.1 v0.7). The operational home for an indexed
 * project: run-status strip, four clickable metric cards, the "What changed" digest, and
 * a "needs attention" action queue. Replaces dropping straight into the doc tree.
 */
export function ProjectHomePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const runQ = useLatestRun(projectId);
  const evalQ = useLatestEval(projectId);
  const digestQ = useDigest(projectId);

  if (!projectId) return null;

  const run = runQ.data;
  const entries = digestQ.data?.entries ?? [];
  const evalScore = evalQ.data?.score;

  const openDocs = () => navigate(`/docs/${projectId}`);
  const openDoc = (docId: string) => navigate(`/docs/${projectId}/${docId}`);

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Project overview</h1>
        <button
          onClick={openDocs}
          className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-foreground"
        >
          <BookOpen className="h-4 w-4" /> Open documentation
        </button>
      </div>

      <RunStrip run={run} loading={runQ.isLoading} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          icon={<Activity className="h-4 w-4" />}
          label="Doc quality"
          value={evalScore != null ? `${Math.round(evalScore * 100)}%` : "—"}
          tone={evalScore != null ? (evalScore >= 0.8 ? "good" : evalScore >= 0.5 ? "warn" : "bad") : "neutral"}
          onClick={() => openDoc("14_onboarding")}
        />
        <MetricCard
          icon={<ListChecks className="h-4 w-4" />}
          label="Requirement coverage"
          value={run?.run ? "view" : "—"}
          tone="neutral"
          onClick={() => navigate(`/docs/${projectId}/trace`)}
        />
        <MetricCard
          icon={<ShieldAlert className="h-4 w-4" />}
          label="Dependencies / CVEs"
          value="view"
          tone="neutral"
          onClick={() => openDoc("13_dependencies")}
        />
        <MetricCard
          icon={<FileWarning className="h-4 w-4" />}
          label="Rules tested"
          value="view"
          tone="neutral"
          onClick={() => openDoc("06_business_logic")}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* What changed */}
        <div className="rounded-lg border bg-background shadow-card">
          <div className="flex items-center justify-between border-b bg-surface px-4 py-2.5">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold">
              <GitCompareArrows className="h-4 w-4" /> What changed
            </h2>
            <button
              onClick={() => openDoc("16_change_digest")}
              className="text-xs text-primary hover:underline"
            >
              Full digest →
            </button>
          </div>
          <div className="max-h-72 space-y-3 overflow-y-auto p-4">
            {entries.length === 0 ? (
              <p className="text-sm text-muted">No changes recorded yet.</p>
            ) : (
              entries.slice(0, 3).map((e, i) => (
                <div key={i}>
                  <p className="text-xs font-medium text-muted">{e.period}</p>
                  <div className="text-sm">
                    <MarkdownView markdown={e.digest_md} />
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Needs attention */}
        <NeedsAttention run={run} onOpenDoc={openDoc} projectId={projectId} navigate={navigate} />
      </div>
    </div>
  );
}

function RunStrip({ run, loading }: { run?: import("@/lib/api").RunStatus; loading: boolean }) {
  if (loading) return <div className="rounded-lg border bg-surface p-4 text-sm text-muted">Loading run status…</div>;
  const status = run?.status ?? "never";
  const r = run?.run;
  const tone =
    status === "error" ? "border-red-300 bg-red-50" : status === "never" ? "border-amber-300 bg-amber-50" : "border bg-surface";
  const icon =
    status === "error" ? <XCircle className="h-4 w-4 text-red-600" />
      : status === "never" ? <AlertTriangle className="h-4 w-4 text-amber-600" />
      : <CheckCircle2 className="h-4 w-4 text-green-600" />;

  return (
    <div className={cn("flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border p-3 text-sm", tone)}>
      <span className="flex items-center gap-1.5 font-medium">
        {icon}
        {status === "never" ? "Never indexed" : status === "error" ? "Last run had errors" : "Indexed"}
      </span>
      {run?.last_indexed && (
        <span className="flex items-center gap-1 text-muted">
          <Clock className="h-3.5 w-3.5" /> {new Date(run.last_indexed).toLocaleString()}
        </span>
      )}
      {r && (
        <>
          <span className="text-muted">{r.files_indexed} files · {r.summaries} summaries</span>
          <span className="text-muted">{r.duration_ms ? `${Math.round(r.duration_ms / 1000)}s` : ""}</span>
          {r.gap_count > 0 && <span className="text-amber-700">{r.gap_count} coverage gaps</span>}
          {r.error_count > 0 && <span className="text-red-700">{r.error_count} errors</span>}
          <span className="text-muted">mode: {r.mode}</span>
        </>
      )}
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
  tone,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: "good" | "warn" | "bad" | "neutral";
  onClick: () => void;
}) {
  const toneCls = {
    good: "text-green-700",
    warn: "text-amber-700",
    bad: "text-red-700",
    neutral: "text-foreground",
  }[tone];
  return (
    <button
      onClick={onClick}
      className="flex flex-col items-start gap-1 rounded-lg border bg-background p-3 text-left shadow-card hover:border-primary transition-colors"
    >
      <span className="flex items-center gap-1.5 text-xs text-muted">
        {icon}
        {label}
      </span>
      <span className={cn("text-lg font-semibold", toneCls)}>{value}</span>
    </button>
  );
}

function NeedsAttention({
  run,
  onOpenDoc,
  projectId,
  navigate,
}: {
  run?: import("@/lib/api").RunStatus;
  onOpenDoc: (d: string) => void;
  projectId: string;
  navigate: (to: string) => void;
}) {
  const items: Array<{ label: string; action: () => void }> = [];
  if (run?.run?.gap_count) {
    items.push({
      label: `${run.run.gap_count} files with coverage gaps`,
      action: () => onOpenDoc("11_quality_hotspots"),
    });
  }
  if (run?.run?.error_count) {
    items.push({
      label: `${run.run.error_count} errors in the last index run`,
      action: () => onOpenDoc("11_quality_hotspots"),
    });
  }
  items.push({
    label: "Review unimplemented requirements",
    action: () => navigate(`/docs/${projectId}/trace`),
  });

  return (
    <div className="rounded-lg border bg-background shadow-card">
      <div className="border-b bg-surface px-4 py-2.5">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold">
          <AlertTriangle className="h-4 w-4" /> Needs attention
        </h2>
      </div>
      <div className="divide-y">
        {items.length === 0 ? (
          <p className="p-4 text-sm text-muted">Nothing needs attention.</p>
        ) : (
          items.map((it, i) => (
            <button
              key={i}
              onClick={it.action}
              className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm hover:bg-surface-2"
            >
              <span>{it.label}</span>
              <span className="text-xs text-primary">Open →</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
