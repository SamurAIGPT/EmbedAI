// Strings for the new interface only; older components keep their literal text. One language
// today: add another by adding a table here and picking it by the document's language.
// Keys are surface.name; {name} in a message is a placeholder. No imports, so node tests can load it.
export const en = {
  "theme.label": "Theme",
  "theme.system": "System",
  "theme.light": "Light",
  "theme.dark": "Dark",
  "common.close": "Close",
  "common.loading": "Loading…",
  "common.cancel": "Cancel",
  "common.delete": "Delete",
  "shell.newChat": "New chat",
  "shell.openSidebar": "Open conversations",
  "shell.conversations": "Conversations",
  "sidebar.agents": "Agents",
  "sidebar.manageAgents": "Manage agents",
  "sidebar.newAgent": "New agent",
  "sidebar.noAgents": "No agents yet. An agent has its own instructions, notes and computer.",
  "sidebar.recent": "Recent",
  "sidebar.search": "Search conversations",
  "sidebar.loading": "Loading…",
  "sidebar.empty": "No conversations yet. Start one with New chat; they are kept on this computer.",
  "sidebar.emptyArchived": "No conversations.",
  "sidebar.showArchived": "Show archived",
  "sidebar.hideArchived": "Hide archived",
  "sidebar.routines": "Routines",
  "sidebar.settings": "Settings",
  "sidebar.rowActions": "Conversation actions",
  "sidebar.rename": "Rename",
  "sidebar.archive": "Archive",
  "sidebar.unarchive": "Unarchive",
  "sidebar.deleteAsk": "Delete…",
  "sidebar.deleteTitle": "Delete this conversation?",
  "sidebar.deleteBody": "Its messages and tool calls stored by this app are removed and cannot be restored. Claude's own copy of the session stays in your .claude folder.",
  "sidebar.renameLabel": "Conversation name",
  "sidebar.renameHint": "Enter to save, Escape to cancel",
  "sidebar.untitled": "New chat",
  "sidebar.archivedTag": "archived",
  "status.working": "Working",
  "status.waiting": "Waiting for you",
  "status.reset": "Needs a new session",
  "header.more": "More",
  "header.approvals": "Approvals",
  "header.files": "Files",
  "header.usage": "Usage",
  "header.agentForChat": "Agent for this chat",
  "header.noAgent": "No agent",
  "header.engine": "Choose engine and model",
  "header.reconnecting": "Reconnecting…",
  "header.unavailable": "Unavailable",
  "header.chatOnly": "chat only",
  "header.ready": "Ready",
  "notifications.title": "Notifications",
  "notifications.unread": "Notifications, {count} unread",
  "notifications.markAll": "Mark all read",
  "notifications.empty": "Nothing yet.",
} as const;

export type MessageKey = keyof typeof en;

export function t(key: MessageKey, params?: Record<string, string | number>): string {
  const template: string = en[key] ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in params ? String(params[name]) : whole));
}
