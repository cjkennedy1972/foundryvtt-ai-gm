# AI-GM Documentation

User-facing documentation for the AI-GM autonomous Game Master, plus two
contributor-facing pages. This file is a guide to the folder; it is excluded from
the generated site, where `index.md` is the landing page.

## Layout

| Path | Audience | Contents |
|------|----------|----------|
| `index.md` | Everyone | Landing page and overview |
| `getting-started/` | New users | Installation, quickstart |
| `user-guide/` | Players and GMs | Sessions, combat, settlements |
| `features/` | Players and GMs | Campaign generation, living world, lore, audit trail |
| `api/` | Integrators | REST endpoint reference |
| `troubleshooting/` | Everyone | FAQ |
| `ROADMAP.md` | Contributors | Positioning, architecture decisions, backlog status |
| `architecture-refactor.md` | Contributors | Why routes live in `ai-engine/api/routes/` |

## Building the site

`mkdocs.yml` at the repo root already configures the Material theme, search and nav.

```bash
pip install mkdocs mkdocs-material
mkdocs serve          # live preview
mkdocs build --strict # build to site/, fail on broken links
```

Deploy `site/` to any static host.

## Writing standards

- Write for D&D players and GMs, not programmers. Explain technical concepts in plain
  terms.
- 300-800 words per page, scannable: headers, tables, short examples.
- Cross-link related pages.
- A new page needs a `# Title` heading and an entry in the `nav:` block of
  `mkdocs.yml`, or it will not appear in the site.

Run `mkdocs build --strict` before opening a PR. It fails on broken internal links,
which is how stale cross-references get caught.
