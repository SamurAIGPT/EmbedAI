"use client";

// Memory upkeep for one agent (switch, tidy up now, the hour, the helper model,
// learning from group chats) and the About me text every agent reads
// (docs/MEMORY.md). Shown inside the memory panel under an agent's notes.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AboutMe, type Agent, type Engine, type MemorySettings, type MemoryUpkeepStatus } from "@/lib/api";
import { tidyLine } from "@/lib/agents";
import { captureLine, helperChange, helperModelLine, tidyHourLabel, withHelperModel } from "@/lib/memory";

const FIELD =
  "w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm placeholder:text-muted";
const SMALL =
  "cursor-pointer rounded-md border border-line px-2 py-1 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY_SMALL =
  "cursor-pointer rounded-md px-3 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50 bg-accent text-accent-fg hover:opacity-90";

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

export function UpkeepCard({
  agent,
  onAgentChange,
  onError,
  onNotesChanged,
}: {
  agent: Agent;
  onAgentChange: (patch: Partial<Agent>) => void;
  onError: (e: string | null) => void;
  onNotesChanged: () => void;
}) {
  const notesOn = agent.memory_enabled ?? true;
  const upkeepOn = agent.memory_upkeep ?? true;
  const summaryOn = agent.compaction_summary ?? true;
  const helperAllowed = agent.use_helper ?? true;
  const [status, setStatus] = useState<MemoryUpkeepStatus | null>(null);
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [model, setModel] = useState("");
  const [engines, setEngines] = useState<Engine[]>([]);
  const [helperEngine, setHelperEngine] = useState("");
  const [helperName, setHelperName] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextStatus, nextSettings] = await Promise.all([api.memoryUpkeep(agent.id), api.memorySettings()]);
      setStatus(nextStatus);
      setSettings(nextSettings);
      setModel(nextSettings.helper_models[nextStatus.engine ?? agent.provider] ?? "");
      setHelperEngine(nextSettings.helper?.engine ?? "");
      setHelperName(nextSettings.helper?.model ?? "");
    } catch (err) {
      onError(errorText(err));
    }
  }, [agent.id, agent.provider, onError]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load, upkeepOn]);

  useEffect(() => {
    api.getEngines().then(setEngines).catch(() => setEngines([]));
  }, []);

  const guard = async (work: () => Promise<void>) => {
    setBusy(true);
    onError(null);
    try {
      await work();
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleSummary = () =>
    guard(async () => {
      await api.updateAgent(agent.id, { compaction_summary: !summaryOn });
      onAgentChange({ compaction_summary: !summaryOn });
    });

  const toggleHelper = () =>
    guard(async () => {
      await api.updateAgent(agent.id, { use_helper: !helperAllowed });
      onAgentChange({ use_helper: !helperAllowed });
      await load();
    });

  const toggleUpkeep = () =>
    guard(async () => {
      await api.updateAgent(agent.id, { memory_upkeep: !upkeepOn });
      onAgentChange({ memory_upkeep: !upkeepOn });
    });

  const tidyNow = () =>
    guard(async () => {
      const report = await api.tidyAgentMemory(agent.id);
      setResult(tidyLine(report));
      onNotesChanged();
      await load();
    });

  const change = (patch: Partial<MemorySettings>) =>
    guard(async () => {
      setSettings(await api.saveMemorySettings(patch));
      await load();
    });

  const engine = status?.engine ?? agent.provider;
  const savedModel = settings?.helper_models[engine] ?? "";
  const tidied = status?.last_tidy;
  const noticed = captureLine(status?.last_capture);
  const active = notesOn && upkeepOn;
  return (
    <div className="space-y-2 rounded-md border border-line p-3 text-xs" aria-label="Memory upkeep">
      <label className="flex cursor-pointer items-start gap-2 text-sm">
        <input
          type="checkbox"
          role="switch"
          checked={upkeepOn}
          disabled={busy || !notesOn}
          onChange={() => void toggleUpkeep()}
          className="mt-0.5"
        />
        <span>
          <span className="font-medium">Memory upkeep</span>
          <span className="block text-xs text-muted">
            Notices facts when a conversation goes quiet and tidies the notes once a night, with quick calls to this
            agent&apos;s own engine. Every change is in History and can be undone.
            {!notesOn && " Switch the notes on first."}
          </span>
        </span>
      </label>
      <label className="flex cursor-pointer items-start gap-2">
        <input
          type="checkbox"
          role="switch"
          checked={summaryOn}
          disabled={busy}
          onChange={() => void toggleSummary()}
          className="mt-0.5"
          aria-label="Summarize long conversations with a model"
        />
        <span>
          <span className="font-medium">Summarize long conversations with a model</span>
          <span className="block text-muted">
            When a conversation outgrows an engine that replays it (an OpenAI-compatible endpoint), the earlier part is
            summarized by that same engine, with secrets removed first; if the call fails or takes over 20 seconds,
            excerpts are used instead. Off: always excerpts.
          </span>
        </span>
      </label>
      {active && (
        <>
          {noticed && <p className="text-muted">{noticed}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => void tidyNow()} disabled={busy} className={SMALL}>
              {busy ? "Working…" : "Tidy up now"}
            </button>
            <span className="text-muted">
              {tidied
                ? `Last tidy-up ${new Date(tidied.at * 1000).toLocaleString()}: ${tidyLine(tidied)}`
                : "Not tidied yet."}
            </span>
          </div>
          {result && <p role="status">{result}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={`tidy-hour-${agent.id}`}>Tidy up each night after</label>
            <select
              id={`tidy-hour-${agent.id}`}
              value={settings?.tidy_hour ?? status?.tidy_hour ?? 3}
              disabled={busy || !settings}
              onChange={(e) => void change({ tidy_hour: Number(e.target.value) })}
              className="rounded-md border border-line bg-transparent px-2 py-1"
            >
              {Array.from({ length: 25 }, (_, h) => (
                <option key={h} value={h}>
                  {tidyHourLabel(h)}
                </option>
              ))}
            </select>
            <span className="text-muted">Applies to every agent; never while the agent is working.</span>
          </div>
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor={`helper-model-${agent.id}`}>Helper model for {engine}</label>
              <input
                id={`helper-model-${agent.id}`}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="the agent's own model"
                className={`${FIELD} max-w-56`}
              />
              <button
                onClick={() => settings && void change({ helper_models: withHelperModel(settings.helper_models, engine, model) })}
                disabled={busy || !settings || model.trim() === savedModel}
                className={SMALL}
              >
                Save model
              </button>
            </div>
            <p className="text-muted">
              {helperModelLine(status, helperAllowed)} A smaller model is enough. This model is on the engine this
              agent already uses, so nothing goes to another provider.
            </p>
          </div>
          <div className="space-y-1 rounded-md border border-line p-2">
            <p className="font-medium">Helper on another provider (off unless you set it)</p>
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor={`helper-engine-${agent.id}`}>Engine</label>
              <select
                id={`helper-engine-${agent.id}`}
                value={helperEngine}
                disabled={busy || !settings || settings.helper_active?.source === "environment"}
                onChange={(e) => setHelperEngine(e.target.value)}
                className="rounded-md border border-line bg-transparent px-2 py-1"
              >
                <option value="">None (nothing leaves the agent&apos;s provider)</option>
                {engines
                  .filter((e) => e.can_write_text)
                  .map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.label}
                    </option>
                  ))}
              </select>
              <label htmlFor={`helper-engine-model-${agent.id}`}>Model</label>
              <input
                id={`helper-engine-model-${agent.id}`}
                value={helperName}
                onChange={(e) => setHelperName(e.target.value)}
                placeholder="its default"
                disabled={!helperEngine || settings?.helper_active?.source === "environment"}
                className={`${FIELD} max-w-48`}
              />
              <button
                onClick={() => void change(helperChange(helperEngine, helperName))}
                disabled={
                  busy ||
                  !settings ||
                  settings.helper_active?.source === "environment" ||
                  (helperEngine === (settings.helper?.engine ?? "") && helperName.trim() === (settings.helper?.model ?? ""))
                }
                className={SMALL}
              >
                Save helper
              </button>
            </div>
            {settings?.helper_active?.source === "environment" && (
              <p className="text-muted">Set by the server&apos;s environment ({settings.helper_active.engine}); change it there.</p>
            )}
            <p className="text-muted">
              Used only for agents whose own engine cannot write the job (Codex and ACP agents): the summary of a long
              conversation and the memory upkeep steps. An agent whose engine can write it always writes it itself.
              With a helper set, <strong>the conversations being read, the notes (secrets removed first) and the history
              being summarized are sent to the helper&apos;s provider</strong>, which may not be this agent&apos;s. Each
              call is listed under Usage.
            </p>
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                role="switch"
                checked={helperAllowed}
                disabled={busy}
                onChange={() => void toggleHelper()}
                className="mt-0.5"
                aria-label="Allow the helper model for this agent"
              />
              <span>
                <span className="font-medium">Allow the helper model for this agent</span>
                <span className="block text-muted">
                  Off: the helper never writes this agent&apos;s texts, whatever you set above. This covers this agent&apos;s own
                  jobs only; what it says in a room becomes part of the other agents&apos; conversations.
                </span>
              </span>
            </label>
          </div>
          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              role="switch"
              checked={settings?.learn_from_rooms ?? false}
              disabled={busy || !settings}
              onChange={() => settings && void change({ learn_from_rooms: !settings.learn_from_rooms })}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Learn from group chats</span>
              <span className="block text-muted">
                Off by default, for every agent. When on, what an agent read and answered in a room can be noticed
                too: your own lines count as yours; another person&apos;s lines give only facts about the shared work
                and never reach About me; another agent&apos;s lines give only an outcome it verified or a decision you
                agreed to. Entries are marked with the room.
              </span>
            </span>
          </label>
        </>
      )}
    </div>
  );
}

