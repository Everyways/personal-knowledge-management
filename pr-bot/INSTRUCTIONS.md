# second-brain → GitHub PR bot — instructions

Runs daily (scheduled). Goal: review the latest project analysis + recent inbox
articles from Benoît's Obsidian vault, and — only when there's a genuinely
concrete, accurate, implementable idea for one of his 3 projects — open a
**draft** pull request on the real GitHub repo. Never merge. Never force a PR
just to have done something; skipping is the correct outcome most days.

## Inputs

- Vault path: `/home/everyways/2nd_brain_obsidian/2nd brain`
  (request Cowork directory access to this path if not already granted this session)
- Today's project analysis note: `Projets/Analyses/<date> Analyse-<project>.md`
  (only one project is analyzed per day, in rotation — see `config.yaml`'s
  `projects:` list in `/home/everyways/second-brain/config.yaml`)
- Recent source articles: `Inbox/` — notes from roughly the last 24-48h, to see
  what article(s) may have fed today's analysis
- Project descriptions (for context on stack/conventions): `Projets/<project>.md`
- Credentials: `/home/everyways/second-brain/pr-bot/credentials.json` — one
  GitHub token per project, scoped to that project's repo only
  (Contents + Pull requests: read/write). Read this file directly; never print
  the token values in any output, log, or commit message.

## Process

1. Read today's date and figure out which project was analyzed today
   (rotation: `today.toordinal() % len(projects)` over the `projects:` list —
   or just check which `Projets/Analyses/<today> Analyse-*.md` file exists).
2. Read that analysis note in full, plus the 1-3 most recent Inbox notes from
   the last couple of days (these are likely what informed the analysis).
3. Judge honestly: is there a **specific, well-defined, accurate** change
   worth proposing to the actual codebase — not a vague direction ("consider
   improving X"), but something you could actually implement correctly having
   looked at the real repo? If the idea is speculative, requires product
   decisions only Benoît can make, or you're not confident it's technically
   correct for this codebase, **do not open a PR** — stop here for the day.
4. If there's a real candidate:
   a. Look up that project's entry in `credentials.json` for its `clone_url`
      and `token`.
   b. Clone the repo into a scratch directory (your outputs/scratch space),
      authenticating via the token
      (`https://x-access-token:<token>@github.com/<owner>/<repo>.git`, or set
      up a repo-local git-credential file the same way as the vault repo —
      never put the token in a URL that ends up in shell history/logs
      unnecessarily; prefer the credential-file approach).
   c. Actually read the relevant parts of the codebase before touching
      anything — don't guess at file locations or conventions.
   d. Make a real, scoped, correct change on a new branch named
      `secondbrain/<YYYY-MM-DD>-<short-slug>`. Keep it small and focused on
      the one concrete idea. A docs/config/small-fix change you're fully
      confident in is much better than a large speculative feature attempt.
   e. Commit as author `second-brain bot <fauriebenoit@gmail.com>`.
   f. Push the branch, then open a **draft** PR via the GitHub API
      (`POST /repos/<owner>/<repo>/pulls` with `"draft": true`) with:
      - Title: short, specific description of the change
      - Body: 2-4 sentences explaining the change, plus a `Source:` line
        linking back to the originating article URL (from the Inbox note's
        frontmatter `url:` field) and/or the analysis note
5. If the analysis note contains an "## Opportunité business (à valider)"
   section: fact-check it. Read the cited source article (Inbox note, and fetch
   the original URL if needed), and judge whether the opportunity is accurately
   grounded in that source and plausible (not hallucinated, not a generic idea
   dressed up). Give a short verdict in your final summary — e.g. "opportunité :
   crédible, la source dit bien X" or "opportunité : à ignorer, la source ne
   parle pas de ça" — so Benoît can validate or discard it quickly. This is
   informational only: never open a PR, repo, or any external action based on
   an opportunity.
6. Log the outcome (PR opened with link, or "skipped: <short reason>") by
   appending one line to `/home/everyways/second-brain/pr-bot/pr-bot.log`
   (format: `YYYY-MM-DD HH:MM — <project> — <outcome>`; if an opportunity was
   checked, append " — opp: <verdict in a few words>").

### Special case: finances-perso

The `finances-perso` project (in rotation since 2026-07-10) has **no code, no
GitHub repo, no credentials entry**. On its day, never attempt any clone or PR.
Instead, review the analysis note for accuracy the same way as an opportunity
section (step 5): check that any advice/idea is actually grounded in the cited
source, give a short verdict in the summary, and log
"reviewed analysis (no repo): <verdict>" in pr-bot.log.

## Hard rules

- Never auto-merge. Draft PRs only.
- Never touch `main`/`master` directly — always a new branch.
- At most one PR per project per day (check `pr-bot.log` and/or open PRs on
  the repo first — if today's project already has an open PR from this bot,
  don't open a duplicate).
- If a token is expired/rejected, log the failure and stop for that project —
  don't retry with the vault's or another project's token.
- If anything is ambiguous or you're not genuinely confident, skip and log
  why. Silence (no PR) is always an acceptable, expected outcome.
