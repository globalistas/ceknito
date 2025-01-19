""" Home, all and related endpoints """
import re
from feedgen.feed import FeedGenerator
from flask import (
    Blueprint,
    request,
    url_for,
    Response,
    abort,
    render_template,
    redirect,
)
from flask_login import current_user
from .. import misc
from ..config import config
from ..misc import engine, limit_pagination
from ..misc import ratelimit, POSTING_LIMIT
from ..models import SubPost, Sub

bp = Blueprint("home", __name__)


@bp.route("/")
def index():
    """The index page, shows /hot of current subscriptions"""
    return hot(1)


@bp.route("/hot", defaults={"page": 1})
@bp.route("/hot/<int:page>")
@limit_pagination
def hot(page):
    """/hot for subscriptions"""
    posts = misc.getPostList(misc.postListQueryHome(), "hot", page)
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.hot",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


@bp.route("/new", defaults={"page": 1})
@bp.route("/new/<int:page>")
@limit_pagination
def new(page):
    """/new for subscriptions"""
    posts = misc.getPostList(misc.postListQueryHome(), "new", page)
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.new",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


@bp.route("/top", defaults={"page": 1})
@bp.route("/top/<int:page>")
@limit_pagination
def top(page):
    """/top for subscriptions"""
    posts = misc.getPostList(misc.postListQueryHome(), "top", page)
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.top",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


@bp.route("/commented", defaults={"page": 1})
@bp.route("/commented/<int:page>")
@limit_pagination
def commented(page):
    """/last commented for subscriptions"""
    posts = misc.getPostList(misc.postListQueryHome(), "commented", page)
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.commented",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


@bp.route("/all/new.rss")
def all_new_rss():
    """RSS feed for /all/new"""
    posts = misc.getPostList(
        misc.postListQueryBase(filter_shadowbanned=True, filter_private_posts=True),
        "new",
        1,
    )
    fg = FeedGenerator()
    fg.id(request.url)
    fg.title("čekni.to")
    fg.link(href=request.url_root, rel="alternate")
    fg.link(href=request.url, rel="self")

    return Response(
        misc.populate_feed(fg, posts).atom_str(pretty=True),
        mimetype="application/atom+xml",
    )


@bp.route("/all/new", defaults={"page": 1})
@bp.route("/all/new/<int:page>")
@limit_pagination
def all_new(page):
    """The index page, all posts sorted as most recent posted first"""
    posts = misc.getPostList(
        misc.postListQueryBase(
            isSubMod=current_user.can_admin,
            filter_shadowbanned=True,
            filter_private_posts=True,
        ),
        "new",
        page,
    )
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.all_new",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


@bp.route("/all/<sort>/more", defaults={"pid": None})
@bp.route("/all/<sort>/more/<int:page>/<int:pid>")
@limit_pagination
def all_more(sort, page, pid):
    """Infinite scroll pagination for /all"""
    # XXX: Our pagination is very slow
    if sort == "new":
        posts = misc.getPostList(
            misc.postListQueryBase(
                isSubMod=current_user.can_admin,
                filter_shadowbanned=True,
                filter_private_posts=True,
            ).where(SubPost.pid < pid),
            "new",
            1,
        )
    elif sort == "top":
        posts = misc.getPostList(
            misc.postListQueryBase(
                isSubMod=current_user.can_admin,
                filter_shadowbanned=True,
                filter_private_posts=True,
            ),
            "top",
            page,
        )
    elif sort == "hot":
        posts = misc.getPostList(
            misc.postListQueryBase(
                isSubMod=current_user.can_admin,
                filter_shadowbanned=True,
                filter_private_posts=True,
            ),
            "hot",
            page,
        )
    elif sort == "commented":
        posts = misc.getPostList(
            misc.postListQueryBase(
                isSubMod=current_user.can_admin,
                filter_shadowbanned=True,
                filter_private_posts=True,
            ),
            "commented",
            page,
        )
    else:
        return abort(404)

    return engine.get_template("shared/post.html").render(
        {"posts": posts, "sub": False}
    )


