import { ChatPanel } from "@/components/ChatPanel";

export default function ChatPage() {
  return (
    <main className="relative flex h-screen flex-1 flex-col overflow-hidden">
      <div className="cnrs-halo cnrs-halo--hero" aria-hidden />
      <ChatPanel />
    </main>
  );
}
