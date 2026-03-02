import type { Source } from "@/lib/api";

interface SourcesPanelProps {
  sources: Source[];
}

export default function SourcesPanel({ sources }: SourcesPanelProps) {
  if (sources.length === 0) return null;

  return (
    <details style={{ marginTop: "4px", fontSize: "11px" }}>
      <summary style={{
        cursor: "pointer",
        color: "#000080",
        textDecoration: "underline",
        fontWeight: "bold",
      }}>
        Sources ({sources.length})
      </summary>
      <div className="win95-sunken" style={{
        background: "#ffffff",
        padding: "4px",
        marginTop: "2px",
        fontSize: "11px",
      }}>
        {sources.map((s) => (
          <div key={s.relevance_rank} style={{ padding: "1px 0" }}>
            <span style={{ fontFamily: "monospace", color: "#808080" }}>
              [{s.relevance_rank}]
            </span>{" "}
            <span style={{ fontWeight: "bold" }}>{s.ticker}</span>
            {" - "}
            {s.filing_type} ({s.filing_date})
            {s.section_path && (
              <div style={{
                marginLeft: "20px",
                color: "#808080",
                fontSize: "10px",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}>
                {s.section_path}
              </div>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
