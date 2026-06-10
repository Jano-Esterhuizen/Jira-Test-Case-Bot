# AI Test Case Generator — Setup Guide
### (Bot hosted on your PERSONAL GitHub account, reading PRs with your WORK account)

Generates test cases as a Jira comment whenever a ticket is assigned to you,
using the ticket description/AC + the GitHub PR linked in the comments.

Flow: Jira assignment → Jira Automation webhook → GitHub Action (in your
PERSONAL repo) → reads the team's PR using your WORK token → Claude API
→ comment posted back on the Jira ticket.

Approved by team lead on: ____________ (fill in — future-you will thank you)

---

## The two-token model (read this first)

| Token | Created on | Used by | Purpose |
|-------|-----------|---------|---------|
| **Personal PAT** | Your personal GitHub account | The Jira Automation rule | Trigger the workflow in your personal repo |
| **Work PAT** | Your work GitHub account | The script (as `GH_PAT` secret) | Read the team's PR diffs |

Plus a Jira API token and a Claude API key. Four credentials total.

---

## Step 1 — Jira API token
Log into Jira with your work account, then go to
https://id.atlassian.com/manage-profile/security/api-tokens
→ Create API token → name it `testcase-bot` → copy it (shown only once).

## Step 2 — Claude API key
Go to https://console.anthropic.com (separate from Claude.ai). Add a small
credit balance under Billing, then Settings → API keys → Create key → copy it.

## Step 3 — Create the bot repo on your PERSONAL account
1. Log into your **personal** GitHub → "+" → New repository →
   name `jira-testcase-bot` → **Private** → initialize with README → Create.
2. Push this folder's contents:
   ```bash
   git init
   git add .
   git commit -m "Add Jira test case bot"
   git branch -M main
   git remote add origin https://github.com/PERSONALUSERNAME/jira-testcase-bot.git
   git push -u origin main --force
   ```
   (Or upload via web UI — the workflow must land at exactly
   `.github/workflows/generate-test-cases.yml` on `main`.)

## Step 4 — Personal PAT (the "doorbell" token)
On your **personal** account: Settings → Developer settings →
Personal access tokens → Tokens (classic) → Generate new token (classic).
- Name: `jira-dispatch`
- Scope: tick **repo**
- Generate and copy (`ghp_...`). No SSO step needed — it only touches your
  own repo. This one goes into the Jira rule in Step 7.

## Step 5 — Work PAT (the "PR reader" token)
Log into your **work** GitHub account (the one you view PRs with):
Settings → Developer settings → Personal access tokens → Tokens (classic)
→ Generate new token (classic).
- Name: `testcase-bot-pr-read`
- Scope: tick **repo**
- Generate and copy.
- **If a "Configure SSO" button appears** next to the token in the list:
  click it → Authorize the company org → complete the PingID login.
  If no button appears, skip this — verify in Step 5b either way.

### Step 5b — Verify the work token (do not skip)
Pick any PR you can view in the browser and run:
```bash
curl -s -H "Authorization: Bearer ghp_WORKTOKEN" \
  -H "Accept: application/vnd.github.v3.diff" \
  https://api.github.com/repos/ORG/REPO/pulls/123
```
- Diff comes back → token works, continue.
- 404 / SAML error → the SSO authorization in Step 5 didn't take; redo it.
- If the team's PRs live on a `github.yourcompany.com` address (self-hosted
  Enterprise Server) rather than github.com, STOP — the script needs a small
  API-URL change and cloud runners may not reach it. Get help before continuing.

## Step 6 — Add the five secrets to your PERSONAL bot repo
Your bot repo → Settings → Secrets and variables → Actions →
New repository secret:

| Secret              | Value                                                  |
|---------------------|--------------------------------------------------------|
| `JIRA_BASE_URL`     | e.g. `https://yourcompany.atlassian.net` (no trailing slash) |
| `JIRA_EMAIL`        | Your work Jira login email                             |
| `JIRA_API_TOKEN`    | From Step 1                                            |
| `ANTHROPIC_API_KEY` | From Step 2                                            |
| `GH_PAT`            | The WORK token from Step 5                             |

## Step 7 — Create the Jira Automation rule
Jira → your project → Project settings → Automation → Create rule:
1. **Trigger:** "Issue assigned"
2. **Condition:** Issue fields condition → Assignee → equals → you
   (optional second condition: Issue type equals Bug)
3. **Action:** "Send web request"
   - URL: `https://api.github.com/repos/PERSONALUSERNAME/jira-testcase-bot/dispatches`
   - Method: POST
   - Headers (mark Authorization as hidden):
     - `Authorization`: `Bearer ghp_PERSONALTOKEN` ← the Step 4 token
     - `Accept`: `application/vnd.github+json`
   - Body → Custom data:
     ```json
     {
       "event_type": "generate-test-cases",
       "client_payload": { "issue_key": "{{issue.key}}" }
     }
     ```
4. Name it and enable.

**Optional second rule** (recommended, since devs often link the PR after
assignment): trigger "Comment added" + conditions "Comment contains
`github.com`" and "Assignee = you" → identical web request action.

## Step 8 — End-to-end test
Assign yourself a ticket that already has a PR link in its comments:
1. Jira → Automation → rule → **Audit log**: web request succeeded
   (GitHub returns 204). 401/403 = personal token problem; 404 = URL typo.
2. Personal repo → **Actions** tab: a run appears within seconds. Open it
   and watch the logs — it prints each stage.
3. The ticket: "🤖 AI-generated test cases" comment lands in ~30–60 seconds.

---

## How the PR is found
The script scans the ticket's comments (newest first) and description for a
GitHub PR URL like `https://github.com/org/repo/pull/482` — your team's
convention. The URL tells it which repo to read; the work PAT provides
access. No link yet → it posts ticket-only test cases and says so.

## Troubleshooting
- **Workflow logs: 404 fetching the PR** → the work token lost (or never
  had) SSO authorization. Note: PingID/SAML resets (password change, org
  security event) can silently revoke it. Re-do Step 5's SSO authorization
  and the Step 5b curl check.
- **Rule fires, no workflow run** → workflow file must be on `main` of the
  bot repo; `event_type` must exactly equal `generate-test-cases`.
- **Jira fetch fails** → check `JIRA_BASE_URL` (no trailing slash) and the
  Jira token.
- **Token expiry** → calendar-remind yourself of both PATs' expiry dates.
  Renewing the personal one = update the Jira rule header. Renewing the
  work one = update the `GH_PAT` secret.

## Cost
One ticket + one diff per run to `claude-sonnet-4-6` — typically well under
a few cents per ticket. Pricing:
https://docs.claude.com/en/docs/about-claude/pricing
