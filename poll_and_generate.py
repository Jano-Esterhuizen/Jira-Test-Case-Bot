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
MODEL = os.environ.get("MODEL", "claude-sonnet-4-6")
JQL = os.environ.get(
    "JQL",
    'assignee = currentUser() AND statusCategory != Done AND updated >= -5d',
)
REQUIRE_PR_LINK = os.environ.get("REQUIRE_PR_LINK", "false").lower() == "true"
MARKER = "Test Strategy"
MAX_DIFF_CHARS = 60_000
MAX_OUTPUT_TOKENS = 4_000


# ---------- GitHub hosts (one or more) ----------
# Each host is configured with a numbered set of env vars:
#   GH_HOST_<n>       web hostname that appears in PR links (e.g. git.i.mercedes-benz.com)
#   GH_API_<n>        REST API base for that host
#                       Enterprise Server : https://<host>/api/v3
#                       Enterprise Cloud  : https://api.<subdomain>.ghe.com   (ghe.com)
#   GH_TOKEN_<n>      access token valid for that host
#   GH_VERIFY_TLS_<n> "false" only if the host uses an untrusted corporate cert (default true)
def _load_github_hosts() -> dict:
    hosts = {}
    for n in range(1, 21):  # support up to 20 hosts
        host = os.environ.get(f"GH_HOST_{n}")
        if not host:
            continue
        api = os.environ.get(f"GH_API_{n}")
        token = os.environ.get(f"GH_TOKEN_{n}")
        if not api or not token:
            print(f"  ! GH_HOST_{n}={host} is missing GH_API_{n} or GH_TOKEN_{n}, skipping")
            continue
        hosts[host.lower()] = {
            "api": api.rstrip("/"),
            "headers": {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            "verify_tls": os.environ.get(f"GH_VERIFY_TLS_{n}", "true").lower() != "false",
        }
    return hosts


GITHUB_HOSTS = _load_github_hosts()
if not GITHUB_HOSTS:
    sys.exit(
        "No GitHub hosts configured. Set GH_HOST_1 / GH_API_1 / GH_TOKEN_1 "
        "(and GH_HOST_2, ... for more) in your .env. See .env.example."
    )

_HOSTS_ALT = "|".join(re.escape(h) for h in GITHUB_HOSTS)
PR_URL_PATTERN = re.compile(
    rf"https://({_HOSTS_ALT})/([\w.\-]+)/([\w.\-]+)/pull/(\d+)",
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


def _inline_nodes(text: str) -> list[dict]:
    """Turn a line of text into ADF text nodes, honouring **bold** markup."""
    nodes = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        node = {"type": "text", "text": part}
        if i % 2 == 1:  # captured group = bold
            node["marks"] = [{"type": "strong"}]
        nodes.append(node)
    return nodes or [{"type": "text", "text": text or " "}]


def md_to_adf(md_text: str) -> list[dict]:
    """Convert a constrained Markdown subset to a list of ADF block nodes.

    Supported: #/##/### headings, '- ' bullets, '1. ' numbered lists,
    **bold** inline, and plain paragraphs. Blank lines separate blocks.
    """
    blocks: list[dict] = []
    bullets: list[dict] = []
    ordered: list[dict] = []

    def flush():
        nonlocal bullets, ordered
        if bullets:
            blocks.append({"type": "bulletList", "content": bullets})
            bullets = []
        if ordered:
            blocks.append({"type": "orderedList", "attrs": {"order": 1}, "content": ordered})
            ordered = []

    def list_item(text):
        return {"type": "listItem",
                "content": [{"type": "paragraph", "content": _inline_nodes(text)}]}

    for raw in md_text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        b = re.match(r"^[-*]\s+(.*)$", line)
        o = re.match(r"^\d+[.)]\s+(.*)$", line)
        if h:
            flush()
            blocks.append({"type": "heading",
                           "attrs": {"level": min(len(h.group(1)), 6)},
                           "content": _inline_nodes(h.group(2))})
        elif b:
            if ordered:
                flush()
            bullets.append(list_item(b.group(1)))
        elif o:
            if bullets:
                flush()
            ordered.append(list_item(o.group(1)))
        else:
            flush()
            blocks.append({"type": "paragraph", "content": _inline_nodes(line)})
    flush()
    return blocks


def post_comment(issue_key: str, content: list[dict]) -> None:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"
    payload = {"body": {"type": "doc", "version": 1, "content": content}}
    resp = requests.post(url, json=payload, auth=JIRA_AUTH, timeout=30)
    resp.raise_for_status()


# ---------- GitHub Enterprise helpers ----------
def fetch_pr(cfg: dict, owner: str, repo: str, number: int) -> dict | None:
    url = f"{cfg['api']}/repos/{owner}/{repo}/pulls/{number}"
    resp = requests.get(url, headers=cfg["headers"], timeout=30, verify=cfg["verify_tls"])
    if not resp.ok:
        print(f"  ! Could not fetch PR {owner}/{repo}#{number} ({resp.status_code})")
        return None
    return resp.json()


def fetch_pr_diff(cfg: dict, owner: str, repo: str, number: int) -> str:
    url = f"{cfg['api']}/repos/{owner}/{repo}/pulls/{number}"
    headers = {**cfg["headers"], "Accept": "application/vnd.github.v3.diff"}
    resp = requests.get(url, headers=headers, timeout=60, verify=cfg["verify_tls"])
    resp.raise_for_status()
    diff = resp.text
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[...diff truncated due to size...]"
    return diff


# ---------- Claude ----------
SYSTEM_PROMPT = """You are a senior QA / test automation engineer. You write precise,
practical test cases that another tester can execute directly.

You are given a Jira ticket (description + acceptance criteria) and the code diff of
the linked pull request. Produce a SHORT, high-signal test plan.

BE SELECTIVE — this is the most important rule:
- Include ONLY the most critical and relevant test cases. At most 3 per section,
  and fewer if the change is small. A focused plan a tester will actually finish
  beats an exhaustive one they abandon.
- Prioritise high-risk, high-impact scenarios tied directly to what changed in the
  diff. Skip trivial, obvious, or low-value checks.
- Quality and relevance over coverage.

Ground every test case in the actual diff and acceptance criteria — do not invent
features that are not present. If the diff and the ticket contradict each other,
flag it explicitly.

OUTPUT FORMAT — use this exact Markdown structure (it is rendered into a Jira
comment, so the headings and bullets matter). Do NOT use tables or code blocks.
Number test cases sequentially (TC-1, TC-2, ...) across the whole plan.

## Summary of Change
2-3 sentences on what changed and the main risk areas.

## Happy Path
### TC-1: <short title>
- **Preconditions:** ...
- **Steps:** 1) ... 2) ... 3) ...
- **Expected:** ...

## Edge Cases
### TC-2: <short title>
- **Preconditions:** ...
- **Steps:** 1) ... 2) ...
- **Expected:** ...

## Negative / Error Handling
### TC-3: <short title>
- **Preconditions:** ...
- **Steps:** 1) ... 2) ...
- **Expected:** ...

## Automation Candidates
- **TC-<n>:** <why it is worth automating, and at what level — unit / API / UI>

Keep each section to its few most important cases. Use **bold** for field labels
exactly as shown above."""


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
        host = pr_match.group(1).lower()
        owner, repo, number = pr_match.group(2), pr_match.group(3), int(pr_match.group(4))
        cfg = GITHUB_HOSTS[host]
        print(f"  - {key}: found PR {host}/{owner}/{repo}#{number}, fetching diff...")
        pr = fetch_pr(cfg, owner, repo, number)
        if pr:
            diff = fetch_pr_diff(cfg, owner, repo, number)

    print(f"  - {key}: generating test cases...")
    ac = get_acceptance_criteria(key)
    test_cases = generate_test_cases(key, summary, issue_type, description, ac, pr, diff)

    if pr:
        note = f"AI-generated from the ticket and PR {pr.get('html_url', '')} — review before use."
    else:
        note = "AI-generated from the ticket only — no PR link found at generation time; review before use."

    content = [
        {"type": "heading", "attrs": {"level": 1},
         "content": [{"type": "text", "text": MARKER}]},
        {"type": "paragraph",
         "content": [{"type": "text", "text": note, "marks": [{"type": "em"}]}]},
        {"type": "rule"},
        *md_to_adf(test_cases),
    ]
    post_comment(key, content)
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
