// --- Types (match backend Pydantic models) ---

export interface Company {
  ticker: string;
  name: string;
  cik: number;
}

export interface Source {
  ticker: string;
  filing_type: string;
  filing_date: string;
  section_path: string;
  relevance_rank: number;
}

export interface QueryResponse {
  session_id: string;
  question: string;
  answer: string;
  sources: Source[];
  model: string;
  chunks_retrieved: number;
}

interface CompaniesResponse {
  companies: Company[];
}

export interface QueryRequest {
  question: string;
  ticker?: string;
  filing_type?: string;
  session_id?: string;
}

// --- Fetch wrappers (call Next.js API routes, which proxy to FastAPI) ---

export async function fetchCompanies(): Promise<Company[]> {
  const res = await fetch("/api/companies");
  if (!res.ok) {
    throw new Error(`Failed to fetch companies: ${res.status}`);
  }
  const data: CompaniesResponse = await res.json();
  return data.companies;
}

export async function postQuery(req: QueryRequest): Promise<QueryResponse> {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Query failed (${res.status}): ${detail}`);
  }
  return res.json();
}
