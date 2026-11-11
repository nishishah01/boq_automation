import { useWork } from "../context/WorkContext";
import { engineName, PageHeader, Spinner } from "../components/ui";

export default function SettingsPage() {
  const { options } = useWork();
  if (!options) return <Spinner />;
  const groups: [string, [string, string][]][] = [
    ["Matching", [
      ["Engine", options.ai_mode !== "mock" ? `${engineName(options.ai_mode)} (${options.model})`
        : "Rule-based. Set ANTHROPIC_API_KEY, or AI_PROVIDER=openai or gemini with that provider's key, in backend/.env"],
      ["Search", options.embedding_backend === "lsa" ? "Offline embeddings over FAISS" : "Sentence Transformers over FAISS"],
      ["Default confidence threshold", `${options.defaults.confidence_threshold}%`],
      ["Alternatives kept per item", String(options.defaults.max_candidates)],
    ]],
    ["Scope", [["Cost Sheet Types available", options.cost_sheet_types.map((t) => t.name).join(", ")]]],
    ["System", [["Database", options.database === "postgresql" ? "PostgreSQL" : "SQLite (development)"]]],
  ];
  return (
    <>
      <PageHeader title="Settings" subtitle="Server configuration. Change these in backend/.env and restart the backend." />
      <div className="space-y-5">
        {groups.map(([title, rows]) => (
          <section key={title} className="card max-w-3xl">
            <h2 className="border-b border-slate-100 px-6 py-3.5 text-sm font-semibold">{title}</h2>
            <dl className="divide-y divide-slate-100">
              {rows.map(([k, v]) => (
                <div key={k} className="grid gap-1 px-6 py-3.5 text-sm sm:grid-cols-[240px_1fr]">
                  <dt className="text-slate-500">{k}</dt><dd className="text-slate-900">{v}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </>
  );
}
