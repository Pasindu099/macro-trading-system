"""Generate a LinkedIn post draft from recent git commits.

Scans commits since the last run, picks the most post-worthy update,
and uses Claude to write a recruiter-friendly LinkedIn draft.

Usage:
    python scripts/linkedin_post.py

Output: data/linkedin/drafts/YYYY-MM-DD.md

Run weekly after notable work sessions. Review the draft, then paste to LinkedIn.
Requires ANTHROPIC_API_KEY in your .env or environment.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
TRACKER_PATH = ROOT / "data" / "linkedin" / "tracker.json"
DRAFTS_DIR = ROOT / "data" / "linkedin" / "drafts"
CONTEXT_PATH = Path(__file__).parent / "post_context.md"

NOTABLE_PREFIXES = ("feat:", "fix:", "refactor:", "perf:", "add:", "improve:")
SKIP_PREFIXES = ("chore:", "docs:", "style:", "wip:", "typo:", "merge:", "bump:")

MAX_DIFF_CHARS = 2500  # per commit, keeps total prompt within ~8k tokens


# ── tracker ─────────────────────────────────────────────────────────────────

def load_tracker() -> dict:
    if TRACKER_PATH.exists():
        return json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
    return {"last_commit_sha": None, "last_post_date": None, "posts_generated": 0}


def save_tracker(tracker: dict) -> None:
    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACKER_PATH.write_text(json.dumps(tracker, indent=2), encoding="utf-8")


# ── git helpers ──────────────────────────────────────────────────────────────

def get_commits_since(sha: str | None, max_days: int = 14) -> list[dict]:
    if sha:
        log_range = f"{sha}..HEAD"
    else:
        since = (date.today() - timedelta(days=max_days)).isoformat()
        log_range = f"--since={since}"

    result = subprocess.run(
        ["git", "log", "--no-merges", "--format=%H|||%s|||%ai", log_range],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if not result.stdout.strip():
        return []

    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|||")
        if len(parts) != 3:
            continue
        sha_val, subject, timestamp = parts
        commits.append({
            "sha": sha_val.strip(),
            "subject": subject.strip(),
            "date": timestamp.strip()[:10],
        })
    return commits


def is_notable(commit: dict) -> bool:
    subj = commit["subject"].lower()
    if any(subj.startswith(p) for p in SKIP_PREFIXES):
        return False
    return any(subj.startswith(p) for p in NOTABLE_PREFIXES)


def get_diff_stat(sha: str) -> str:
    result = subprocess.run(
        ["git", "show", "--stat", "--no-color", sha],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return result.stdout[:MAX_DIFF_CHARS]


def build_commit_summary(commits: list[dict]) -> str:
    parts = []
    for c in commits:
        diff = get_diff_stat(c["sha"])
        parts.append(f"[{c['date']}] {c['subject']}\n{diff}\n---")
    return "\n".join(parts)


# ── claude ───────────────────────────────────────────────────────────────────

def generate_post(commits: list[dict], context: str) -> str:
    client = anthropic.Anthropic()

    system = f"""You are a technical content writer helping a software engineer showcase their \
work on LinkedIn. The engineer is job-searching for fintech, data engineering, or backend \
engineering roles.

PROJECT CONTEXT:
{context}

WRITING RULES:
- Pick the SINGLE most post-worthy update from the commits provided.
- Write one complete LinkedIn post (~200 words max).
- Structure: punchy 1-line hook → problem or context → how you solved it → what you \
learned or why it matters → soft CTA.
- Tone: first-person, direct, technically specific but readable to a non-specialist.
- Do NOT start the post with "I" — open with a statement, question, or observation.
- No buzzwords: no "leveraged", "synergies", "innovative", "cutting-edge".
- No emoji unless it genuinely earns its place (default: none).
- End with an open question or a genuine invitation to connect — not "follow me for more".
- Output ONLY the post text. No preamble, no explanation, no markdown headers."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=700,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                "Here are the recent notable commits. "
                "Pick the most post-worthy update and write the LinkedIn post.\n\n"
                + build_commit_summary(commits)
            ),
        }],
    )
    return message.content[0].text.strip()


# ── output ───────────────────────────────────────────────────────────────────

def save_draft(post: str) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = DRAFTS_DIR / f"{today}.md"
    counter = 1
    while path.exists():
        path = DRAFTS_DIR / f"{today}-{counter}.md"
        counter += 1
    path.write_text(post, encoding="utf-8")
    return path


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    # Load .env so ANTHROPIC_API_KEY is available without setting it manually
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    tracker = load_tracker()
    context = CONTEXT_PATH.read_text(encoding="utf-8")

    print("Reading git history...")
    all_commits = get_commits_since(tracker["last_commit_sha"])

    if not all_commits:
        print("No new commits since last run.")
        return 0

    notable = [c for c in all_commits if is_notable(c)]

    if not notable:
        print(
            f"Found {len(all_commits)} commit(s) but none qualify as notable "
            "(all chore/docs/style/wip).\n"
            "Tip: prefix notable commits with feat:, fix:, refactor:, or perf:"
        )
        return 0

    print(f"Found {len(notable)} notable commit(s) out of {len(all_commits)} total.")
    for c in notable:
        print(f"  [{c['date']}] {c['subject']}")

    print("\nGenerating LinkedIn post via Claude...")

    try:
        post = generate_post(notable, context)
    except anthropic.APIError as exc:
        print(f"Claude API error: {exc}", file=sys.stderr)
        return 1

    draft_path = save_draft(post)

    tracker["last_commit_sha"] = all_commits[0]["sha"]
    tracker["last_post_date"] = date.today().isoformat()
    tracker["posts_generated"] = tracker.get("posts_generated", 0) + 1
    save_tracker(tracker)

    print(f"\nDraft saved: {draft_path.relative_to(ROOT)}")
    print(f"Posts generated so far: {tracker['posts_generated']}")
    print("\n" + "─" * 60)
    print(post)
    print("─" * 60)
    print("\nReview the draft above, then paste to LinkedIn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
