# YuktiCastle agent tasks

Each `.md` file in this directory (except this README and the
`_template.*.md` files) is a self-contained prompt for the
YuktiCastle implementer agent. Pass one to `npm run agents:run`:

```bash
npm run agents:run -- tasks/your-task.md
```

## Pick a template by shape

Different task shapes have different acceptance patterns. Pick the
template closest to what you're building, then copy + fill:

| Shape | Template | Use when… |
|---|---|---|
| **Feature / default** | `_template.md` | Adding new behavior, new endpoints, new content. The proven shape; covers ~70% of tasks. |
| **Refactor** | `_template.refactor.md` | **No** behavior change — restructuring, extracting modules, inlining. Reviewer enforces "all existing tests pass without modification". |
| **Schema** | `_template.schema.md` | Database migrations. Reviewer enforces `migrations/manual_<feature>.sql` + reversibility comment + no automatic `db:push`. |
| **UI** | `_template.ui.md` | Frontend pages / components. Reviewer enforces UI-library lock, no inline styles, accessibility minimums, loading/empty/error states. |
| **Bug fix** | `_template.bugfix.md` | Specific observable bug + repro steps. Reviewer enforces a regression test that would have caught it. |
| **E2E coverage** | `_template.e2e.md` | Add Playwright spec covering a user-visible flow. Test-only changes — reviewer blocks any production-code diff. Uses the `authedPage` fixture; passes against local dev AND deployed preview. |

Each shape's template includes shape-specific Constraints + Acceptance
criteria; the rest of the structure is identical so operators move
between shapes without re-learning the layout.

## Quick start

```bash
cp tasks/_template.md             tasks/your-new-feature.md     # default shape
cp tasks/_template.refactor.md    tasks/your-new-refactor.md    # restructuring
cp tasks/_template.schema.md      tasks/your-new-schema.md      # DB migration
cp tasks/_template.ui.md          tasks/your-new-ui.md          # frontend
cp tasks/_template.bugfix.md      tasks/your-new-bugfix.md      # bug fix
cp tasks/_template.e2e.md         tasks/your-new-e2e.md         # e2e coverage

$EDITOR tasks/your-new-task.md                        # fill the template
npm run agents:lint -- tasks/your-new-task.md         # ~50ms; catches H1/{{X}}/missing-acceptance
npm run agents:run  -- tasks/your-new-task.md
```

`_template.md` is the proven structure for general feature work
(see "Why this template works" below). The four shape-specific
variants share the same skeleton with shape-aware Constraints and
Acceptance criteria. The lint step is optional but highly
recommended — most of the historical "PromptError at startup" /
"reviewer had nothing to check" failures get caught there.

## The proven structure

Validated against two end-to-end runs (one auth-site build from
scratch in $0.09 / 1 iteration, one verity domain config in similar
scope). Both completed with the reviewer finding nothing to fix.

| Section | What goes in it | Why it matters |
|---|---|---|
| `# H1 title` | Imperative, verb-led, short | Becomes the branch slug — keep it readable |
| `> blockquote summary` | 2–4 sentence framing | Optional but useful for non-trivial tasks |
| `## Why this exists` | Context, motivation, deadline | Agent makes better trade-offs when it knows the goal |
| `## What to do` | Numbered steps with file paths, commands, naming | Most load-bearing section. Specificity → fewer iterations |
| `## Constraints` | Hard rules: ADR enforcement, scope budget, off-limits files, "don't push" | Reviewer enforces these. Block on violations |
| `## Files to read` | Explicit list: reference impl, types, registry, ADR(s) | Saves discovery iterations |
| `## Acceptance criteria` | Mechanical, testable: typecheck, smoke, file list | Reviewer checks each |
| `## When done` | `<promise>COMPLETE</promise>` reminder | Orchestrator stop signal |

