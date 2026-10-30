# Agent instructions

This repository publishes Agent skills (`skills/<name>/SKILL.md`) together with
their generated index.

## Read the index first

- Before answering "which skills are available" or picking a skill, read
  `SKILLS.md`: it is the English, AI-facing catalogue, one section per skill with
  its purpose, when to use it, and where it lives.
- Before acting on a skill, read that skill's own `SKILL.md`.

## The index is generated: do not edit it by hand

- `SKILLS.json` is the database and the single source of truth. It is the only
  index file an agent edits, and it holds the file inventory, the upstream
  descriptions and the prose of every language.
- `SKILLS.md` (English, for agents), `SKILLS.html` and `README.md` (Simplified
  Chinese, for people) are rendered from `SKILLS.json` by
  `.github/workflows/skills-index.yml`. Hand edits to those three are overwritten
  by the workflow on the next push, so change the database instead.

## Updating the index

Use the `yashi-build-skills-index` skill (`skills/yashi-build-skills-index/`).
It compares every skill with the database by file list and modification time, so
only entries that really changed are rewritten.

```bash
# 1. update the database (the workflow makes a plain run do exactly this)
python skills/yashi-build-skills-index/scripts/build_skills_index.py

# 2. translate or review the pending Chinese prose shown in the report; either
#    edit locales.zh-Hans in SKILLS.json, or export a draft and merge it back
python skills/yashi-build-skills-index/scripts/build_skills_index.py --emit-draft

# 3. confirm translations that need no change, and commit SKILLS.json
python skills/yashi-build-skills-index/scripts/build_skills_index.py --mark-reviewed
```

When the repository has the workflow, a plain run updates `SKILLS.json` only and
skips the generated files. Add `--ignore-ci` to render `SKILLS.md`,
`SKILLS.html` and `README.md` locally as well, for example to preview a change.

## Adding or removing a skill

1. Add or remove the folder under `skills/`.
2. Run the skill above so `SKILLS.json` picks the change up (it reports new
   entries as untranslated).
3. Translate the new entries, then commit the database. The workflow rebuilds
   the index files.