export function AboutMeTab({ onError }: { onError: (e: string | null) => void }) {
  const [about, setAbout] = useState<AboutMe | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState<{ current: string; hash: string } | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.aboutMe();
      setAbout(next);
      setText(next.text);
    } catch (err) {
      onError(errorText(err));
    }
  }, [onError]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const save = async (overwrite = false) => {
    if (!about) return;
    setBusy(true);
    onError(null);
    try {
      const next = await api.saveAboutMe(text, overwrite ? null : about.hash);
      setAbout(next);
      setText(next.text);
      setConflict(null);
      setSaved(next.redacted ? `Saved. ${next.redacted} thing(s) that looked like secrets were replaced.` : "Saved.");
    } catch (err) {
      if (err instanceof ApiError && err.code === "memory_changed") {
        const current = await api.aboutMe();
        setConflict({ current: current.text, hash: current.hash });
      } else onError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    setBusy(true);
    onError(null);
    try {
      const next = await api.removeLearnedFact(id);
      if (about && text === about.text) setText(next.text);
      setAbout(next);
    } catch (err) {
      onError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  if (about === null) return <p className="text-xs text-muted">Loading…</p>;
  const dirty = text !== about.text;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">
        What you want every agent to know about you. All of your agents read it, even one whose notes are off. Agents
        add a line when you tell them something lasting about yourself (never from another person&apos;s words, and
        never from what an agent said); it is background, never an instruction.
      </p>
      <textarea
        value={text}
        rows={6}
        onChange={(e) => {
          setText(e.target.value);
          setSaved(null);
        }}
        aria-label="About me"
        placeholder={"- I live in Pune\n- I like short answers"}
        className={`${FIELD} resize-y font-mono`}
      />
      <p className="text-xs text-muted">
        {text.length.toLocaleString()} of {about.max_chars.toLocaleString()} characters
      </p>
      {conflict && (
        <div role="alert" className="rounded-md border border-warning/60 p-3 text-xs">
          <p>An agent added to About me while you were editing. Load the new text, or keep yours and overwrite it.</p>
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => {
                setAbout({ ...about, text: conflict.current, hash: conflict.hash });
                setText(conflict.current);
                setConflict(null);
              }}
              disabled={busy}
              className={SMALL}
            >
              Load the new text
            </button>
            <button onClick={() => void save(true)} disabled={busy} className={SMALL}>
              Overwrite with mine
            </button>
          </div>
        </div>
      )}
      {saved && <p className="text-xs text-muted">{saved}</p>}
      <div className="flex gap-2">
        <button onClick={() => void save()} disabled={busy || !dirty} className={PRIMARY_SMALL}>
          {busy ? "Saving…" : "Save About me"}
        </button>
        {dirty && (
          <button onClick={() => setText(about.text)} disabled={busy} className={SMALL}>
            Discard
          </button>
        )}
      </div>
      <div className="space-y-1 border-t border-line pt-2">
        <p className="text-xs font-medium">Added by your agents</p>
        {about.learned.length === 0 ? (
          <p className="text-xs text-muted">Nothing yet.</p>
        ) : (
          <ul className="space-y-1">
            {about.learned.map((fact) => (
              <li key={fact.id} className="flex flex-wrap items-center gap-2 text-xs">
                <span>{fact.text}</span>
                <span className="text-muted">
                  learned by {fact.agent_name || "an agent"} · {new Date(fact.at * 1000).toLocaleDateString()}
                </span>
                <button onClick={() => void remove(fact.id)} disabled={busy} className={SMALL}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted">A removed fact is never added again.</p>
      </div>
    </div>
  );
}
