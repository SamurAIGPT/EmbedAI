"use client";

// An agent's skills: Markdown instructions the person writes, imports or saves from a finished
// conversation, reviews and switches on. A skill only reaches a conversation when the person
// picks it for a message (the Composer's skill picker); nothing here sends or runs anything.
import { useCallback, useEffect, useState } from "react";
import { api, type Agent } from "@/lib/api";
import {
  isSkillName,
  skillErrorText,
  skillSkeleton,
  slugSkillName,
  sourceLabel,
  SKILL_NAME_MAX,
  type SkillSource,
  type SkillSummary,
} from "@/lib/skills";

const FIELD =
  "w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm placeholder:text-muted";
const SMALL =
  "cursor-pointer rounded-md border border-line px-2 py-1 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const BUTTON =
  "cursor-pointer rounded-md border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY =
  "cursor-pointer rounded-md px-4 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 bg-accent text-accent-fg hover:opacity-90";

type Draft = {
  // Null while a new skill has no name yet or a name that is still being edited.
  existing: string | null;
  name: string;
  description: string;
  body: string;
  enabled: boolean;
  source: SkillSource;
  warnings: string[];
  redacted: number;
};

type Import = { text: string; error: string | null };

export function SkillsPanel({
  agent,
  seed = null,
  onChanged,
}: {
  agent: Agent;
  // Start with a new skill drafted from this text (Save as skill). It opens the editor only:
  // nothing is saved until the person presses Save. Read once, when the panel mounts; mount
  // it with a new `key` to start another. Mount with a `key` per agent too.
  seed?: string | null;
  onChanged?: () => void;
}) {
  const agentId = agent.id;
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(() =>
    seed === null ? null : { existing: null, ...skillSkeleton(seed), enabled: false, source: "learned", warnings: [], redacted: 0 },
  );
  const [importing, setImporting] = useState<Import | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSkills(await api.listSkills(agentId));
      setError(null);
    } catch (err) {
      setSkills([]);
      setError(skillErrorText(err));
    }
  }, [agentId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const startNew = () => {
    setImporting(null);
    setDraft({ existing: null, ...skillSkeleton(), enabled: false, source: "user", warnings: [], redacted: 0 });
  };

  const open = async (name: string) => {
    setBusy(true);
    setError(null);
    try {
      const doc = await api.getSkill(agentId, name);
      setDraft({ existing: name, ...doc });
    } catch (err) {
      setError(skillErrorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (skill: SkillSummary) => {
    setBusy(true);
    setError(null);
    try {
      const doc = await api.getSkill(agentId, skill.name);
      await api.saveSkill(agentId, skill.name, { description: doc.description, body: doc.body, enabled: !skill.enabled, source: doc.source });
      await load();
      onChanged?.();
    } catch (err) {
      setError(skillErrorText(err));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    const name = draft.existing ?? draft.name;
    setBusy(true);
    setError(null);
    try {
      const doc = await api.saveSkill(agentId, name, {
        description: draft.description.trim(),
        body: draft.body,
        enabled: draft.enabled,
        source: draft.source,
      });
      // What was saved can differ (secrets are removed); show it so the person sees the result.
      setDraft({ existing: name, ...doc });
      await load();
      onChanged?.();
    } catch (err) {
      setError(skillErrorText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (name: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteSkill(agentId, name);
      setConfirming(null);
      setDraft((d) => (d?.existing === name ? null : d));
      await load();
      onChanged?.();
    } catch (err) {
      setError(skillErrorText(err));
    } finally {
      setBusy(false);
    }
  };

  const preview = async () => {
    if (!importing || !importing.text.trim()) return;
    setBusy(true);
    setImporting({ ...importing, error: null });
    try {
      const p = await api.previewSkill(agentId, importing.text);
      // Imported skills always start switched off; the person reviews, then switches one on.
      setDraft({ existing: null, name: p.name, description: p.description, body: p.body, enabled: false, source: "imported", warnings: p.warnings, redacted: 0 });
      setImporting(null);
    } catch (err) {
      setImporting({ ...importing, error: skillErrorText(err) });
    } finally {
      setBusy(false);
    }
  };

  const nameValid = draft ? (draft.existing !== null || isSkillName(draft.name)) : false;
  const taken = draft !== null && draft.existing === null && (skills ?? []).some((s) => s.name === draft.name);
  const canSave = draft !== null && !busy && nameValid && !taken && draft.body.trim() !== "" && draft.description.trim() !== "";

  return (
    <section aria-label="Skills" className="space-y-3 border-t border-line pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Skills</h3>
        <div className="flex gap-2">
          <button className={SMALL} onClick={startNew} disabled={busy}>New skill</button>
          <button className={SMALL} onClick={() => { setDraft(null); setImporting({ text: "", error: null }); }} disabled={busy}>
            Import SKILL.md
          </button>
        </div>
      </div>
      <p className="text-xs text-muted">
        Saved instructions for {agent.name}. A skill is used only when you pick it for a message; it never starts on its own,
        and importing one does not switch it on.
      </p>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}

      {importing && (
        <div className="space-y-2 rounded-md border border-line p-3">
          <label className="block text-xs text-muted" htmlFor="skill-import">Paste the contents of a SKILL.md file</label>
          <textarea
            id="skill-import"
            className={`${FIELD} min-h-32 font-mono text-xs`}
            value={importing.text}
            onChange={(e) => setImporting({ text: e.target.value, error: null })}
            placeholder={"---\nname: weekly-report\ndescription: Summarise the week\n---\n\nSteps…"}
          />
          {importing.error && <p role="alert" className="text-xs text-danger">{importing.error}</p>}
          <div className="flex gap-2">
            <button className={PRIMARY} onClick={preview} disabled={busy || !importing.text.trim()}>Review</button>
            <button className={BUTTON} onClick={() => setImporting(null)} disabled={busy}>Cancel</button>
          </div>
        </div>
      )}

      {draft && (
        <div className="space-y-2 rounded-md border border-line p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
            <span>{draft.existing === null ? "New skill" : "Editing"} · {sourceLabel(draft.source)}</span>
            <span>{draft.enabled ? "On" : "Off"}</span>
          </div>
          {draft.warnings.length > 0 && (
            <ul role="status" className="list-disc space-y-0.5 pl-5 text-xs text-warning">
              {draft.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          {draft.redacted > 0 && (
            <p role="status" className="text-xs text-warning">
              {draft.redacted} secret-like {draft.redacted === 1 ? "value was" : "values were"} removed from the saved text.
            </p>
          )}
          <label className="block text-xs text-muted" htmlFor="skill-name">Name</label>
          <input
            id="skill-name"
            className={FIELD}
            value={draft.existing ?? draft.name}
            disabled={draft.existing !== null}
            maxLength={SKILL_NAME_MAX}
            onChange={(e) => setDraft({ ...draft, name: slugSkillName(e.target.value) })}
            placeholder="expense-review"
          />
          {draft.existing === null && draft.name !== "" && taken && <p className="text-xs text-danger">A skill with this name already exists.</p>}
          {draft.existing === null && draft.name === "" && <p className="text-xs text-muted">Lowercase letters, digits and hyphens.</p>}
          <label className="block text-xs text-muted" htmlFor="skill-description">Description (one line, shown in the picker)</label>
          <input
            id="skill-description"
            className={FIELD}
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          />
          <label className="block text-xs text-muted" htmlFor="skill-body">Instructions (Markdown)</label>
          <textarea
            id="skill-body"
            className={`${FIELD} min-h-64 font-mono text-xs`}
            value={draft.body}
            onChange={(e) => setDraft({ ...draft, body: e.target.value })}
          />
          <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={draft.enabled} onChange={() => setDraft({ ...draft, enabled: !draft.enabled })} />
            Available to pick in conversations
          </label>
          <div className="flex flex-wrap gap-2">
            <button className={PRIMARY} onClick={save} disabled={!canSave}>{busy ? "Saving…" : "Save skill"}</button>
            <button className={BUTTON} onClick={() => setDraft(null)} disabled={busy}>Close</button>
          </div>
        </div>
      )}

      {skills === null ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : skills.length === 0 ? (
        <p className="text-xs text-muted">No skills yet. Write one, import a SKILL.md, or use Save as skill after a conversation that went well.</p>
      ) : (
        <ul className="space-y-2">
          {skills.map((skill) => (
            <li key={skill.name} className="rounded-md border border-line p-2">
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={skill.enabled}
                    disabled={busy}
                    onChange={() => toggle(skill)}
                    aria-label={`${skill.enabled ? "Switch off" : "Switch on"} ${skill.name}`}
                  />
                  <span className="min-w-0">
                    <span className="block break-words font-mono text-xs text-ink">/{skill.name}</span>
                    <span className="block break-words text-xs text-muted">{skill.description}</span>
                    <span className="block text-xs text-faint">{sourceLabel(skill.source)} · {skill.enabled ? "on" : "off"}</span>
                  </span>
                </label>
                <div className="flex shrink-0 gap-2">
                  <button className={SMALL} onClick={() => open(skill.name)} disabled={busy}>Edit</button>
                  {confirming === skill.name ? (
                    <>
                      <button className={`${SMALL} text-danger`} onClick={() => remove(skill.name)} disabled={busy}>Delete</button>
                      <button className={SMALL} onClick={() => setConfirming(null)} disabled={busy}>Keep</button>
                    </>
                  ) : (
                    <button className={SMALL} onClick={() => setConfirming(skill.name)} disabled={busy}>Delete…</button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
