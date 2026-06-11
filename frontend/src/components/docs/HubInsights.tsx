import { useState } from "react";
import { Activity, GitCompareArrows, RefreshCw } from "lucide-react";
import { useDigest, useLatestEval, useRunEval } from "@/hooks/useHubInsights";
import { MarkdownView } from "@/components/chat/MarkdownView";
import { cn } from "@/lib/utils";

interface Props {
  projectId?: string;
}

/**
 * Hub insights strip (architecture §8.9): the doc-eval quality badge (§8.9.3) and a
 * collapsible "What changed" architecture drift digest (§8.9.4). Both degrade silently
 * when the project has no eval run or no digest entries yet.
 */
export function HubInsights({ projectId }: Props) {
  const evalQ = useLatestEval(projectId);
  const digestQ = useDigest(projectId);
  const runEval = useRunEval(projectId);
  const [showDigest, setShowDigest] = useState(false);

  if (!projectId) return null;

  const score = evalQ.data?.score;
  const entries = digestQ.data?.entries ?? [];

  return (
    <div className="border-b bg-surface px-4 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <EvalBadge score={score ?? null} loading={evalQ.isLoading} />
        <button
          onClick={() => runEval.mutate()}
          disabled={runEval.isPending}
          className="flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted hover:bg-surface-2 disabled:opacity-40"
          title="Re-run golden-Q&A eval"
        >
          <RefreshCw className={cn("h-3 w-3", runEval.isPending && "animate-spin")} />
          {runEval.isPending ? "Evaluating…" : "Re-eval"}
        </button>

        {entries.length > 0 && (
          <button
            onClick={() => setShowDigest((v) => !v)}
            className="flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted hover:bg-surface-2"
          >
            <GitCompareArrows className="h-3 w-3" />
            What changed ({entries.length})
          </button>
        )}
      </div>

      {showDigest && entries.length > 0 && (
        <div className="mt-2 max-h-72 space-y-3 overflow-y-auto rounded-md border bg-background p-3">
          {entries.map((e, i) => (
            <div key={i}>
              <p className="text-xs font-medium text-muted">{e.period}</p>
              <div className="text-sm">
                <MarkdownView markdown={e.digest_md} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EvalBadge({ score, loading }: { score: number | null; loading: boolean }) {
  if (loading) {
    return <span className="text-xs text-muted">Loading eval…</span>;
  }
  if (score === null || score === undefined) {
    return (
      <span className="flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">
        <Activity className="h-3 w-3" /> No eval yet
      </span>
    );
  }
  const pct = Math.round(score * 100);
  const tone =
    pct >= 80 ? "bg-green-100 text-green-700" : pct >= 50 ? "bg-amber-100 text-amber-700" : "bg-red-100 text-red-700";
  return (
    <span className={cn("flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", tone)}>
      <Activity className="h-3 w-3" /> Doc quality {pct}%
    </span>
  );
}
