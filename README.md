# AI Test Case Bot

Automatically generates a focused QA test plan for your Jira tickets and posts it
back as a Jira comment.

Each run the bot:

1. Queries Jira for tickets matching your filter (by default: assigned to you,
   in a chosen status).
2. Skips any ticket it has already handled (its own comment is the marker, so it
   never double-posts).
3. Looks for a GitHub pull-request link in the ticket's comments/description.
4. Fetches that PR's code diff from the right GitHub host.
5. Sends the ticket text + diff to Claude.
6. Posts a formatted **Test Strategy** comment (happy-path, edge, negative cases,
   and automation candidates) back on the ticket.

It is a **run-once script** — every run is one polling cycle. You run it manually
or, more usefully, on a schedule (e.g. Windows Task Scheduler).

---

## Why "polling" and why it runs locally

The PR diffs live on GitHub servers that may sit **behind a corporate firewall**
(e.g. an internal GitHub Enterprise Server). A public webhook can't reach inside
the network, so instead the bot runs on a machine **inside** the network and polls
Jira on a timer. Jira Cloud and the Claude API are reachable from there.

---

## How it works (flow)

```
Jira (your JQL)  ──►  for each ticket  ──►  already has "Test Strategy"? ──► skip
                                              │ no
                                              ▼
                              find PR link in comments/description
                                              │
                                  ┌───────────┴───────────┐
                              found                     not found
                                  │                         │
                       fetch diff from the         REQUIRE_PR_LINK?
                       matching GitHub host         ├ true  → skip, retry next run
                                  │                 └ false → generate from ticket only
                                  ▼
                          Claude generates plan
                                  │
                                  ▼
                     post "Test Strategy" comment to Jira
```

---

## Prerequisites

- **Python 3.10+** (the code uses `str | None` type hints). Check with
  `python --version`. Install from <https://www.python.org/downloads/> (tick
  *Add Python to PATH*) or via your company software center.
- Network access from the run machine to: your Jira Cloud site, every GitHub host
  whose PRs you reference, and `api.anthropic.com`.

---

## Step 1 — Get the code and install dependencies

Put the folder somewhere permanent, e.g. `C:\Users\YOU\jira-testcase-bot`, then:

```powershell
cd C:\Users\YOU\jira-testcase-bot
pip install -r requirements.txt
```

Dependencies (`requirements.txt`): `requests`, `anthropic`, `python-dotenv`.

## Step 2 — Create your credentials

You need a Jira token, **one GitHub token per GitHub host** you reference, and a
Claude API key.

### a) Jira API token
<https://id.atlassian.com/manage-profile/security/api-tokens> → *Create API token*.
Pair it with the email of your Jira account.

### b) GitHub token(s) — one per host
This project supports **multiple GitHub hosts at once**, because tickets often link
PRs on different systems. Tokens are **per-instance**: a token minted on one host
will **not** work on another. Create one on each host you use.

On the host's web UI: *your avatar → Settings → Developer settings → Personal
access tokens* → generate a token with the **`repo`** scope (read access).

**Two host types you may encounter, and their REST API base URL:**

| GitHub type | Web host example | REST API base (`GH_API_*`) |
|-------------|------------------|----------------------------|
| Enterprise **Server** (self-hosted) | `git.i.mercedes-benz.com` | `https://git.i.mercedes-benz.com/api/v3` |
| Enterprise **Cloud**, data residency | `mercedes-benz.ghe.com` | `https://api.mercedes-benz.ghe.com` |
| Public GitHub | `github.com` | `https://api.github.com` |

> **SAML SSO:** if a host enforces single sign-on, you must **authorize the token
> for each organization** after creating it. On the token page click
> **Configure SSO** and authorize the org that owns the repo (otherwise API calls
> return **401**, even though the token is valid). See *Troubleshooting*.

**Verify each token** (replace the token and host):

