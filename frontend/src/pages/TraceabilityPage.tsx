import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Filter, ThumbsDown } from "lucide-react";
import { api, type TraceRow } from "@/lib/api";
import { cn } from "@/lib/utils";

type GapView = "all" | "unimplemented" | "untraced";

/**
 * Traceability matrix screen (architecture §13B.3 v0.7). Rows = requirements, columns =
 * trace targets (components / rules / tests), cells = chips colored by status. Controls:
 * filter by work-item type / state, plus the two gap views as toggle tabs. Each component
 * chip offers a "wrong link" vote (feeds trace_eval_links, §8.9.1).
 */
export function TraceabilityPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["trace", projectId],
    queryFn: () => api.getTraceability(projectId!),
    enabled: !!projectId,
  });

  const [typeFilter, setTypeFilter] = useState<string>("");
  const [stateFilter, setStateFilter] = useState<string>("");
  const [gapView, setGapView] = useState<GapView>("all");

  const rows = q.data?.matrix ?? [];
  const types = useMemo(() => [...new Set(rows.map((r) => r.wi_type).filter(Boolean))], [rows]);
  const states = useMemo(() => [...new Set(rows.map((r) => r.state).filter(Boolean))], [rows]);

  const filtered = useMemo(
    () =>
      rows.filter((r) => {
        if (typeFilter && r.wi_type !== typeFilter) return false;
        if (stateFilter && r.state !== stateFilter) return false;
        if (gapView === "unimplemented" && r.status !== "unimplemented") return false;
        return true;
      }),
    [rows, typeFilter, stateFilter, gapView],
  );

  const reportWrong = (row: TraceRow, kind: string, ref: string) => {
    if (!projectId) return;
    api
      .reportWrongTraceLink(projectId, {
        workitem_id: row.work_item_id,
        target_kind: kind,
        target_ref: ref,
        method: "lexical",
      })
      .catch(() => {});
  };

  if (!projectId) return null;

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex items-center gap-2">
        <button
          onClick={() => navigate(`/projects/${projectId}`)}
          className="flex items-center gap-1 text-sm text-muted hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Project
        </button>
        <h1 className="text-xl font-semibold">Requirements Traceability</h1>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-surface p-3 text-sm">
        <Filter className="h-4 w-4 text-muted" />
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-xs"
        >
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-xs"
        >
          <option value="">All states</option>
          {states.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <div className="ml-auto flex overflow-hidden rounded-md border">
          {(["all", "unimplemented"] as GapView[]).map((g) => (
            <button
              key={g}
              onClick={() => setGapView(g)}
              className={cn(
                "px-2.5 py-1 text-xs",
                gapView === g ? "bg-primary text-primary-foreground" : "bg-background text-muted hover:bg-surface-2",
              )}
            >
              {g === "all" ? "All" : "Unimplemented"}
            </button>
          ))}
        </div>
      </div>

      {q.isLoading && <p className="text-sm text-muted">Loading matrix…</p>}
      {!q.isLoading && rows.length === 0 && (
        <div className="rounded-lg border border-dashed bg-surface p-10 text-center text-sm text-muted">
          No traceability data. Set an ADO requirements area path on the project to ingest
          and link work items.
        </div>
      )}

      {filtered.length > 0 && (
        <div className="overflow-x-auto rounded-lg border bg-background shadow-card">
          <table className="w-full text-sm">
            <thead className="border-b bg-surface text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-2">Work item</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Components</th>
                <th className="px-3 py-2">Rules</th>
                <th className="px-3 py-2">Tests</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map((r) => (
                <tr key={r.work_item_id} className="align-top">
                  <td className="px-3 py-2">
                    <p className="font-medium">#{r.work_item_id}</p>
                    <p className="text-xs text-muted">{r.title}</p>
                    <p className="text-[11px] text-muted">{r.wi_type} · {r.state}</p>
                  </td>
                  <td className="px-3 py-2">
                    <StatusChip status={r.status} />
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {r.components.length === 0 && <span className="text-xs text-muted">—</span>}
                      {r.components.map((c) => (
                        <Chip key={c} label={c} onWrong={() => reportWrong(r, "component", c)} />
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {r.business_rules.length === 0 && <span className="text-xs text-muted">—</span>}
                      {r.business_rules.map((rule, i) => (
                        <span key={i} className="rounded bg-surface-2 px-1.5 py-0.5 text-[11px]">
                          {rule.length > 40 ? rule.slice(0, 40) + "…" : rule}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {r.tests.length === 0 && <span className="text-xs text-muted">—</span>}
                      {r.tests.map((t) => (
                        <code key={t} className="rounded bg-surface-2 px-1.5 py-0.5 text-[11px]">{t}</code>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const tone =
    status === "implemented" ? "bg-green-100 text-green-700"
      : status === "partial" ? "bg-amber-100 text-amber-700"
      : "bg-red-100 text-red-700";
  return <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", tone)}>{status}</span>;
}

function Chip({ label, onWrong }: { label: string; onWrong: () => void }) {
  return (
    <span className="group inline-flex items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[11px] text-primary">
      {label}
      <button
        onClick={onWrong}
        title="Wrong link"
        className="opacity-0 transition-opacity group-hover:opacity-100 hover:text-red-600"
      >
        <ThumbsDown className="h-3 w-3" />
      </button>
    </span>
  );
}
