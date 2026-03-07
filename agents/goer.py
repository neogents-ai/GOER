"""
GOER Agent
Reads infra/snapshots/ and infra/docs/, organizes them into a categorized
portfolio index at infra/portfolio/index.md, then git commits and pushes.

Usage:
  python agents/goer.py            # run once
  python agents/goer.py --watch    # watch infra/ for changes, auto-run
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
INFRA_DIR  = BASE_DIR / "infra"
SNAP_DIR   = INFRA_DIR / "snapshots"
DOCS_DIR   = INFRA_DIR / "docs"
PORT_DIR   = INFRA_DIR / "portfolio"
INDEX_MD   = PORT_DIR / "index.md"
INDEX_JSON = PORT_DIR / "index.json"
SYSTEM_TXT = BASE_DIR / "prompts" / "job_finder_system.txt"


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------

def _collect_docs() -> list[dict]:
    docs = []
    for folder in [SNAP_DIR, DOCS_DIR]:
        if folder.exists():
            for f in sorted(folder.glob("*.md")):
                docs.append({"path": str(f.relative_to(BASE_DIR)), "content": f.read_text()})
    return docs


# ---------------------------------------------------------------------------
# AI organize
# ---------------------------------------------------------------------------

def _organize(docs: list[dict], client) -> dict:
    system = SYSTEM_TXT.read_text() if SYSTEM_TXT.exists() else ""
    user = f"Organize these {len(docs)} doc(s) into a portfolio index.\n\n"
    for d in docs:
        user += f"--- FILE: {d['path']} ---\n{d['content'][:2500]}\n\n"

    resp = client.chat.completions.create(
        model=os.getenv("JF_MODEL", "anthropic/claude-sonnet-4-6"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=2000,
    )
    raw = resp.choices[0].message.content.strip()
    # Strip markdown fences if present
    import re
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
    return json.loads(raw.strip())


# ---------------------------------------------------------------------------
# Write index
# ---------------------------------------------------------------------------

def _write_index(categories: list[dict]) -> None:
    PORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    lines = [f"# Portfolio Index\n*Last updated: {today}*\n\n---\n"]

    for cat in categories:
        lines.append(f"## {cat['name']}")
        techs = ", ".join(cat.get("technologies", []))
        if techs:
            lines.append(f"**Stack:** {techs}\n")
        for proj in cat.get("projects", []):
            lines.append(f"### {proj['title']}")
            pt = ", ".join(proj.get("technologies", []))
            if pt:
                lines.append(f"`{pt}`\n")
            for tp in proj.get("talking_points", []):
                lines.append(f"- {tp}")
            src = proj.get("source_file", "")
            if src:
                lines.append(f"\n*[Source doc]({src})*")
            lines.append("")

    INDEX_MD.write_text("\n".join(lines))
    print(f"  [job_finder] index.md written")


# ---------------------------------------------------------------------------
# Git push
# ---------------------------------------------------------------------------

def _git_push(message: str) -> None:
    try:
        subprocess.run(["git", "add", "infra/"], cwd=BASE_DIR, check=True, capture_output=True)
        has_changes = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR, capture_output=True
        ).returncode != 0

        if not has_changes:
            print("  [job_finder] Nothing to commit.")
            return

        subprocess.run(["git", "commit", "-m", message], cwd=BASE_DIR, check=True, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=BASE_DIR, check=True, capture_output=True)
        print(f"  [job_finder] Pushed → {message}")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode().strip() if e.stderr else str(e)
        print(f"  [job_finder] Git error: {err}")


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run() -> None:
    print("\n[job_finder] Scanning infra docs...")

    docs = _collect_docs()
    if not docs:
        print("  [job_finder] No docs found. Add .md files to infra/docs/ or infra/snapshots/")
        return

    print(f"  [job_finder] {len(docs)} doc(s) found — organizing...")

    from openai import OpenAI
    client = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url=os.getenv("OPENCLAW_BASE_URL", "https://openrouter.ai/api/v1"),
    )

    try:
        result = _organize(docs, client)
    except Exception as e:
        print(f"  [job_finder] AI error: {e}")
        return

    categories = result.get("categories", [])
    commit_msg = result.get("commit_message", "infra: update portfolio index")

    _write_index(categories)
    INDEX_JSON.write_text(json.dumps(result, indent=2))
    _git_push(commit_msg)

    total = sum(len(c.get("projects", [])) for c in categories)
    print(f"  [job_finder] Done. {total} project(s) indexed across {len(categories)} domain(s).\n")


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def watch() -> None:
    print(f"[job_finder] Watching {INFRA_DIR}")
    print("[job_finder] Drop .md files in infra/docs/ or run snapshot.sh — index auto-updates.")
    print("[job_finder] Ctrl+C to stop.\n")

    def state():
        files = list(SNAP_DIR.glob("*.md")) if SNAP_DIR.exists() else []
        files += list(DOCS_DIR.glob("*.md")) if DOCS_DIR.exists() else []
        return {str(f): f.stat().st_mtime for f in files}

    last = state()
    try:
        while True:
            time.sleep(5)
            current = state()
            if current != last:
                changed = (set(current) - set(last)) | {f for f in current if f in last and current[f] != last[f]}
                for f in changed:
                    print(f"[job_finder] Changed: {Path(f).name}")
                last = current
                run()
    except KeyboardInterrupt:
        print("\n[job_finder] Stopped.")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    else:
        run()