The H1 + the H2 set above is what the proven runs use. Don't omit
the constraints or acceptance criteria — those are what keep the
reviewer effective and stop scope creep.

## ⚠️ Known gotchas

### Don't use `{{X}}` placeholder syntax in prose

YuktiCastle's prompt-substitution requires every `{{name}}` it finds
in the task spec or prompt files to have a matching value in
`promptArgs`. If you write something like:

```
> Use simple {{TOKEN}} string replacement for HTML templating.
```

…the run will die at startup with `PromptError: Prompt argument
"{{TOKEN}}" has no matching value in promptArgs`.

**Fix**: use `[[X]]` syntax for any placeholder example you mention
in prose. The agent will write code that uses `[[X]]` as the actual
template syntax — that works fine. The only "real" `{{...}}`
substitutions are `{{TASK_DESCRIPTION}}` (your task content) and
`{{BRANCH}}` (the agent's branch), both filled by `main.mts`.

Real run that hit this: hotel-app demo, fixed by replacing every
`{{TITLE}}`, `{{ROOMS_HTML}}` etc. example with `[[TITLE]]`,
`[[ROOMS_HTML]]`. ~30s of pain, easily avoided.

### Always `unset ANTHROPIC_API_KEY` before launching

If your shell has `ANTHROPIC_API_KEY` exported as an empty string
(some Claude Code installations do this silently), Node's
`--env-file` won't override it, and the agent in the container
gets `ANTHROPIC_API_KEY=""` which Claude CLI rejects as "Not
logged in." Always run:

```bash
unset ANTHROPIC_API_KEY CLAUDE_AUTH_TOKEN
npm run agents:run -- tasks/your-task.md
```

`main.mts` defends against this in code (only forwards env vars
with actual values), but the shell-side unset is the cleanest
fix.

## Why this template works

Three forces shape the agent's behavior, in order:

1. **The implement-prompt.md** sets project conventions (TypeScript,
   tenant scoping, off-limits files, etc.). Same across all tasks.
2. **The review-prompt.md** sets the gatekeeping checklist
   (typecheck, security audit for auth code, ADR-0046 for verity).
   Same across all tasks.
3. **The task .md** sets THIS task's goal, scope, and acceptance.
   Different per task.

If your prompts are doing their job, the task .md only needs the
specifics: what to build, what's off-limits for this task, and how
to know you're done. The template enforces this separation.

## Why files instead of env vars

For anything more than a one-liner, env-var quoting is painful:

```bash
# Unreadable, shell-escaping pitfalls, can't review in editor
TASK_DESCRIPTION="Add server/domains/hospitality.ts mirroring wellness, with persona, motifs, complianceLens for ADA + FTC, register in DOMAIN_REGISTRY..." npm run agents:run
```

vs

```bash
# Editable in your editor with full markdown
$EDITOR tasks/add-hospitality-domain.md
npm run agents:run -- tasks/add-hospitality-domain.md
```

The agent gets the same content; you get a much better authoring
experience.

## Branch slug derivation

`main.mts` builds the branch name from the H1 (or first 80 chars of
the body if there's no H1). So:

  good: `# Add hospitality domain config`
   →  `agent/add-hospitality-domain-config-20260507`

  bad:  `# I want to maybe add a domain config for the hospitality vertical`
   →  `agent/i-want-to-maybe-add-a-domain-config-fo-20260507`

Keep titles concise.

## Should I commit task files?

Up to you. Two reasonable conventions:

1. **Commit them** — directory becomes a history of what you've
   asked the agent to do. Useful for audit, useful for "was this
   feature agent-built?", useful as templates for similar future
   tasks.

2. **Gitignore them** — task files are ephemeral working drafts;
   the committed work is the agent's branch + commits. Add
   `tasks/*.md` to `.gitignore` and keep `tasks/README.md` +
   `tasks/_template.md` + `tasks/.gitkeep`.

The example task files committed here serve as reference templates
for future operators (and future-you).
