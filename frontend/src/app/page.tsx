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

  // Load companies on mount
  useEffect(() => {
    fetchCompanies()
      .then(setCompanies)
      .catch((err) => setError(`Failed to load companies: ${err.message}`));
  }, []);

  // Auto-scroll to bottom on new messages
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
      // Auto-clear expired session
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
    <div className="flex h-screen flex-col bg-white">
      {/* Header */}
      <header className="flex items-center justify-between border-b px-6 py-3">
        <h1 className="text-lg font-semibold text-gray-900">
          SEC Filing Assistant
        </h1>
        <button
          onClick={handleNewChat}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          New Chat
        </button>
      </header>

      {/* Filters row */}
      <div className="flex items-center gap-4 border-b px-6 py-2">
        <label className="flex items-center gap-2 text-sm text-gray-600">
          Company
          <select
            value={selectedTicker}
            onChange={(e) => setSelectedTicker(e.target.value)}
            className="rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900"
          >
            <option value="">All Companies</option>
            {companies.map((c) => (
              <option key={c.ticker} value={c.ticker}>
                {c.ticker} — {c.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm text-gray-600">
          Filing
          <select
            value={filingType}
            onChange={(e) => setFilingType(e.target.value)}
            className="rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900"
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
      <div className="flex-1 overflow-y-auto px-6 py-4 scrollbar-hide">
        {messages.length === 0 && !loading && (
          <div className="flex h-full flex-col items-center justify-center gap-6">
            <p className="text-center text-gray-400">
              Select a company and ask a question about their SEC filings.
            </p>
            <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
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
                  className="rounded-xl border border-gray-200 px-4 py-3 text-left text-sm text-gray-600 hover:border-blue-300 hover:bg-blue-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="mx-auto max-w-3xl space-y-4">
          {messages.map((msg, i) => (
            <ChatMessage key={i} message={msg} />
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="max-w-[80%] rounded-2xl bg-gray-100 px-4 py-3">
                <p className="animate-pulse text-sm text-gray-500">
                  Thinking...
                </p>
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="border-t border-red-200 bg-red-50 px-6 py-2">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {/* Input bar */}
      <div className="border-t px-6 py-3">
        <div className="mx-auto flex max-w-3xl gap-3">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about SEC filings..."
            disabled={loading}
            className="flex-1 rounded-xl border border-gray-300 px-4 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-blue-500 focus:outline-none disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
