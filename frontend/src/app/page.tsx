import { ChatPanel } from "@/components/ChatPanel";
import { PageHeader } from "@/components/PageHeader";

export default function ChatPage() {
  return (
    <main className="relative flex flex-1 flex-col overflow-hidden">
      <div className="cnrs-halo cnrs-halo--hero" aria-hidden />
      <PageHeader
        eyebrow="Chat"
        title="Ask the literature."
        subtitle="Six retrieval modes, real-time streaming, traceable sources."
      />
      <ChatPanel />
    </main>
  );
}
