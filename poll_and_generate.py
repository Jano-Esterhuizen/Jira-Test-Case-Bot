"""
Jira Test Case Bot — polling version.

Runs on a machine INSIDE the corporate network (e.g. your work laptop via
Task Scheduler). Every run it:

  1. Queries Jira (cloud) for open tickets assigned to you
  2. Skips any ticket that already has the bot's comment
  3. Finds a GitHub PR URL in the ticket's comments/description
  4. Fetches the PR diff from the internal GitHub Enterprise Server
  5. Sends ticket + diff to the Claude API
  6. Posts the generated test cases back as a Jira comment

Configuration comes from a .env file next to this script (see .env.example).
Safe to run as often as you like — the marker comment prevents duplicates.
"""

import os
import re
import sys
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------- Config ----------
JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_AUTH = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
GITHUB_API_BASE = os.environ.get(
    "GITHUB_API_BASE", "https://git.i.mercedes-benz.com/api/v3"
).rstrip("/")
GITHUB_WEB_HOST = os.environ.get("GITHUB_WEB_HOST", "git.i.mercedes-benz.com")
GH_TOKEN = os.environ["GH_TOKEN"]
MODEL = os.environ.get("MODEL", "claude-sonnet-4-6")
JQL = os.environ.get(
    "JQL",
    'assignee = currentUser() AND statusCategory != Done AND updated >= -5d',
)
REQUIRE_PR_LINK = os.environ.get("REQUIRE_PR_LINK", "false").lower() == "true"
MARKER = "Test Strategy"
MAX_DIFF_CHARS = 60_000
MAX_OUTPUT_TOKENS = 4_000
GH_VERIFY_TLS = os.environ.get("GH_VERIFY_TLS", "true").lower() == "true"

GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
}

PR_URL_PATTERN = re.compile(
    rf"https://{re.escape(GITHUB_WEB_HOST)}/([\w.\-]+)/([\w.\-]+)/pull/(\d+)",
    re.IGNORECASE,
)


# ---------- Jira helpers ----------
def adf_to_text(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    out = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node.get("text", ""))
        for child in node.get("content", []) or []:
            out.append(adf_to_text(child))
        if node.get("type") in ("paragraph", "heading", "listItem"):
            out.append("\n")
    elif isinstance(node, list):
        for child in node:
            out.append(adf_to_text(child))
    return "".join(out)


def search_my_tickets() -> list[dict]:
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    resp = requests.get(
        url, auth=JIRA_AUTH,
        params={"jql": JQL, "fields": "summary,issuetype,description", "maxResults": 50},
        timeout=30,
    )
    if resp.status_code == 404:  # older deployments
        url = f"{JIRA_BASE_URL}/rest/api/3/search"
        resp = requests.get(
            url, auth=JIRA_AUTH,
            params={"jql": JQL, "fields": "summary,issuetype,description", "maxResults": 50},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json().get("issues", [])


def get_comments(issue_key: str) -> list[str]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"
    resp = requests.get(url, auth=JIRA_AUTH, params={"orderBy": "-created", "maxResults": 100}, timeout=30)
    resp.raise_for_status()
    return [adf_to_text(c.get("body")) for c in resp.json().get("comments", [])]


def get_acceptance_criteria(issue_key: str) -> str:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    resp = requests.get(url, auth=JIRA_AUTH, params={"expand": "names"}, timeout=30)
    if not resp.ok:
        return ""
    data = resp.json()
    parts = []
    for field_id, field_name in (data.get("names") or {}).items():
        if "acceptance" in (field_name or "").lower():
            value = data["fields"].get(field_id)
            text = adf_to_text(value).strip() if value else ""
            if text:
                parts.append(text)
    return "\n".join(parts)


def post_comment(issue_key: str, body_text: str) -> None:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": body_text}]}
            ],
        }
    }
    resp = requests.post(url, json=payload, auth=JIRA_AUTH, timeout=30)
    resp.raise_for_status()


# ---------- GitHub Enterprise helpers ----------
def fetch_pr(owner: str, repo: str, number: int) -> dict | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
    resp = requests.get(url, headers=GH_HEADERS, timeout=30, verify=GH_VERIFY_TLS)
    if not resp.ok:
        print(f"  ! Could not fetch PR {owner}/{repo}#{number} ({resp.status_code})")
        return None
    return resp.json()


