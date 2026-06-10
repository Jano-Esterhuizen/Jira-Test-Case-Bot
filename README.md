# AI Test Case Bot — Polling Version (runs inside the corporate network)

Every run: finds open Jira tickets assigned to you, skips ones already
processed, pulls the PR diff from the internal GitHub Enterprise
(git.i.mercedes-benz.com), generates test cases with Claude, and posts them
as a Jira comment. Duplicate-safe — the bot's own comment is the marker.

Why polling instead of webhooks: the PR diffs live behind the firewall, so
the bot must run inside the network. Jira cloud and the Claude API are
reachable from there; the public internet can't reach the internal GitHub.

---

## Step 1 — Install Python (if not already installed)
Check: open Command Prompt, run `python --version`. If missing, install
from https://www.python.org/downloads/ (tick "Add Python to PATH" during
install) or via your company software center.

## Step 2 — Put this folder somewhere permanent
e.g. `C:\Users\YOU\jira-testcase-bot`. Then in Command Prompt:
```
cd C:\Users\YOU\jira-testcase-bot
pip install -r requirements.txt
```

## Step 3 — Create the three credentials
1. **Jira API token:** https://id.atlassian.com/manage-profile/security/api-tokens
2. **Internal GitHub token:** on git.i.mercedes-benz.com → your avatar →
   Settings → Developer settings → Personal access tokens → generate with
   `repo` scope.
   Verify it (replace token, org/repo/number with a PR you can view):
   ```
   curl -s -H "Authorization: Bearer YOURTOKEN" https://git.i.mercedes-benz.com/api/v3/user
   ```
   → should return JSON with your username.
3. **Claude API key:** https://console.anthropic.com → Settings → API keys
   (add a small credit balance under Billing first).

## Step 4 — Configure
Copy `.env.example` to `.env` (in the same folder) and fill in the values.
Never share or commit the `.env` file.

## Step 5 — Test manually
With a ticket assigned to you that has a PR link in its comments:
```
python poll_and_generate.py
```
Watch the output — it lists each ticket and what it did. Check the Jira
ticket for the "🤖 AI-generated test cases" comment.

## Step 6 — Schedule it (Windows Task Scheduler)
1. Start menu → "Task Scheduler" → Create Task (not Basic Task).
2. **General:** name `Jira Test Case Bot`; select "Run only when user is
   logged on".
3. **Triggers:** New → "At log on", tick "Repeat task every: 10 minutes",
   duration "Indefinitely".
4. **Actions:** New → Start a program →
   Program: `C:\Users\YOU\jira-testcase-bot\run_bot.bat`
   Start in: `C:\Users\YOU\jira-testcase-bot`
5. **Settings:** tick "Run task as soon as possible after a scheduled
   start is missed".
6. OK to save. Right-click the task → Run, to test it immediately.

Output is appended to `bot_log.txt` in the folder. Tail it when debugging.

## Behavior notes
- A ticket with no PR link yet is skipped and retried on every run, so the
  comment appears shortly after a dev posts the link. Set
  `REQUIRE_PR_LINK=false` in .env to generate ticket-only cases immediately.
- Already-processed tickets are recognized by the bot's marker comment —
  reruns never duplicate.
- Laptop was off? The next run catches up on everything missed.
- To regenerate for a ticket (e.g. the PR changed), delete the bot's
  comment and the next run will redo it.

## Moving to always-on later
The script is host-agnostic. If you get a repo on the internal GitHub
Enterprise with Actions, or an internal VM, copy the folder there and run
the same script on a schedule (GHE Actions `schedule:` trigger or cron).
Nothing else changes.

## Troubleshooting
- **401 from internal GitHub:** token was created on github.com instead of
  git.i.mercedes-benz.com, or expired. Re-run the Step 3 curl check.
- **TLS/certificate errors to the internal server:** corporate CA issue.
  Ask IT for the CA bundle (set env var REQUESTS_CA_BUNDLE to its path),
  or as a last resort set GH_VERIFY_TLS=false in .env.
- **Claude API unreachable:** corporate proxy may require configuration —
  set HTTPS_PROXY in .env if your company uses one, or ask IT whether
  api.anthropic.com is allowed egress.
- **Jira 401:** check JIRA_EMAIL + token pair; tokens expire if revoked.

## Cost
One ticket + one diff per generation to `claude-sonnet-4-6` — typically
well under a few cents per ticket. Polling itself costs nothing (Claude is
only called when a new ticket needs test cases).
