import { Link } from "react-router-dom";
import { FileCode2, BookOpen, ShieldAlert, LayoutDashboard, UserRound } from "lucide-react";

const AGENTS = [
  { to: "/code-doc", title: "Code Documentation", desc: "Generate exhaustive docs for Java + React codebases.", icon: FileCode2 },
  { to: "/docs", title: "Documentation Hub", desc: "Browse generated docs and chat about a project.", icon: BookOpen },
  { to: "/sre", title: "SRE Triage", desc: "Triage incoming issues against generated docs.", icon: ShieldAlert },
  { to: "/md", title: "MD Dashboard", desc: "Portfolio view across squads.", icon: LayoutDashboard },
  { to: "/dev", title: "Dev Assistant", desc: "Daily workitem status and updates.", icon: UserRound },
];

export function HomePage() {
  return (
    <div className="p-8">
      <h2 className="text-xl font-semibold tracking-tight">Choose an agent</h2>
      <p className="mt-1 text-sm text-muted">Five specialized agents, one platform.</p>

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {AGENTS.map(({ to, title, desc, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="group rounded-lg border bg-surface p-5 shadow-card transition-colors hover:border-primary"
          >
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-md bg-surface-2 text-primary">
                <Icon className="h-5 w-5" />
              </span>
              <h3 className="font-medium">{title}</h3>
            </div>
            <p className="mt-3 text-sm text-muted">{desc}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