def fetch_pr_diff(owner: str, repo: str, number: int) -> str:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
    headers = {**GH_HEADERS, "Accept": "application/vnd.github.v3.diff"}
    resp = requests.get(url, headers=headers, timeout=60, verify=GH_VERIFY_TLS)
    resp.raise_for_status()
    diff = resp.text
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[...diff truncated due to size...]"
    return diff


# ---------- Claude ----------
SYSTEM_PROMPT = """You are a senior QA / test automation engineer. You write precise,
practical test cases that another tester can execute directly.

Given a Jira ticket (description + acceptance criteria) and the code diff of the
linked pull request, produce a test plan with these sections:

1. SUMMARY OF CHANGE — 2-3 sentences on what changed and the risk areas, based on the diff.
2. HAPPY PATH TEST CASES
3. EDGE CASES & BOUNDARY CONDITIONS
4. NEGATIVE / ERROR-HANDLING TESTS
5. REGRESSION RISKS — existing functionality the diff could plausibly break, and what to re-test.
6. AUTOMATION CANDIDATES — which of the above are worth automating, and at what level (unit/API/UI).

Format every test case as:
  TC-<n>: <title>
  Preconditions: ...
  Steps: ...
  Expected result: ...

Ground every test case in the actual diff and acceptance criteria — do not invent
features that are not present. If the diff and the ticket contradict each other,
flag it explicitly. Output plain text (no markdown syntax), since this will be
posted as a Jira comment."""


def generate_test_cases(issue_key, summary, issue_type, description, ac, pr, diff) -> str:
    client = Anthropic()  # reads ANTHROPIC_API_KEY from env / .env

    if pr:
        pr_section = (
            f"PR #{pr['number']}: {pr.get('title', '')}\n"
            f"Branch: {pr.get('head', {}).get('ref', '')}\n"
            f"PR description:\n{pr.get('body') or '(empty)'}\n\n"
            f"--- DIFF ---\n{diff}"
        )
    else:
        pr_section = ("No linked PR was found. Base the test cases on the ticket alone "
                      "and note that the PR was unavailable.")

    user_prompt = (
        f"JIRA TICKET {issue_key} ({issue_type})\n"
        f"Summary: {summary}\n\n"
        f"Description:\n{description or '(empty)'}\n\n"
        f"Acceptance criteria:\n{ac or '(none found)'}\n\n"
        f"LINKED PULL REQUEST:\n{pr_section}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


# ---------- Main loop ----------
def process_ticket(issue: dict) -> None:
    key = issue["key"]
    fields = issue["fields"]
    summary = fields.get("summary", "")
    issue_type = (fields.get("issuetype") or {}).get("name", "")
    description = adf_to_text(fields.get("description")).strip()

    comments = get_comments(key)

    if any(MARKER in c for c in comments):
        print(f"  - {key}: 'Test Strategy' comment already exists, skipping")
        return

    pr_match = None
    for text in comments + [description]:
        m = PR_URL_PATTERN.search(text or "")
        if m:
            pr_match = m
            break

    if pr_match is None and REQUIRE_PR_LINK:
        print(f"  - {key}: no PR link yet, will retry next run")
        return

    pr, diff = None, None
    if pr_match:
        owner, repo, number = pr_match.group(1), pr_match.group(2), int(pr_match.group(3))
        print(f"  - {key}: found PR {owner}/{repo}#{number}, fetching diff...")
        pr = fetch_pr(owner, repo, number)
        if pr:
            diff = fetch_pr_diff(owner, repo, number)

    print(f"  - {key}: generating test cases...")
    ac = get_acceptance_criteria(key)
    test_cases = generate_test_cases(key, summary, issue_type, description, ac, pr, diff)

    header = f"{MARKER}\n(AI-generated from the ticket"
    if pr:
        header += f" and PR {pr.get('html_url', '')}"
        header += " — review before use)\n"
    else:
        header += " only — no PR link found at generation time; review before use)\n"
    header += "-" * 40 + "\n\n"

    post_comment(key, header + test_cases)
    print(f"  ✓ {key}: test cases posted")


def main() -> int:
    print("Polling Jira for assigned tickets...")
    try:
        issues = search_my_tickets()
    except Exception as e:
        print(f"Jira search failed: {e}")
        return 1

    print(f"Found {len(issues)} candidate ticket(s)")
    failures = 0
    for issue in issues:
        try:
            process_ticket(issue)
        except Exception as e:
            failures += 1
            print(f"  ! {issue.get('key', '?')}: error: {e}")

    print("Done.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
