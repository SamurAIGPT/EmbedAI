"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { applyEvent, emptyTranscript, type Transcript } from "@/lib/transcript";

export type Connection = "idle" | "connecting" | "live" | "reconnecting" | "failed";

export type ThreadStream = {
  threadId: number | null;
  transcript: Transcript;
  connection: Connection;
  // Set only when the connection failed for good (the thread does not exist, access refused).
  error: string | null;
};

function initial(threadId: number | null): ThreadStream {
  return {
    threadId,
    transcript: emptyTranscript(),
    connection: threadId === null ? "idle" : "connecting",
    error: null,
  };
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true }
    );
  });
}

// One stream per open thread: the server replays every stored event from the
// start and then goes live, so loading history and following live updates are
// the same thing. After any drop it reconnects from the last event seen, so
// nothing is missed and nothing is shown twice.
export function useThreadStream(threadId: number | null): ThreadStream {
  const [state, setState] = useState<ThreadStream>(() => initial(threadId));

  useEffect(() => {
    if (threadId === null) return;
    const controller = new AbortController();
    let transcript = emptyTranscript();

    const publish = (patch: Partial<ThreadStream>) =>
      setState((prev) => ({
        ...(prev.threadId === threadId ? prev : initial(threadId)),
        ...patch,
        threadId,
      }));

    (async () => {
      let delay = 500;
      while (!controller.signal.aborted) {
        try {
          await api.streamThread(
            threadId,
            transcript.lastSeq === 0 ? null : transcript.lastSeq,
            (event) => {
              const next = applyEvent(transcript, event);
              if (next === transcript) return;
              transcript = next;
              publish({ transcript });
            },
            controller.signal,
            () => {
              delay = 500;
              publish({ connection: "live", error: null });
            }
          );
        } catch (err) {
          if (controller.signal.aborted) return;
          // Not worth retrying: the thread is not there, or access is refused.
          if (err instanceof ApiError && [400, 403, 404].includes(err.status)) {
            publish({
              connection: "failed",
              error: err.status === 404 ? "This conversation was not found." : err.message,
            });
            return;
          }
        }
        if (controller.signal.aborted) return;
        publish({ connection: "reconnecting" });
        await sleep(delay, controller.signal);
        delay = Math.min(delay * 2, 8000);
      }
    })();

    return () => controller.abort();
  }, [threadId]);

  return state.threadId === threadId ? state : initial(threadId);
}
