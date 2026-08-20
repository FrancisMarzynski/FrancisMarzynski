#!/usr/bin/env python3
"""Build the neofetch-style profile cards for github.com/FrancisMarzynski.

Asks the GitHub GraphQL API what the account looks like today, then draws
two SVGs - one for light mode, one for dark. The README embeds both and the
browser picks. A GitHub Action re-runs this daily and commits the result.

    python3 today.py              # fetch fresh stats, redraw both cards
    python3 today.py --offline    # redraw from cache/stats.json, no network

Needs a token in ACCESS_TOKEN with 'repo' and 'read:user' scope. Private
repositories are counted too, which matters while most of the work is private.
Only the totals reach the card - no private names, no private code.
"""
import argparse, json, os, pathlib, sys, urllib.error, urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# The static half of the card. Edit freely; nothing here is fetched.
# ---------------------------------------------------------------------------

TITLE = ("francis", "marzynski")          # rendered as francis@marzynski
TAGLINE = "context before agents"         # sits under the title; "" to drop it

# Uptime counts from here. Set it to your birth date and the card reports your
# actual age, in years and months - deliberately not days, because a day count
# is the birth date itself once someone subtracts it. Left empty it falls back
# to how long the GitHub account has existed.
BIRTH_DATE = "2008-06-15"

STATIC = [
    ("section", "System"),
    ("field", "OS", "macOS (Darwin 25.5), Ubuntu on servers"),
    ("field", "Uptime", None),            # None = filled in from account age
    ("field", "Editor", "Claude Code, VS Code"),

    ("section", "Languages"),
    ("field", "Programming", None),       # None = measured from your repos
    ("field", "Markup and data", "Markdown, JSON, YAML, SQL"),
    ("field", "Spoken", "Polish, English, some Spanish"),

    ("section", "Focus"),
    ("field", "Building", "operating-memory"),
    ("field", "Field", "AI engineering"),
    ("field", "Stack", "Python, SQLite, agent pipelines"),
    ("field", "Interests", "memory systems for LLM agents"),
    ("field", "Approach", "AI-pilled, agent-first"),
    ("field", "Based in", "Warsaw, Poland"),

    ("section", "Contact"),
    ("field", "Email", "francis.marzynski@ail-agency.com"),
    ("field", "GitHub", "github.com/FrancisMarzynski"),
]

# Lines of code is the least trustworthy number on the card: a repo with a
# lockfile, a vendored dependency or a generated bundle in its history can
# add six figures without a line being written by hand. Repos whose commits
# average more than this many changed lines are treated as vendored and left
# out of the line count. They still count towards repos and commits.
# Set to 0 to disable the filter, or SHOW_LINES_OF_CODE = False to drop the
# line from the card entirely.
SHOW_LINES_OF_CODE = False
MAX_LINES_PER_COMMIT = 1000
EXCLUDE_REPOS = []          # e.g. ["FrancisMarzynski/some-repo"]

ROOT = pathlib.Path(__file__).parent
CACHE = ROOT / "cache"
API = "https://api.github.com/graphql"

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "art": "#58a6ff", "title": "#58a6ff",
        "label": "#f778ba", "value": "#c9d1d9", "dim": "#6e7681",
        "green": "#3fb950", "orange": "#d29922", "purple": "#bc8cff",
    },
    "light": {
        "bg": "#ffffff", "border": "#d1d9e0", "art": "#0969da", "title": "#0969da",
        "label": "#bf3989", "value": "#1f2328", "dim": "#818b98",
        "green": "#1a7f37", "orange": "#9a6700", "purple": "#8250df",
    },
}

# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------