@bp.route("/home/<sort>/more", defaults={"pid": None})
@bp.route("/home/<sort>/more/<int:page>/<int:pid>")
@limit_pagination
def home_more(sort, page, pid):
    """Infinite scroll pagination for /all"""
    # XXX: Our pagination is very slow
    if sort == "new":
        posts = misc.getPostList(
            misc.postListQueryHome().where(SubPost.pid < pid), "new", 1
        )
    elif sort == "top":
        posts = misc.getPostList(misc.postListQueryHome(), "top", page)
    elif sort == "hot":
        posts = misc.getPostList(misc.postListQueryHome(), "hot", page)
    elif sort == "commented":
        posts = misc.getPostList(misc.postListQueryHome(), "commented", page)
    else:
        return abort(404)

    return engine.get_template("shared/post.html").render(
        {"posts": posts, "sub": False}
    )


@bp.route("/domain/<domain>", defaults={"page": 1})
@bp.route("/domain/<domain>/<int:page>")
@limit_pagination
def all_domain_new(domain, page):
    """The index page, all posts sorted as most recent posted first"""
    domain = re.sub(r"[^A-Za-z0-9.\-_]+", "", domain)
    posts = misc.getPostList(
        misc.postListQueryBase(
            noAllFilter=True, filter_shadowbanned=True, filter_private_posts=True
        ).where(
            SubPost.link % ("%://" + domain + "/%") | SubPost.link % ("%://" + domain)
        ),
        "new",
        page,
    )
    count = misc.postListQueryBase(
        noAllFilter=True, filter_shadowbanned=True, filter_private_posts=True
    ).where(SubPost.link % ("%://" + domain + "/%") | SubPost.link % ("%://" + domain))
    post_count = len(count)
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.all_domain_new",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {"domain": domain, "post_count": post_count},
        }
    )


@bp.route("/search/<term>", defaults={"page": 1})
@bp.route("/search/<term>/<int:page>")
@ratelimit(POSTING_LIMIT)
@limit_pagination
def search(page, term):
    """The index page, with basic title search"""
    term = re.sub(r'[^A-Za-zÁ-ž0-9.,\-_\'" ]+', "", term)
    posts = misc.getPostList(
        misc.postListQueryBase(
            filter_shadowbanned=True, filter_private_posts=True
        ).where(SubPost.title ** ("%" + term + "%")),
        "new",
        page,
    )
    # Query to get the total count of matching posts
    count = misc.postListQueryBase(
        filter_shadowbanned=True, filter_private_posts=True
    ).where(SubPost.title ** ("%" + term + "%"))
    search_count = len(count)
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.search",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {"term": term, "search_count": search_count},
        }
    )


@bp.route("/search/<term>/.rss")
def search_and_build_feed(page, term):
    """Posts matching search keywords rendered as web feed"""
    if not config.allow_search_feeds:
        return
    term = re.sub(r'[^A-Za-zÁ-ž0-9.,\-_\'" ]+', "", term)
    posts = misc.getPostList(
        misc.postListQueryBase(
            filter_shadowbanned=True, filter_private_posts=True
        ).where(SubPost.title ** ("%" + term + "%")),
        "new",
        page,
    )
    posts = misc.getPostList(
        misc.postListQueryBase(filter_shadowbanned=True, filter_private_posts=True),
        "new",
        1,
    )
    fg = FeedGenerator()
    fg.id(request.url)
    fg.title(f"Search results matching {term}")
    fg.link(href=request.url_root, rel="alternate")
    fg.link(href=request.url, rel="self")
    return Response(
        misc.populate_feed(fg, posts).atom_str(pretty=True),
        mimetype="application/atom+xml",
    )


@bp.route("/all/top", defaults={"page": 1})
@bp.route("/all/top/<int:page>")
@limit_pagination
def all_top(page):
    """The index page, all posts sorted as most recent posted first"""
    posts = misc.getPostList(
        misc.postListQueryBase(
            isSubMod=current_user.can_admin,
            filter_shadowbanned=True,
            filter_private_posts=True,
        ),
        "top",
        page,
    )
    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.all_top",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


@bp.route("/all", defaults={"page": 1})
@bp.route("/all/hot", defaults={"page": 1})
@bp.route("/all/hot/<int:page>")
@limit_pagination
def all_hot(page):
    """The index page, all posts sorted as most recent posted first"""
    posts = misc.getPostList(
        misc.postListQueryBase(
            isSubMod=current_user.can_admin,
            filter_shadowbanned=True,
            filter_private_posts=True,
        ),
        "hot",
        page,
    )

    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.all_hot",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


