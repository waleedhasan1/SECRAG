"use client";

import { useEffect, useRef, useState } from "react";
import { fetchCompanies, postQuery } from "@/lib/api";
import type { Company } from "@/lib/api";
import ChatMessage from "@/components/ChatMessage";
import type { Message } from "@/components/ChatMessage";

const FILING_TYPES = ["All", "10-K", "10-Q"] as const;

export default function Home() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedTicker, setSelectedTicker] = useState("");
  const [filingType, setFilingType] = useState("All");
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchCompanies()
      .then(setCompanies)
      .catch((err) => setError(`Failed to load companies: ${err.message}`));
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  function handleNewChat() {
    setMessages([]);
    setSessionId(null);
    setError(null);
    setInput("");
    inputRef.current?.focus();
  }

  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;

    setError(null);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setLoading(true);

    try {
      const resp = await postQuery({
        question,
        ticker: selectedTicker || undefined,
        filing_type: filingType === "All" ? undefined : filingType,
        session_id: sessionId ?? undefined,
      });

      setSessionId(resp.session_id);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: resp.answer, sources: resp.sources },
      ]);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      if (msg.includes("404")) {
        setSessionId(null);
        setError("Session expired. Starting a new conversation.");
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="flex h-screen items-center justify-center p-4"
         style={{ background: "#008080" }}>
      {/* Window */}
      <div className="win95-raised flex h-full w-full max-w-4xl flex-col"
           style={{ background: "#c0c0c0", maxHeight: "95vh" }}>

        {/* Title bar */}
        <div className="win95-titlebar">
          <div className="flex items-center gap-2">
            <span style={{ fontSize: "14px" }}>SEC Filing Assistant</span>
          </div>
          <div className="flex gap-1">
            <button className="win95-btn" style={{ padding: "0 4px", minHeight: "16px", fontSize: "10px", lineHeight: "10px" }}
                    onClick={handleNewChat}
                    title="New Chat">
              New
            </button>
          </div>
        </div>

        {/* Menu bar / Filters */}
        <div className="flex items-center gap-4 px-2 py-1"
             style={{ borderBottom: "1px solid #808080" }}>
          <label className="flex items-center gap-1" style={{ fontSize: "12px" }}>
            Company:
            <select
              value={selectedTicker}
              onChange={(e) => setSelectedTicker(e.target.value)}
              className="win95-select"
            >
              <option value="">All Companies</option>
              {companies.map((c) => (
                <option key={c.ticker} value={c.ticker}>
                  {c.ticker} - {c.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-1" style={{ fontSize: "12px" }}>
            Filing:
            <select
              value={filingType}
              onChange={(e) => setFilingType(e.target.value)}
              className="win95-select"
            >
              {FILING_TYPES.map((ft) => (
                <option key={ft} value={ft}>
                  {ft}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* Chat area */}
        <div className="win95-sunken mx-2 mt-2 flex-1 overflow-y-auto p-2 scrollbar-hide"
             style={{ background: "#ffffff" }}>
          {messages.length === 0 && !loading && (
            <div className="flex h-full flex-col items-center justify-center gap-4">
              <p style={{ color: "#808080", fontSize: "12px" }}>
                Select a company and ask a question about their SEC filings.
              </p>
              <p style={{ color: "#a0a0a0", fontSize: "11px" }}>
                Data: latest 10-K and 10-Q filings from SEC EDGAR (2025-2026)
              </p>
              <div className="grid max-w-lg gap-2 sm:grid-cols-2">
                {[
                  "What are JPMorgan's biggest risk factors?",
                  "Compare Goldman Sachs and Morgan Stanley revenue",
                  "What did Bank of America report for net interest income?",
                  "Summarize Northern Trust's latest 10-K filing",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => {
                      setInput(q);
                      inputRef.current?.focus();
                    }}
                    className="win95-btn text-left"
                    style={{ fontSize: "11px", padding: "6px 8px" }}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-2">
            {messages.map((msg, i) => (
              <ChatMessage key={i} message={msg} />
            ))}

            {loading && (
              <div style={{ fontSize: "12px", color: "#808080", padding: "4px" }}>
                Querying database...
              </div>
            )}

            <div ref={chatEndRef} />
          </div>
        </div>

        {/* Error bar */}
        {error && (
          <div className="mx-2 mt-1 win95-sunken px-2 py-1"
               style={{ background: "#ffffe0", fontSize: "11px", color: "#800000" }}>
            {error}
          </div>
        )}

        {/* Status bar / Input */}
        <div className="flex items-center gap-1 p-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about SEC filings..."
            disabled={loading}
            className="win95-sunken flex-1 px-2 py-1 disabled:opacity-50"
            style={{ background: "#ffffff", fontSize: "12px", outline: "none" }}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="win95-btn"
          >
            Send
          </button>
        </div>

        {/* Status bar */}
        <div className="win95-sunken mx-2 mb-2 px-2"
             style={{ fontSize: "11px", color: "#808080", minHeight: "18px", lineHeight: "18px" }}>
          {loading ? "Retrieving data from vector store..." : "Ready"}
        </div>
      </div>
    </div>
  );
}
