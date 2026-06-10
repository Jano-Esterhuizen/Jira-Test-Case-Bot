# AI Test Case Generator — Setup Guide

Generates test cases as a Jira comment whenever a ticket is assigned to you,
using the ticket description/AC + the GitHub PR linked in the comments.

Flow: Jira assignment → Jira Automation webhook → GitHub Action (in YOUR
personal repo) → reads the team's PR via API → Claude API → Jira comment

You do NOT need write access to the team's code repo — read access (being
able to view PRs) is enough. Note: the company GitHub org uses SAML SSO
(PingID), so Step 4 (token SSO authorization) is mandatory.

---

## Step 1 — Get your Jira API token
Go to https://id.atlassian.com/manage-profile/security/api-tokens
→ Create API token → name it `testcase-bot` → copy it (shown only once).

## Step 2 — Get your Claude API key
Go to https://console.anthropic.com (separate from Claude.ai).
Add a small credit balance under Billing, then Settings → API keys →
Create key → copy it.

## Step 3 — Create your bot repo and add these files
1. GitHub → "+" → New repository → name `jira-testcase-bot` → **Private**
   → initialize with README → Create.
2. Push this folder's contents to it:
   ```bash
   git init
   git add .
   git commit -m "Add Jira test case bot"
   git branch -M main
   git remote add origin https://github.com/YOURUSERNAME/jira-testcase-bot.git
   git push -u origin main --force
   ```
   (Or upload via the web UI — ensure the workflow lands at exactly
   `.github/workflows/generate-test-cases.yml` on `main`.)

## Step 4 — Create the GitHub token and authorize it for SSO (critical)
1. GitHub → profile picture → Settings → Developer settings →
   Personal access tokens → **Tokens (classic)** → Generate new token (classic).
2. Name: `jira-testcase-bot`. Scope: tick **repo**. Generate and copy
   (starts with `ghp_`).
3. **SSO authorization:** back on the tokens list, click **Configure SSO**
   next to the new token → **Authorize** beside the company org. This
   bounces you through the PingID login once. Without this, the token
   cannot read the org's PRs even though your browser can.
   - If the Authorize button is missing/greyed out, the org has disabled
     classic PAT authorization — ask your GitHub org admin to allow it,
     or request a fine-grained PAT through the org's approval flow.

## Step 5 — Verify the token can read a PR (before touching Jira)
Pick any PR you can view in the browser and run:
```bash
curl -s -H "Authorization: Bearer ghp_YOURTOKEN" \
  -H "Accept: application/vnd.github.v3.diff" \
  https://api.github.com/repos/ORG/REPO/pulls/123
```
- Diff comes back → token is good, continue.
- 404 or a SAML enforcement message → redo the SSO authorization in Step 4.

## Step 6 — Add the five secrets to your bot repo
Your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret              | Value                                              |
|---------------------|----------------------------------------------------|
| `JIRA_BASE_URL`     | e.g. `https://yourcompany.atlassian.net` (no trailing slash) |
| `JIRA_EMAIL`        | Your Jira login email                              |
| `JIRA_API_TOKEN`    | From Step 1                                        |
| `ANTHROPIC_API_KEY` | From Step 2                                        |
| `GH_PAT`            | The SSO-authorized token from Step 4               |

## Step 7 — Create the Jira Automation rule
Jira → your project → Project settings → Automation → Create rule:
1. **Trigger:** "Issue assigned"
2. **Condition:** Issue fields condition → Assignee → equals → you
   (optional: Issue type equals Bug)
3. **Action:** "Send web request"
   - URL: `https://api.github.com/repos/YOURUSERNAME/jira-testcase-bot/dispatches`
   - Method: POST
   - Headers (mark the first one hidden):
     - `Authorization`: `Bearer ghp_YOURTOKEN` (same token from Step 4)
     - `Accept`: `application/vnd.github+json`
   - Body → Custom data:
     ```json
     {
       "event_type": "generate-test-cases",
       "client_payload": { "issue_key": "{{issue.key}}" }
     }
     ```
4. Name the rule and enable it.

Optional second rule for late PR links: trigger "Comment added" + conditions
"comment contains github.com" and "assignee = you", same web request action.

## Step 8 — End-to-end test
Assign yourself a ticket that has a PR link in its comments, then check:
1. Jira → Automation → rule → **Audit log**: web request succeeded
   (GitHub returns 204). 401/403 = token problem; 404 = URL typo.
2. Your bot repo → **Actions** tab: a run appears within seconds; open it
   to watch the logs.
3. The ticket: the "🤖 AI-generated test cases" comment lands in ~30–60s.

---

## How the PR is found
The script scans the ticket's comments (newest first) and the description
for a GitHub PR URL like `https://github.com/org/repo/pull/482`. The URL
tells it which repo to read; your SSO-authorized PAT provides the access.
No link in the comments yet → it posts ticket-only test cases and says so.

## Troubleshooting
- **Workflow logs show 404 fetching the PR:** the PAT lost or never had SSO
  authorization. Re-run Step 4.3 and the Step 5 curl check. Note: SSO
  authorization can be silently revoked by password resets or org security
  events — this is the first thing to check if the bot breaks after weeks
  of working.
- **Rule fires but no workflow run:** workflow file must be on `main` of the
  bot repo, and `event_type` must exactly equal `generate-test-cases`.
- **Jira fetch fails:** check `JIRA_BASE_URL` (no trailing slash) and that
  the Jira token is valid.
- **Token expiry:** set a calendar reminder for the PAT's expiry date;
  renewing means updating both the `GH_PAT` secret and the Jira rule header.

## Cost
One ticket + one diff per run to `claude-sonnet-4-6` — typically well under
a few cents. Pricing: https://docs.claude.com/en/docs/about-claude/pricing