@bp.route("/all", defaults={"page": 1})
@bp.route("/all/commented", defaults={"page": 1})
@bp.route("/all/commented/<int:page>")
@limit_pagination
def all_commented(page):
    """/last commented for all"""
    posts = misc.getPostList(
        misc.postListQueryBase(
            isSubMod=current_user.can_admin,
            filter_shadowbanned=True,
            filter_private_posts=True,
        ),
        "commented",
        page,
    )

    return engine.get_template("index.html").render(
        {
            "posts": posts,
            "sort_type": "home.all_commented",
            "page": page,
            "subOfTheDay": misc.getSubOfTheDay(),
            "changeLog": misc.getChangelog(),
            "ann": misc.getAnnouncement(),
            "kw": {},
        }
    )


# Note for future self: I rewrote until this part. You should do the rest.


@bp.route("/subs", defaults={"page": 1, "sort": "name_asc"})
@bp.route("/subs/<sort>", defaults={"page": 1})
@bp.route("/subs/<int:page>", defaults={"sort": "name_asc"})
@bp.route("/subs/<int:page>/<sort>")
def view_subs(page, sort):
    """Here we can view available subs"""
    c = Sub.select(
        Sub.sid, Sub.name, Sub.title, Sub.nsfw, Sub.creation, Sub.subscribers, Sub.posts
    )
    if not current_user.can_admin:
        c = c.where(Sub.private == 0)

    # sorts...
    if sort == "name_desc":
        c = c.order_by(Sub.name.desc())
    elif sort == "name_asc":
        c = c.order_by(Sub.name.asc())
    elif sort == "posts_desc":
        c = c.order_by(Sub.posts.desc())
    elif sort == "posts_asc":
        c = c.order_by(Sub.posts.asc())
    elif sort == "subs_desc":
        c = c.order_by(Sub.subscribers.desc())
    elif sort == "subs_asc":
        c = c.order_by(Sub.subscribers.asc())
    else:
        return redirect(url_for("home.view_subs", page=page, sort="name_asc"))

    c = c.paginate(page, 50).dicts()
    cp_uri = "/subs/" + str(page)
    return render_template(
        "subs.html", page=page, subs=c, nav="home.view_subs", sort=sort, cp_uri=cp_uri
    )


@bp.route("/subs/search/<term>", defaults={"page": 1, "sort": "name_asc"})
@bp.route("/subs/search/<term>/<sort>", defaults={"page": 1})
@bp.route("/subs/search/<term>/<int:page>", defaults={"sort": "name_asc"})
@bp.route("/subs/search/<term>/<int:page>/<sort>")
@ratelimit(POSTING_LIMIT)
def subs_search(page, term, sort):
    """The subs index page, with basic title search"""
    term = re.sub(r"[^A-Za-zÁ-ž0-9\-_]+", "", term)
    c = Sub.select(
        Sub.sid,
        Sub.name,
        Sub.title,
        Sub.private,
        Sub.nsfw,
        Sub.creation,
        Sub.subscribers,
        Sub.posts,
    )

    c = c.where((Sub.name.contains(term)) & (Sub.private == 0))

    # sorts...
    if sort == "name_desc":
        c = c.order_by(Sub.name.desc())
    elif sort == "name_asc":
        c = c.order_by(Sub.name.asc())
    elif sort == "posts_desc":
        c = c.order_by(Sub.posts.desc())
    elif sort == "posts_asc":
        c = c.order_by(Sub.posts.asc())
    elif sort == "subs_desc":
        c = c.order_by(Sub.subscribers.desc())
    elif sort == "subs_asc":
        c = c.order_by(Sub.subscribers.asc())
    else:
        return redirect(url_for("home.view_subs", page=page, sort="name_asc"))
    c = c.paginate(page, 50).dicts()
    cp_uri = "/subs/search/" + term + "/" + str(page)
    return render_template(
        "subs.html",
        page=page,
        subs=c,
        nav="home.subs_search",
        term=term,
        sort=sort,
        cp_uri=cp_uri,
    )
