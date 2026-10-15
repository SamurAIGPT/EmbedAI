import { Suspense } from "react";
import { ChatApp } from "@/components/chat/ChatApp";

export const metadata = {
  title: "Chat · Vadoo Autonomous Agent",
};

// The open conversation is a query string (/chat?thread=3), not a route
// segment, so the page also works as a static export. useSearchParams needs
// the Suspense boundary for that.
export default function ChatPage() {
  return (
    <Suspense fallback={<p className="p-6 text-center text-sm text-zinc-500">Loading…</p>}>
      <ChatApp />
    </Suspense>
  );
}
