import type { Source } from "@/lib/api";

interface SourcesPanelProps {
  sources: Source[];
}

export default function SourcesPanel({ sources }: SourcesPanelProps) {
  if (sources.length === 0) return null;

  return (
    <details className="mt-2 rounded-lg border border-gray-700 bg-gray-900 text-sm">
      <summary className="cursor-pointer px-3 py-2 font-medium text-gray-400 hover:text-gray-200 select-none">
        Sources ({sources.length})
      </summary>
      <ol className="list-none space-y-1 px-3 pb-3 pt-1">
        {sources.map((s) => (
          <li key={s.relevance_rank} className="text-gray-400">
            <span className="font-mono text-xs text-gray-500">
              [{s.relevance_rank}]
            </span>{" "}
            <span className="font-semibold text-gray-300">{s.ticker}</span>
            {" — "}
            {s.filing_type} ({s.filing_date})
            {s.section_path && (
              <span className="block ml-6 text-xs text-gray-500 truncate">
                {s.section_path}
              </span>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}