```powershell
# Enterprise Server
curl.exe -s -o NUL -w "%{http_code}`n" -H "Authorization: Bearer YOURTOKEN" https://git.i.mercedes-benz.com/api/v3/user
# Enterprise Cloud (ghe.com)
curl.exe -s -o NUL -w "%{http_code}`n" -H "Authorization: Bearer YOURTOKEN" https://api.mercedes-benz.ghe.com/user
```

`200` = good. `401` = token not valid / not SSO-authorized for that host.

### c) Claude API key
<https://console.anthropic.com> → *Settings → API keys*. Add a small credit
balance under *Billing* first.

## Step 3 — Configure `.env`

Copy the template and fill in real values:

```powershell
Copy-Item .env.example .env
```

**Never commit or share `.env`.** It is already listed in `.gitignore`, so it stays
local; `.env.example` is the shareable template.

### `.env` reference

```ini
# --- Jira (Atlassian cloud) ---
JIRA_BASE_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...

# --- GitHub hosts: one numbered block per host (GH_HOST_1, GH_HOST_2, ...) ---
GH_HOST_1=git.i.mercedes-benz.com
GH_API_1=https://git.i.mercedes-benz.com/api/v3
GH_TOKEN_1=token-created-on-that-server
GH_VERIFY_TLS_1=true

GH_HOST_2=mercedes-benz.ghe.com
GH_API_2=https://api.mercedes-benz.ghe.com
GH_TOKEN_2=token-created-on-ghe.com
GH_VERIFY_TLS_2=true

# --- Claude API ---
ANTHROPIC_API_KEY=sk-ant-...
MODEL=claude-sonnet-4-6

