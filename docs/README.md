# AI-GM Documentation

Complete user-facing documentation for the AI-GM autonomous Game Master.

## Structure

```
docs/
├── index.md                          # Landing page & overview
├── getting-started/                  # Quick setup & first session
│   ├── installation.md               # Install instructions
│   └── quickstart.md                 # Run your first game in 5min
├── user-guide/                       # How to play
│   ├── overview.md                   # Guide table of contents
│   ├── sessions.md                   # Manage sessions
│   ├── combat.md                     # Play combat encounters
│   └── settlements.md                # Explore the world
├── features/                         # Feature deep-dives
│   ├── overview.md                   # Feature roadmap
│   ├── campaign-generation.md        # Auto-generate campaigns
│   ├── combat.md                     # Combat system & NPC AI
│   ├── living-world.md               # Settlements & NPCs
│   ├── lore-system.md                # Semantic vault & memory
│   └── action-audit-trail.md         # What the AI did, recorded
├── api/                              # For integrations
│   ├── overview.md                   # API basics
│   └── rest-endpoints.md             # Complete endpoint reference
├── troubleshooting/                  # Help & support
│   └── faq.md                        # FAQs & common issues
├── architecture/                     # For developers
│   └── (future: technical deep-dives)
├── development/                      # For contributors
│   └── (future: dev setup, code standards)
└── README.md                         # This file
```

## Building the Website

### Option 1: MkDocs (Recommended)

MkDocs automatically generates a professional website from these markdown files.

**Install**:
```bash
pip install mkdocs mkdocs-material
```

**Configure**: Create `mkdocs.yml` in the repo root:
```yaml
site_name: AI-GM Documentation
site_description: Autonomous Game Master for FoundryVTT
theme:
  name: material
nav:
  - Home: index.md
  - Getting Started:
      - Installation: getting-started/installation.md
      - Quickstart: getting-started/quickstart.md
  - User Guide:
      - Overview: user-guide/overview.md
      - Managing Sessions: user-guide/sessions.md
      - Playing Combat: user-guide/combat.md
      - Exploring Settlements: user-guide/settlements.md
  - Features:
      - Overview: features/overview.md
      - Campaign Generation: features/campaign-generation.md
      - Combat System: features/combat.md
      - Living World: features/living-world.md
      - Lore System: features/lore-system.md
      - Action Audit Trail: features/action-audit-trail.md
  - API Reference:
      - Overview: api/overview.md
      - REST Endpoints: api/rest-endpoints.md
  - Troubleshooting: troubleshooting/faq.md
```

**Build**:
```bash
mkdocs build          # Build static site to `site/` directory
mkdocs serve          # Live preview during editing
```

**Deploy**: Upload `site/` to any static host (GitHub Pages, Netlify, etc.)

### Option 2: Docusaurus

Docusaurus is another popular choice with more interactive features.

**Install**:
```bash
npx create-docusaurus@latest ai-gm-docs classic
```

Then migrate markdown files into the Docusaurus structure.

## Document Standards

All documentation follows these standards:

- **Audience**: D&D players and GMs, not programmers
- **Tone**: Friendly, conversational, encouraging
- **Length**: 300-800 words per page (scannable)
- **Structure**: Headers, tables, code blocks, examples
- **Links**: Cross-link related pages liberally
- **Examples**: Real-world scenarios and use cases
- **No jargon**: Technical concepts explained simply

## Content Guidelines

When writing or updating docs:

1. **Start with the user's goal** — Why would someone read this page?
2. **Show, don't tell** — Use examples, screenshots, code samples
3. **Link to related pages** — Help readers discover connected content
4. **Keep it current** — Update as features change
5. **Write for beginners** — Assume no prior knowledge of the system

## Adding New Pages

1. Create `.md` file in the appropriate subdirectory
2. Add a header: `# Page Title`
3. Update the MkDocs nav config (see above)
4. Link to it from related pages
5. Test locally with `mkdocs serve`

## Archiving Old Docs

Development notes and implementation guides are archived in `/docs/Archived markdown/` — kept for reference but not part of the user-facing documentation.

See that folder for:
- Implementation guides (P1B, P2B, etc.)
- Architecture decisions
- Development notes
- Code review findings
- Technical specifications

These are **not** for end-users; they document the development process.

## Status

✅ **Documentation v1.0** — Complete and ready for website generation.

Last updated: August 2026

---

**Ready to build a website? Start with `mkdocs build`.**

**Questions?** See [troubleshooting/faq.md](./troubleshooting/faq.md).
