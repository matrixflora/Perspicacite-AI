"use client";

import { use } from "react";
import { ChatPanel } from "@/components/ChatPanel";

export default function ChatResumePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const conversationId = decodeURIComponent(id);

  return (
    <main className="relative flex h-screen flex-1 flex-col overflow-hidden">
      <div className="cnrs-halo cnrs-halo--hero" aria-hidden />
      <ChatPanel initialConversationId={conversationId} />
    </main>
  );
}
