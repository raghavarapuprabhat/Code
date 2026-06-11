interface Props {
  title: string;
  description: string;
}

/** Temporary scaffold content for routes not yet migrated (F0). */
export function PagePlaceholder({ title, description }: Props) {
  return (
    <div className="p-8">
      <div className="rounded-lg border bg-surface p-8 shadow-card">
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="mt-2 max-w-2xl text-sm text-muted">{description}</p>
        <p className="mt-4 text-xs text-muted">
          Scaffolded shell (Phase F0). Functionality lands in later phases.
        </p>
      </div>
    </div>
  );
}
