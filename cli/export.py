"""
Flask CLI command to export all non-deleted posts and comments
from a sub into a self-contained HTML file.

Usage:
    poetry run ./throat.py export sub <sub_name>
    poetry run ./throat.py export sub <sub_name> --out /tmp/myarchive.html
"""

import html as html_lib
from datetime import datetime

import click
from flask.cli import AppGroup

from app.models import Sub, SubPost, SubPostComment, User

export = AppGroup("export", help="Export sub content to human-readable files.")


# ── Helpers ───────────────────────────────────────────────────────────────────


def e(text) -> str:
    if text is None:
        return ""
    return html_lib.escape(str(text))


def nl2br(text: str) -> str:
    if not text:
        return ""
    return e(text).replace("\n", "<br>\n")


def fmt_date(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    return dt.strftime("%d %b %Y, %H:%M")


# ── Data fetching ─────────────────────────────────────────────────────────────


def get_posts(sub):
    return (
        SubPost.select(SubPost, User.name.alias("author_name"))
        .join(User)
        .where((SubPost.sid == sub.sid) & (SubPost.deleted == 0))
        .order_by(SubPost.posted.desc())
        .dicts()
    )


def get_comments_for_post(post) -> list:
    """Returns a fully built, unpaginated comment tree for a single post.
    Intentionally does NOT use misc.get_comment_tree because that function
    requires current_user (a Flask-Login proxy unavailable in CLI context).
    Instead we replicate only the two pieces we need: build_tree + populate.
    """
    # 1 — Fetch full comment data for all non-deleted comments on this post
    rows = list(
        SubPostComment.select(
            SubPostComment.cid,
            SubPostComment.parentcid,
            SubPostComment.content,
            SubPostComment.time,
            SubPostComment.lastedit,
            SubPostComment.upvotes,
            SubPostComment.downvotes,
            SubPostComment.distinguish,
            User.name.alias("author"),
        )
        .join(User, on=(User.uid == SubPostComment.uid))
        .where(
            (SubPostComment.pid == post["pid"])
            & (SubPostComment.status.is_null() | (SubPostComment.status == 0))
        )
        .order_by(SubPostComment.time.asc())
        .dicts()
    )
    if not rows:
        return []

    # 2 — Build tree (same logic as get_comment_tree's inner build_tree,
    #     but operating on the fully-populated rows directly)
    def build_tree(comments, parent_cid=None):
        result = []
        for row in comments[:]:
            if row["parentcid"] == parent_cid:
                comments.remove(row)
                row["children"] = build_tree(comments, parent_cid=row["cid"])
                result.append(row)
        return result

    return build_tree(rows)


def _count_all(nodes) -> int:
    return sum(1 + _count_all(n["children"]) for n in nodes)


# ── HTML rendering ─────────────────────────────────────────────────────────────


def render_comment(node: dict, depth: int = 0) -> str:
    # get_comment_tree uses 'user' not 'author', and 'time' not 'posted'
    # content is already rendered as HTML by our_markdown() inside get_comment_tree
    if not node.get("cid"):
        # pagination stub — shouldn't appear in export but guard anyway
        return ""

    distinguish = node.get("distinguish") or 0
    badge = ""
    if distinguish == 1:
        badge = '<span class="badge mod">MOD</span>'
    elif distinguish == 2:
        badge = '<span class="badge admin">ADMIN</span>'

    edited_html = (
        f'<span class="edited">(edited {e(fmt_date(node["lastedit"]))})</span>'
        if node.get("lastedit")
        else ""
    )

    children_html = "".join(
        render_comment(c, depth + 1) for c in node.get("children", [])
    )
    depth_class = f"depth-{min(depth, 8)}"
    up = node.get("upvotes") or 0
    down = node.get("downvotes") or 0
    net = up - down
    score_str = f"+{net}" if net >= 0 else str(net)

    return f"""
    <div class="comment {depth_class}" id="c-{node['cid']}">
      <div class="comment-meta">
        <span class="author">{e(node.get('author'))}</span>{badge}
        <span class="score">{e(score_str)}</span>
        <span class="date">{e(fmt_date(node.get('time')))}</span>
        {edited_html}
      </div>
      <div class="comment-body">{nl2br(node.get('content', ''))}</div>
      {f'<div class="comment-children">{children_html}</div>' if children_html else ''}
    </div>"""


def render_post(post: dict) -> str:
    pid = post["pid"]
    comment_tree = get_comments_for_post(post)

    link_html = (
        f'<div class="post-link-header"><a href="{e(post["link"])}" target="_blank" rel="noopener">'
        f'🔗 {e(post["link"])}</a></div>'
        if post.get("link")
        else ""
    )
    body_html = (
        f'<div class="post-text">{nl2br(post["content"])}</div>'
        if post.get("content")
        else ""
    )
    flair_html = (
        f'<span class="flair">{e(post["flair"])}</span>' if post.get("flair") else ""
    )
    nsfw_html = '<span class="badge nsfw">NSFW</span>' if post.get("nsfw") else ""
    edited_html = (
        f'<span class="edited">(edited {e(fmt_date(post["edited"]))})</span>'
        if post.get("edited")
        else ""
    )

    up = post.get("upvotes") or 0
    down = post.get("downvotes") or 0
    net = up - down
    score_str = f"+{net}" if net >= 0 else str(net)

    comments = comment_tree  # already a list of top-level nodes for this post
    comment_count = _count_all(comments)
    comments_html = "".join(render_comment(c) for c in comments)
    comments_section = f"""
    <div class="comments-section">
      <h3 class="comments-heading">{comment_count} comment{'s' if comment_count != 1 else ''}</h3>
      {comments_html if comments_html else '<p class="no-comments">No comments.</p>'}
    </div>"""

    return f"""
  <article class="post" id="p-{pid}">
    <div class="post-header">
      <div class="post-title-row">
        <h2 class="post-title">{e(post['title'])}</h2>
        {flair_html}{nsfw_html}
      </div>
      {link_html}
      <div class="post-meta">
        <span class="author">{e(post.get('author_name'))}</span>
        <span class="score">{e(score_str)}</span>
        <span class="date">{e(fmt_date(post.get('posted')))}</span>
        {edited_html}
      </div>
    </div>
    {body_html}
    {comments_section}
  </article>"""


def render_html_sub(sub_name: str, posts) -> str:
    posts_list = list(posts)
    posts_html = "\n".join(render_post(p) for p in posts_list)
    total_posts = len(posts_list)
    # comment counts come from SubPostComment noreplies or we recount from rendered tree
    total_comments = sum(p.get("comments", 0) or 0 for p in posts_list)
    generated = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    toc_items = "".join(
        f'<li><a href="#p-{p["pid"]}">{e(p["title"])}</a></li>' for p in posts_list
    )
    font_url = (
        "https://fonts.googleapis.com/css2"
        "?family=Lora:ital,wght@0,400;0,600;1,400"
        "&family=JetBrains+Mono:wght@400;500&display=swap"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>/s/{e(sub_name)} — Archive</title>
  <style>
    @import url('{font_url}');

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg:          #f5f0e8;
      --surface:     #fffdf8;
      --border:      #d4c9b0;
      --text:        #2c2418;
      --muted:       #7a6e5f;
      --accent:      #8b4513;
      --accent-lt:   #c4752a;
      --mod-color:   #2563eb;
      --admin-color: #dc2626;
      --nsfw-color:  #db2777;
    }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'Lora', Georgia, serif;
      font-size: 16px;
      line-height: 1.7;
      padding: 2rem 1rem;
    }}

    /* ── Header ── */
    .site-header {{
      max-width: 860px;
      margin: 0 auto 3rem;
      border-bottom: 2px solid var(--accent);
      padding-bottom: 1.5rem;
    }}
    .site-header h1 {{ font-size: 2.2rem; font-weight: 600; color: var(--accent); }}
    .site-header h1 span {{ color: var(--muted); font-weight: 400; }}
    .header-stats {{
      margin-top: 0.4rem;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.78rem;
      color: var(--muted);
    }}

    /* ── TOC ── */
    .toc {{
      max-width: 860px;
      margin: 0 auto 2.5rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1.2rem 1.5rem;
    }}
    .toc h2 {{
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 0.8rem;
    }}
    .toc ol {{ padding-left: 1.4rem; font-size: 0.9rem; }}
    .toc li {{ margin-bottom: 0.25rem; }}
    .toc a {{ color: var(--accent); text-decoration: none; }}
    .toc a:hover {{ text-decoration: underline; }}

    /* ── Posts ── */
    .post {{
      max-width: 860px;
      margin: 0 auto 2.5rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 4px;
      overflow: hidden;
    }}
    .post-header {{
      padding: 1.4rem 1.6rem 1rem;
      border-bottom: 1px solid var(--border);
    }}
    .post-title-row {{
      display: flex;
      align-items: flex-start;
      gap: 0.6rem;
      flex-wrap: wrap;
    }}
    .post-title {{ font-size: 1.2rem; font-weight: 600; line-height: 1.4; flex: 1; min-width: 0; }}
    .post-meta {{
      margin-top: 0.5rem;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      color: var(--muted);
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
      align-items: center;
    }}
    .post-meta .author {{ color: var(--accent-lt); font-weight: 500; }}

    .post-link-header {{ margin-top: 0.5rem; font-size: 0.85rem; word-break: break-all; }}
    .post-link-header a {{ color: var(--accent); text-decoration: none; }}
    .post-link-header a:hover {{ text-decoration: underline; }}

    .post-text {{
      padding: 1.2rem 1.6rem;
      border-bottom: 1px solid var(--border);
      font-size: 0.97rem;
      line-height: 1.75;
    }}

    /* ── Badges ── */
    .badge {{
      display: inline-block;
      padding: 0.1em 0.5em;
      border-radius: 3px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.68rem;
      font-weight: 500;
      letter-spacing: 0.04em;
      vertical-align: middle;
    }}
    .badge.mod   {{ background: #dbeafe; color: var(--mod-color); }}
    .badge.admin {{ background: #fee2e2; color: var(--admin-color); }}
    .badge.nsfw  {{ background: #fce7f3; color: var(--nsfw-color); }}
    .flair {{
      display: inline-block;
      padding: 0.15em 0.6em;
      background: #e8e0d0;
      border-radius: 99px;
      font-size: 0.75rem;
      color: var(--muted);
      white-space: nowrap;
    }}

    .edited {{ font-style: italic; font-size: 0.75rem; color: var(--muted); }}

    /* ── Comments ── */
    .comments-section {{ padding: 1rem 1.6rem 1.4rem; }}
    .comments-heading {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--muted);
      margin-bottom: 1rem;
      font-weight: 600;
    }}
    .no-comments {{ font-size: 0.88rem; color: var(--muted); font-style: italic; }}

    .comment {{
      margin-top: 0.9rem;
      padding-left: 1rem;
      border-left: 2px solid var(--border);
      transition: border-color 0.15s;
    }}
    .comment:hover {{ border-left-color: var(--accent-lt); }}
    .depth-0 {{ border-left-color: #c4752a44; }}
    .depth-1 {{ border-left-color: #2563eb44; }}
    .depth-2 {{ border-left-color: #16a34a44; }}
    .depth-3 {{ border-left-color: #9333ea44; }}
    .depth-4 {{ border-left-color: #d9770644; }}
    .depth-5 {{ border-left-color: #0891b244; }}
    .depth-6 {{ border-left-color: #65a30d44; }}
    .depth-7 {{ border-left-color: #db287744; }}
    .depth-8 {{ border-left-color: #6366f144; }}

    .comment-meta {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.72rem;
      color: var(--muted);
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 0.35rem;
    }}
    .comment-meta .author {{ color: var(--accent-lt); font-weight: 500; }}
    .comment-body {{ font-size: 0.93rem; line-height: 1.65; }}
    .comment-children {{ margin-top: 0.4rem; }}

    /* ── Footer ── */
    footer {{
      max-width: 860px;
      margin: 3rem auto 0;
      padding-top: 1rem;
      border-top: 1px solid var(--border);
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      color: var(--muted);
      text-align: center;
    }}

    @media print {{
      body {{ background: white; }}
      .post {{ border: 1px solid #ccc; page-break-inside: avoid; }}
    }}
    @media (max-width: 600px) {{
      .post-header, .post-text, .comments-section {{ padding-left: 1rem; padding-right: 1rem; }}
      .comment {{ padding-left: 0.6rem; }}
    }}
  </style>
</head>
<body>

<header class="site-header">
  <h1><span>/s/</span>{e(sub_name)}</h1>
  <div class="header-stats">
    {total_posts} post{'s' if total_posts != 1 else ''} &middot;
    {total_comments} comment{'s' if total_comments != 1 else ''} &middot;
    exported {generated}
  </div>
</header>

<nav class="toc">
  <h2>Table of Contents</h2>
  <ol>{toc_items}</ol>
</nav>

<main>
{posts_html}
</main>

<footer>/s/{e(sub_name)} archive &middot; generated {generated}</footer>

</body>
</html>"""


# ── CLI command ───────────────────────────────────────────────────────────────


@export.command(
    name="sub", help="Export all posts and comments from a sub to an HTML file."
)
@click.argument("sub_name")
@click.option(
    "--out",
    default=None,
    help="Output file path (default: <sub_name>_export.html)",
)
def export_sub(sub_name, out):
    try:
        sub = Sub.get(Sub.name == sub_name)
    except Sub.DoesNotExist:
        raise click.ClickException(f"Sub '{sub_name}' does not exist.")

    out_file = out or f"{sub_name}_export.html"

    click.echo(f"Fetching posts from /s/{sub_name}…")
    posts = list(get_posts(sub))
    if not posts:
        raise click.ClickException(f"No posts found in '{sub_name}'.")
    click.echo(f"  → {len(posts)} posts found.")
    click.echo("Fetching comments and rendering HTML…")
    click.echo("  (comments are fetched per post via get_comment_tree)")

    output = render_html_sub(sub_name, posts)

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(output)

    click.echo(f"\nDone! Saved to: {out_file}")
