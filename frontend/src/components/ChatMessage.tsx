import type { Source } from "@/lib/api";
import SourcesPanel from "./SourcesPanel";

export interface Message {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
}

interface ChatMessageProps {
  message: Message;
}

export default function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div style={{
      fontSize: "12px",
      padding: "4px 0",
      borderBottom: "1px dotted #c0c0c0",
    }}>
      <span style={{
        fontWeight: "bold",
        color: isUser ? "#000080" : "#008000",
      }}>
        {isUser ? "You:" : "Assistant:"}
      </span>{" "}
      <span style={{ whiteSpace: "pre-wrap", lineHeight: "1.5" }}>
        {message.content}
      </span>
      {!isUser && message.sources && message.sources.length > 0 && (
        <SourcesPanel sources={message.sources} />
      )}
    </div>
  );
}
