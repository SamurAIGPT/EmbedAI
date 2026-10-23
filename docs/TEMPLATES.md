# Templates

Templates are ready-made starting points for new agents. Open **Agents → Templates…** to browse a bundled list of roles, filter by category (Everyday, Work, Writing, Design, Sales, Personal) and search by name, role, description or category.

## What choosing one does

**Add** only fills the new-agent form: name, title, description, instructions and optional starter notes. Every field can be edited. Nothing is created until you press **Save**, and **Cancel** returns to the gallery with nothing written. A template cannot choose your engine, model, workspace folder or approval mode, enable a tool, connect an account or schedule anything: those stay your choices, with the usual defaults.

After Save the new agent is selected, ready to chat. Adding the same template again names the next copy `Name 2`, `Name 3` and so on; copies are independent.

## Suggested skills

Some templates suggest skills (text instructions the agent can be asked to follow). They are listed with a checkbox in the form and saved on the new agent after it is created. If saving a skill fails, or this version has no skills library, the agent stays and a notice names what was not added.

## Roles that need a connection

A template built around a connection this app does not have yet (email, community feeds) is shown with "Needs … · not available yet" and its Add button is disabled. Most roles need no connection and work offline.

## Where the catalog lives

`backend/app/templates/catalog.py` (bundled, versioned by `CATALOG_VERSION`) served read-only by `GET /api/v1/templates` (`q`, `category`) and `GET /api/v1/templates/{id}`. Browsing makes no model call and no network request. Local access is required, like the rest of the agent API.

## Not included yet

Saved presets, a public or organisation catalog, team packages, importing from GitHub, and creating a team from a project folder.
