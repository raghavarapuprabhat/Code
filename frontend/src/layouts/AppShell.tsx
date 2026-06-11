import { NavLink, Outlet } from "react-router-dom";
import {
  Home,
  FileCode2,
  BookOpen,
  ShieldAlert,
  LayoutDashboard,
  UserRound,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/code-doc", label: "Code Doc", icon: FileCode2 },
  { to: "/docs", label: "Documentation", icon: BookOpen },
  { to: "/sre", label: "SRE Triage", icon: ShieldAlert },
  { to: "/md", label: "MD Dashboard", icon: LayoutDashboard },
  { to: "/dev", label: "Dev Assistant", icon: UserRound },
];

export function AppShell() {
  return (
    <div className="grid h-full grid-rows-[auto_1fr]">
      <header className="flex items-center gap-3 border-b bg-surface px-5 py-3">
        <div className="h-6 w-6 rounded bg-primary" aria-hidden />
        <h1 className="text-sm font-semibold tracking-tight">AI Agent Platform</h1>
      </header>

      <div className="grid grid-cols-[220px_1fr] overflow-hidden">
        <nav className="flex flex-col gap-1 border-r bg-surface p-3">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-surface-2 font-medium text-primary"
                    : "text-muted hover:bg-surface-2 hover:text-foreground",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>

        <main className="overflow-auto bg-background">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