def graphql(query, variables, token):
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(API, data=body, headers={
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "profile-card",
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"GitHub returned {error.code}: {error.read().decode()[:400]}")
    if "errors" in payload:
        sys.exit("GitHub rejected the query: " + json.dumps(payload["errors"])[:400])
    return payload["data"]


VIEWER_QUERY = """
query($cursor: String) {
  viewer {
    id login name createdAt
    followers { totalCount }
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner isFork stargazerCount pushedAt
        languages(first: 8, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
        defaultBranchRef { target { ... on Commit { oid } } }
      }
    }
  }
}
"""

CONTRIBUTIONS_QUERY = """
query($from: DateTime!, $to: DateTime!) {
  viewer {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
    }
  }
}
"""

HISTORY_QUERY = """
query($owner: String!, $name: String!, $author: ID!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target { ... on Commit {
        history(first: 100, after: $cursor, author: {id: $author}) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { additions deletions }
        }
      } }
    }
  }
}
"""


def fetch_repositories(token):
    repos, cursor, viewer = [], None, None
    while True:
        data = graphql(VIEWER_QUERY, {"cursor": cursor}, token)["viewer"]
        viewer = viewer or data
        page = data["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return viewer, repos, page["totalCount"]
        cursor = page["pageInfo"]["endCursor"]


def fetch_commit_total(token, created_at):
    """Commits are only queryable a year at a time, so walk the years."""
    start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    total = 0
    year = start.year
    while year <= now.year:
        window_from = max(start, datetime(year, 1, 1, tzinfo=timezone.utc))
        window_to = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        collection = graphql(CONTRIBUTIONS_QUERY, {
            "from": window_from.isoformat(), "to": window_to.isoformat(),
        }, token)["viewer"]["contributionsCollection"]
        total += collection["totalCommitContributions"] + collection["restrictedContributionsCount"]
        year += 1
    return total


def fetch_lines_of_code(token, author_id, repos):
    """Walk every owned repo's history for commits authored by you.

    Commits are counted everywhere. Lines are counted only where the repo
    does not look like it is carrying vendored or generated files - see
    MAX_LINES_PER_COMMIT above. Returns the skipped repos so the run can
    say out loud what it left out.
    """
    cache_file = CACHE / "loc.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    added = removed = commits = 0
    skipped = []

    for repo in repos:
        if repo["isFork"] or not repo["defaultBranchRef"]:
            continue
        name = repo["nameWithOwner"]
        head = repo["defaultBranchRef"]["target"]["oid"]
        entry = cache.get(name)

        if not entry or entry.get("head") != head:
            owner, short = name.split("/", 1)
            cursor, totals = None, {"head": head, "added": 0, "removed": 0, "commits": 0}
            while True:
                branch = graphql(HISTORY_QUERY, {
                    "owner": owner, "name": short, "author": author_id, "cursor": cursor,
                }, token)["repository"]["defaultBranchRef"]
                if not branch:
                    break
                history = branch["target"]["history"]
                totals["commits"] = history["totalCount"]
                for commit in history["nodes"]:
                    totals["added"] += commit["additions"]
                    totals["removed"] += commit["deletions"]
                if not history["pageInfo"]["hasNextPage"]:
                    break
                cursor = history["pageInfo"]["endCursor"]
            entry = cache[name] = totals

        commits += entry["commits"]

        churn = entry["added"] + entry["removed"]
        per_commit = churn / entry["commits"] if entry["commits"] else 0
        vendored = MAX_LINES_PER_COMMIT and per_commit > MAX_LINES_PER_COMMIT
        if name in EXCLUDE_REPOS or vendored:
            skipped.append((name, int(per_commit)))
            continue

        added += entry["added"]
        removed += entry["removed"]

    CACHE.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")
    return added, removed, commits, sorted(skipped, key=lambda pair: -pair[1])


def top_languages(repos, limit=8):
    sizes = {}
    for repo in repos:
        if repo["isFork"]:
            continue
        for edge in repo["languages"]["edges"]:
            sizes[edge["node"]["name"]] = sizes.get(edge["node"]["name"], 0) + edge["size"]
    ranked = sorted(sizes, key=sizes.get, reverse=True)
    return ranked[:limit]


def since(timestamp):
    delta = datetime.now(timezone.utc) - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if delta.days > 1:
        return f"{delta.days} days ago"
    hours = delta.days * 24 + delta.seconds // 3600
    if hours > 1:
        return f"{hours} hours ago"
    return f"{max(1, delta.seconds // 60)} minutes ago"


def elapsed_since(timestamp, include_days=True):
    """Years, months and days from then to now, the way a person says it.

    include_days=False stops at months. The card uses that for age: printing
    the exact number of days since a birth date publishes the birth date,
    since anyone can subtract it back.
    """
    if len(timestamp) == 10:                       # a bare YYYY-MM-DD
        timestamp += "T00:00:00+00:00"
    start = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    years = now.year - start.year
    months = now.month - start.month
    days = now.day - start.day
    if days < 0:
        months -= 1
        days += 30
    if months < 0:
        years -= 1
        months += 12
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if include_days or not parts:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    return ", ".join(parts)


def collect(token):
    viewer, repos, repo_total = fetch_repositories(token)
    added, removed, commits_in_repos, skipped = fetch_lines_of_code(token, viewer["id"], repos)

    # Two ways to count commits, and they disagree for good reasons.
    # contributionsCollection covers every repo you touched, including ones
    # you do not own - but it silently returns 0 unless the token carries
    # read:user scope. Walking your own repos' history always works but
    # misses contributions elsewhere. Take whichever found more.
    contributed = fetch_commit_total(token, viewer["createdAt"])
    if contributed == 0 and commits_in_repos > 0:
        print("Note: contributionsCollection returned 0 - the token is missing "
              "read:user scope. Falling back to counting your own repos.", file=sys.stderr)

    for name, per_commit in skipped:
        print(f"Skipped for line count: {name} ({per_commit:,} lines/commit, looks vendored)", file=sys.stderr)

    return {
        "login": viewer["login"],
        "name": viewer["name"] or viewer["login"],
        "created_at": viewer["createdAt"],
        "age": elapsed_since(viewer["createdAt"]),
        "followers": viewer["followers"]["totalCount"],
        "repos": repo_total,
        "forks": sum(1 for repo in repos if repo["isFork"]),
        "stars": sum(repo["stargazerCount"] for repo in repos),
        "languages": top_languages(repos),
        "commits": max(contributed, commits_in_repos),
        "commits_in_repos": commits_in_repos,
        "added": added,
        "removed": removed,
        "last_push": since(max(repo["pushedAt"] for repo in repos if repo["pushedAt"])),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------------------
# Turning stats into coloured character runs
# ---------------------------------------------------------------------------

DOT_COLUMN = 24
RULE = "\x00"          # placeholder: widened to the panel once it is measured


def section(title):
    return [("label", f"- {title} "), ("dim", RULE)]


def field(label, parts):
    head = f"- {label}: "
    dots = "." * max(1, DOT_COLUMN - len(head))
    if isinstance(parts, str):
        parts = [("value", parts)]
    return [("label", head), ("dim", dots), ("dim", ": ")] + parts


def number(value):
    return f"{value:,}"


def widen_rules(lines):
    """Stretch every rule to the width of the widest real line."""
    width = max(sum(len(chunk) for _, chunk in line)
                for line in lines if not any(chunk == RULE for _, chunk in line))
    return [[(kind, "-" * max(3, width - sum(len(c) for _, c in line if c != RULE))
              if chunk == RULE else chunk) for kind, chunk in line]
            for line in lines]


def build_panel(stats):
    lines = []
    left, right = TITLE
    lines.append([("title", f"{left}@{right} "), ("dim", RULE)])
    if TAGLINE:
        lines.append([("purple", TAGLINE)])
    lines.append([])

    for entry in STATIC:
        if entry[0] == "section":
            lines.append(section(entry[1]))
        else:
            _, label, value = entry
            if label == "Uptime":
                value = elapsed_since(BIRTH_DATE, include_days=False) if BIRTH_DATE else stats["age"]
            elif label == "Programming":
                value = ", ".join(stats["languages"][:6]) or "Python"
            lines.append(field(label, value))
    lines.append([])

    lines.append(section("GitHub stats"))
    lines.append(field("Repos", [
        ("green", number(stats["repos"])),
        ("dim", " {"), ("value", "forks: "), ("orange", number(stats["forks"])), ("dim", "}"),
        ("dim", "  |  "), ("value", "Stars: "), ("green", number(stats["stars"])),
    ]))
    lines.append(field("Commits", [
        ("green", number(stats["commits"])),
        ("dim", "  |  "), ("value", "Followers: "), ("green", number(stats["followers"])),
    ]))
    if SHOW_LINES_OF_CODE:
        lines.append(field("Lines of code", [
            ("green", number(stats["added"] - stats["removed"])),
            ("dim", " ("), ("green", f"{number(stats['added'])}++"),
            ("dim", ", "), ("orange", f"{number(stats['removed'])}--"), ("dim", ")"),
        ]))
    lines.append(field("Last push", [("value", stats["last_push"])]))
    lines.append([])
    lines.append([(kind, "\u2588\u2588\u2588") for kind in
                  ("art", "label", "green", "orange", "purple", "value", "dim", "title")])
    lines.append([])
    lines.append([("dim", f"Last refreshed {stats['generated']} - private repos included")])
    return widen_rules(lines)


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

FONT = ("'JetBrains Mono','Cascadia Code','Fira Code','SF Mono',"
        "Menlo,Consolas,'DejaVu Sans Mono',monospace")
FONT_SIZE = 12.5
CHAR_W = FONT_SIZE * 0.6
LINE_H = 16.5
PAD = 26
GUTTER = 30


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def text_line(x, y, parts, palette, default="value"):
    """One row of the card.

    textLength pins the row to the width we measured the layout against.
    Without it the card depends on the viewer having a font whose advance
    width is exactly 0.6em - and when it is not, the ASCII art shears and
    the longest lines spill past the border. This makes the geometry
    survive whatever font the viewer actually has.
    """
    spans = "".join(
        f'<tspan fill="{palette[kind if kind in palette else default]}">{escape(chunk)}</tspan>'
        for kind, chunk in parts if chunk
    )
    columns = sum(len(chunk) for _, chunk in parts)
    if not columns:
        return ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" xml:space="preserve" '
            f'textLength="{columns * CHAR_W:.1f}" lengthAdjust="spacingAndGlyphs">{spans}</text>')


def render(art, panel, palette):
    art_w = max((len(line) for line in art), default=0)
    panel_w = max((sum(len(chunk) for _, chunk in line) for line in panel), default=0)
    width = round(PAD * 2 + art_w * CHAR_W + GUTTER + panel_w * CHAR_W)
    height = round(PAD * 2 + max(len(art), len(panel)) * LINE_H)

    art_x = PAD
    panel_x = PAD + art_w * CHAR_W + GUTTER
    top = PAD + FONT_SIZE

    body = [text_line(art_x, top + i * LINE_H, [("art", line)], palette)
            for i, line in enumerate(art)]
    body += [text_line(panel_x, top + i * LINE_H, line, palette)
             for i, line in enumerate(panel) if line]

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" \
viewBox="0 0 {width} {height}" font-family="{FONT}" font-size="{FONT_SIZE}">
<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="8" \
fill="{palette['bg']}" stroke="{palette['border']}"/>
{chr(10).join(body)}
</svg>
'''


# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="redraw from cache/stats.json, no network")
    args = parser.parse_args()

    CACHE.mkdir(exist_ok=True)
    stats_file = CACHE / "stats.json"

    if args.offline:
        if not stats_file.exists():
            sys.exit("No cache/stats.json yet. Run once online first.")
        stats = json.loads(stats_file.read_text())
    else:
        token = os.environ.get("ACCESS_TOKEN")
        if not token:
            sys.exit("Set ACCESS_TOKEN to a GitHub token with 'repo' and 'read:user' scope.")
        stats = collect(token)
        stats_file.write_text(json.dumps(stats, indent=2) + "\n")

    art_file = ROOT / "ascii_art.txt"
    art = art_file.read_text().rstrip("\n").split("\n") if art_file.exists() else []
    panel = build_panel(stats)

    for theme, palette in THEMES.items():
        (ROOT / f"{theme}_mode.svg").write_text(render(art, panel, palette))

    print(f"{stats['repos']} repos, {number(stats['commits'])} commits, "
          f"{number(stats['stars'])} stars, {number(stats['added'] - stats['removed'])} net lines")
    print("Wrote dark_mode.svg and light_mode.svg")


if __name__ == "__main__":
    main()
