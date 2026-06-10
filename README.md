# Jira-Test-Case-Bot

Generates test cases in a Jira comment whenever a ticket is assigned to you,
using the ticket description + the GitHub PR linked in the comments.

Flow: Jira assignment → Jira Automation webhook → GitHub Action (in YOUR repo)
→ reads the team's PR via API → Claude API → Jira comment

You do NOT need write access to the team's code repo. Read access (the ability
to view PRs) is enough — the bot lives entirely in a repo you own.

---

## 1. Create your own bot repo

On GitHub: click "+" → New repository.
- Name: `jira-testcase-bot` (anything works)
- Visibility: **Private**
- Initialize with a README so the default branch exists.

## 2. Add the two files (via the web UI — no git needed)

In your new repo, click "Add file" → "Create new file":

1. Type the filename `.github/workflows/generate-test-cases.yml`
   (typing the slashes creates the folders) and paste in the workflow file.
2. Repeat with `scripts/generate_test_cases.py` and paste in the script.

Commit both directly to `main`.

## 3. Create one GitHub Personal Access Token (does double duty)

GitHub → profile picture → Settings → Developer settings →
Personal access tokens → **Tokens (classic)** → Generate new token (classic).

- Name: `jira-testcase-bot`
- Scope: tick **repo**
- Generate, and copy the token (starts with `ghp_`).

This single token is used for two things:
- Jira uses it to trigger your workflow (write access to YOUR repo)
- The script uses it to read PR diffs from the team's repo (your existing
  read/view permission)

⚠️ If your company's GitHub org uses SSO, open the token's settings and click
**Configure SSO → Authorize** for the org — otherwise it can't read org repos.

## 4. Add the secrets to YOUR bot repo

Your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret              | Value                                                                 |
|---------------------|-----------------------------------------------------------------------|
| `JIRA_BASE_URL`     | e.g. `https://yourcompany.atlassian.net` (no trailing slash)          |
| `JIRA_EMAIL`        | Your Jira login email                                                 |
| `JIRA_API_TOKEN`    | From https://id.atlassian.com/manage-profile/security/api-tokens     |
| `ANTHROPIC_API_KEY` | From https://console.anthropic.com (Settings → API keys)              |
| `GH_PAT`            | The classic token from step 3                                         |

## 5. Create the Jira Automation rule

Jira → your project → Project settings → Automation → Create rule:

1. **Trigger:** "Issue assigned"
2. **Condition:** Issue fields condition → Assignee equals → you
   (optional second condition: Issue type equals Bug)
3. **Action:** "Send web request"
   - URL: `https://api.github.com/repos/YOURUSERNAME/jira-testcase-bot/dispatches`
   - Method: POST
   - Headers:
     - `Authorization`: `Bearer ghp_xxxxx` (the same token from step 3 — mark it hidden)
     - `Accept`: `application/vnd.github+json`
   - Body → Custom data:
     ```json
     {
       "event_type": "generate-test-cases",
       "client_payload": { "issue_key": "{{issue.key}}" }
     }
     ```
4. Name the rule and turn it on.

If you can't see project Automation settings, ask a Jira admin to enable it
for you — this is the only step that might need someone else.

## 6. How the PR is found

1. The script scans the ticket's **comments** (newest first) and description
   for a GitHub PR URL like `https://github.com/org/repo/pull/482` — your
   team's convention. The URL tells the script exactly which repo to read,
   and your PAT provides the read access.
2. Fallback: searching for the issue key in PR titles/branches — note this
   fallback only searches the bot repo itself, so in this hosting model the
   comment link is effectively required. No link → ticket-only test cases.

## 7. Test the chain

Assign yourself a ticket that has a PR link in the comments, then check:

1. Jira → Automation → your rule → **Audit log** — web request should succeed
   (GitHub returns 204). 401/403 = token problem; 404 = typo in the URL.
2. Your bot repo → **Actions** tab — a run should appear within seconds.
   Open it to see the script's progress logs.
3. The Jira ticket — the "🤖 AI-generated test cases" comment appears in
   ~30–60 seconds.

## Troubleshooting

- **Workflow runs but "could not fetch PR (404)":** your PAT can't see the
  team's repo. Check the SSO authorization in step 3, and confirm you can
  open that PR in your browser while logged in as the same account.
- **No PR found:** there's no GitHub PR URL in the ticket's comments yet.
  Consider a second Jira rule with trigger "Comment added" + condition
  "comment contains github.com" so the bot fires when the link lands.
- **Rule fires, no workflow run:** the workflow file must be on the default
  branch of the bot repo, and event_type in the rule body must exactly match
  `generate-test-cases`.

## Cost note

Each run sends one ticket + one diff to `claude-sonnet-4-6` — typically a
fraction of a cent to a few cents. Pricing:
https://docs.claude.com/en/docs/about-claude/pricing