# --- Behavior ---
JQL=project = ABA AND assignee = currentUser() AND issuetype in (Bug, Story) AND status = "READY FOR INT TESTING"
REQUIRE_PR_LINK=false
```

| Variable | Required | What it does |
|----------|----------|--------------|
| `JIRA_BASE_URL` | yes | Your Jira Cloud URL. |
| `JIRA_EMAIL` / `JIRA_API_TOKEN` | yes | Jira auth. `currentUser()` in the JQL resolves to this account. |
| `GH_HOST_n` | yes (≥1) | Web hostname as it appears in PR links. Add `_1`, `_2`, … per host. |
| `GH_API_n` | yes | REST API base for that host (see table above). |
| `GH_TOKEN_n` | yes | Token valid for that host. |
| `GH_VERIFY_TLS_n` | no (default `true`) | Set `false` only for an untrusted corporate cert. |
| `ANTHROPIC_API_KEY` | yes | Claude API key. |
| `MODEL` | no | Defaults to `claude-sonnet-4-6`. |
| `JQL` | no | Which tickets to scan (see below). |
| `REQUIRE_PR_LINK` | no | `false` (default) = generate even without a PR link; `true` = wait for one. |

The bot reads `GH_HOST_1..20`. Each must have a matching `GH_API_n` and
`GH_TOKEN_n` or it is skipped with a warning. At least one valid host is required.

### Choosing your `JQL`

`JQL` is a Jira query; every match is processed each run. Examples:

| Goal | JQL |
|------|-----|
| Your open tickets, recently updated | `assignee = currentUser() AND statusCategory != Done AND updated >= -5d` |
| Bugs + Stories in a specific status | `project = ABA AND assignee = currentUser() AND issuetype in (Bug, Story) AND status = "READY FOR INT TESTING"` |
| A whole team board | `project = ABA AND status = "READY FOR INT TESTING"` |

> **Tip:** build and test the query in Jira's **Filters → Advanced search (JQL)**
> first. Issue-type and status **names must match exactly** (e.g. `Story` vs
> `"User Story"`); Jira's autocomplete shows the valid values. Paste the working
> query verbatim into `.env`.

## Step 4 — Run it manually

```powershell
python poll_and_generate.py
```

The console lists each candidate ticket and what happened (found PR / generated /
skipped / errors). Open a processed ticket in Jira to see the **Test Strategy**
comment.

## Step 5 — Schedule it (Windows Task Scheduler)

`run_bot.bat` runs the script and appends output to `bot_log.txt`.

1. Start menu → **Task Scheduler** → **Create Task** (not *Basic Task*).
2. **General:** name `Jira Test Case Bot`; *Run only when user is logged on*.
3. **Triggers:** New → *At log on*; tick *Repeat task every: 10 minutes*,
   for a duration of *Indefinitely*.
4. **Actions:** New → *Start a program* →
   - Program: `C:\Users\YOU\jira-testcase-bot\run_bot.bat`
   - Start in: `C:\Users\YOU\jira-testcase-bot`
5. **Settings:** tick *Run task as soon as possible after a scheduled start is
   missed* (so it catches up if the laptop was off).
6. Save. Right-click the task → **Run** to test immediately. Tail `bot_log.txt`
   when debugging.

---

## What the generated comment looks like

The plan is deliberately **short and prioritized** — at most ~3 cases per section,
focused on the highest-risk scenarios tied to the diff. It is posted as formatted
Jira content (real headings, bullets, bold):

> # Test Strategy
> *AI-generated from the ticket and PR <link> — review before use.*
> ───
> ## Summary of Change
> ## Happy Path → TC-1, …
> ## Edge Cases → TC-2, …
> ## Negative / Error Handling → TC-3, …
> ## Automation Candidates

Each test case lists **Preconditions**, **Steps**, and **Expected** result.

---

## Behavior notes

- **Trigger is your filter:** when a ticket starts matching the `JQL`, the next
  cycle picks it up.
- **Duplicate-safe:** every comment starts with the phrase **"Test Strategy"**. If
  any comment on a ticket already contains it, the bot skips that ticket — reruns
  never double-post.
- **No PR link?** With `REQUIRE_PR_LINK=false` (default) it generates from the
  description/acceptance criteria alone and notes that in the comment. With `true`
  it skips and retries on later runs until a PR link appears.
- **Multiple hosts:** the bot detects which host each PR link uses and picks the
  matching API base + token automatically.
- **Regenerate a ticket:** delete the bot's existing "Test Strategy" comment; the
  next run redoes it with the latest PR/diff.
- **Catch-up:** if the machine was off, the next run processes everything still
  matching the filter.
- **Large diffs** are truncated at 60,000 characters before being sent to Claude.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| **401** fetching a PR | Token not valid for that host, expired, or — most common — **not SSO-authorized**. On the token page click *Configure SSO* and authorize the org that owns the repo. Check the `X-GitHub-SSO` response header for the authorize URL. |
| **403** fetching a PR | Token authorized but missing the `repo` scope, or no access to that specific repo. |
| **404** fetching a PR | Wrong `GH_API_n` for the host type (Server needs `/api/v3`; Cloud uses `api.<subdomain>.ghe.com`), or the token can't see that repo. |
| PR link **not detected** | The link's host isn't configured. Add a `GH_HOST_n` block for it. |
| **TLS / certificate** error | Corporate CA not trusted. Set `REQUESTS_CA_BUNDLE` to the CA bundle path (ask IT), or as a last resort set `GH_VERIFY_TLS_n=false`. |
| **Jira 401** | Wrong `JIRA_EMAIL`/`JIRA_API_TOKEN` pair, or the token was revoked. |
| **JQL error** (no tickets / red error in Jira) | An issue-type or status name doesn't match exactly. Test the query in Jira's Advanced search and copy the working text. |
| **Claude API unreachable** | Corporate proxy — set `HTTPS_PROXY` in the environment, or ask IT whether `api.anthropic.com` egress is allowed. |

---

## Security

- `.env` holds live secrets (Jira token, GitHub tokens, Claude key). It is
  git-ignored — keep it that way; share `.env.example` instead.
- Treat any secret that leaves your machine as compromised — **rotate it**.
- Confirm `.env` was never committed: `git log --all --full-history -- .env`
  should return nothing.

---

## Cost

One ticket + one diff per generation to `claude-sonnet-4-6` — typically well under
a few cents per ticket. Polling itself costs nothing; Claude is only called when a
new matching ticket needs a plan.

---

## Moving to always-on later

The script is host-agnostic. To run it server-side, copy the folder to an internal
VM or a repo with CI that can reach the GitHub host(s), and run it on a schedule
(cron, or a CI `schedule:` trigger). Nothing in the code changes — only where it
runs and how it's triggered.

---

## File overview

| File | Purpose |
|------|---------|
| `poll_and_generate.py` | The bot. |
| `.env.example` | Config template (committed). |
| `.env` | Your real config (git-ignored, never commit). |
| `.gitignore` | Keeps `.env`, caches, etc. out of git. |
| `requirements.txt` | Python dependencies. |
| `run_bot.bat` | Wrapper for Task Scheduler; logs to `bot_log.txt`. |
