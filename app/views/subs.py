""" Generic sub actions (creating subs, creating posts, etc) """
import uuid
from datetime import datetime, timedelta, timezone
from peewee import fn
from flask import Blueprint, abort, request, redirect, url_for
from flask_login import login_required, current_user
from flask_babel import _
from .. import misc
from ..badges import Badges
from ..config import config
from ..misc import engine, ratelimit, POSTING_LIMIT, gevent_required
from ..socketio import socketio
from ..models import (
    Sub,
    db as pdb,
    SubMod,
    SubMetadata,
    SubStylesheet,
    SubSubscriber,
    SiteMetadata,
    SubPost,
)
from ..models import SubPostPollOption, SubPostMetadata, SubPostVote, User, UserUploads
from ..forms import CreateSubPostForm, CreateSubForm
from ..storage import file_url, upload_file
from ..tasks import create_thumbnail, create_thumbnail_external

bp = Blueprint("subs", __name__)


@bp.route("/submit/<ptype>", defaults={"sub": ""}, methods=["GET"])
@bp.route("/submit/<ptype>/<sub>", methods=["GET"])
@login_required
def submit(ptype, sub):
    if ptype not in ["link", "text", "poll", "upload"]:
        abort(404)

    captcha = None
    if misc.get_user_level(current_user.uid)[0] <= 4:
        captcha = misc.create_captcha()

    form = CreateSubPostForm()
    if not current_user.canupload:
        form.ptype.choices = [
            choice for choice in form.ptype.choices if choice[0] != "upload"
        ]

    form.ptype.data = ptype

    if sub != "":
        form.sub.data = sub
        try:
            sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
        except Sub.DoesNotExist:
            abort(404)

    if request.args.get("title"):
        form.title.data = request.args.get("title")

    if request.args.get("url"):
        form.link.data = request.args.get("url")

    return engine.get_template("sub/createpost.html").render(
        {
            "error": misc.get_errors(form, True),
            "form": form,
            "sub": sub,
            "captcha": captcha,
        }
    )


@bp.route("/submit/<ptype>", defaults={"sub": ""}, methods=["POST"])
@bp.route("/submit/<ptype>/<sub>", methods=["POST"])
@gevent_required  # Starts async task (thumbnail).
@login_required
@ratelimit(POSTING_LIMIT)
def create_post(ptype, sub):
    if ptype not in ["link", "text", "poll", "upload"]:
        abort(404)

    captcha = None
    if misc.get_user_level(current_user.uid)[0] <= 4:
        captcha = misc.create_captcha()

    form = CreateSubPostForm()
    if not current_user.canupload:
        form.ptype.choices = [
            choice for choice in form.ptype.choices if choice[0] != "upload"
        ]

    if not form.sub.data and sub != "":
        form.sub.data = sub

    flairs = []
    subdata = {}

    def error_response(msg):
        return (
            engine.get_template("sub/createpost.html").render(
                {
                    "error": msg,
                    "form": form,
                    "sub": sub,
                    "flairs": flairs,
                    "sub_data": subdata,
                    "captcha": captcha,
                }
            ),
            400,
        )

    try:
        sub = Sub.get(fn.Lower(Sub.name) == form.sub.data.lower())
        subdata = misc.getSubData(sub.sid)
        user_can_flair = subdata.get("ucf") == "1"
        user_must_flair = subdata.get("umf") == "1"
        if user_can_flair or user_must_flair:
            flairs = misc.getSubFlairs(sub.sid)
    except Sub.DoesNotExist:
        sub = ""
        return error_response(_("Sub does not exist."))

    if not form.validate():
        if not form.ptype.data:
            form.ptype.data = ptype
        return error_response(misc.get_errors(form, True))

    submods = misc.getSubMods(sub.sid)
    if (
        user_must_flair
        and form.flair.data == ""
        and current_user.uid not in submods["all"]
    ):
        return error_response(_("Please select a flair for your post."))

    flair = None
    if form.flair.data:
        flair = [f.text for f in flairs if str(f.xid) == form.flair.data]
        if (user_must_flair or user_can_flair) and not flair:
            return error_response(_("Invalid flair."))
        flair = flair[0]

    ptype_flag = misc.ptype_names.get(form.ptype.data, None)
    if ptype_flag is None or subdata.get(ptype_flag, "0") == "0":
        return error_response(_("That post type is not allowed in this sub."))

    if misc.get_user_level(current_user.uid)[0] <= 4:
        if not misc.validate_captcha(form.ctok.data, form.captcha.data):
            return error_response(_("Invalid captcha."))

    # Put pre-posting checks here
    if not current_user.is_admin() and not config.site.enable_posting:
        return error_response(_("Posting has been temporarily disabled."))

    isSubMod = current_user.is_mod(sub.sid, 2) or current_user.is_admin()
    if sub.private == 1 and not (
        current_user.can_admin or isSubMod or (current_user.has_subscribed(sub.name))
    ):
        return error_response(_("You are not allowed to post here."))

    if not current_user.can_post():
        return error_response(_("Insufficient user level to create posts."))

    if sub.name.lower() in ("all", "new", "hot", "top", "commented", "admin", "home"):
        return error_response(_("You cannot post in this sub."))

    if current_user.is_subban(sub):
        return error_response(_("You're banned from posting on this sub."))

    if subdata.get("restricted", 0) == "1" and not (current_user.uid in submods["all"]):
        return error_response(_("Only mods can post on this sub."))

    if misc.get_user_level(current_user.uid)[0] < 10:
        today = datetime.utcnow() - timedelta(days=1)
        lposts = (
            SubPost.select()
            .where(SubPost.uid == current_user.uid)
            .where(SubPost.sid == sub.sid)
            .where(SubPost.posted > today)
            .count()
        )
        tposts = (
            SubPost.select()
            .where(SubPost.uid == current_user.uid)
            .where(SubPost.posted > today)
            .count()
        )
        if (
            lposts > config.site.daily_sub_posting_limit
            or tposts > config.site.daily_site_posting_limit
        ):
            return error_response(_("You have posted too much today."))

    if len(form.title.data.strip(misc.WHITESPACE)) < 3:
        return error_response(
            _("Title is too short and/or contains whitespace characters.")
        )

    fileid = False
    img = ""
    ptype = 0
    if form.ptype.data in ("link", "upload"):
        # TODO: Make a different ptype for uploads?
        ptype = 1
        fupload = upload_file()
        if fupload[0] is not False and fupload[1] is False:
            return (
                engine.get_template("sub/createpost.html").render(
                    {"error": fupload[0], "form": form, "sub": sub, "captcha": captcha}
                ),
                400,
            )

        if fupload[1]:
            form.link.data = file_url(fupload[0])
            fileid = fupload[0]

        if not form.link.data:
            return error_response(_("No link provided."))

        try:
            lx = SubPost.select(SubPost.pid).where(SubPost.sid == sub.sid)
            lx = lx.where(SubPost.link == form.link.data).where(SubPost.deleted == 0)
            monthago = datetime.utcnow() - timedelta(days=30)
            post = lx.where(SubPost.posted > monthago).get()
            return error_response(
                _(
                    'This link was <a href="%(link)s">recently posted</a> on this sub.',
                    link=url_for("sub.view_post", sub=sub.name, pid=post.pid),
                )
            )
        except SubPost.DoesNotExist:
            pass

        if misc.is_domain_banned(form.link.data.lower(), domain_type="link"):
            return error_response(_("This domain is banned."))

        img = "deferred"
    elif form.ptype.data == "poll":
        ptype = 3
        # check if we got at least three options
        options = form.options.data
        options = [
            x for x in options if len(x.strip(misc.WHITESPACE)) > 0
        ]  # Remove empty strings
        if len(options) < 2:
            return error_response(_("Not enough poll options provided."))

        for p in options:
            if len(p) > 128:
                return error_response(_("Poll option text is too long."))

        if form.closetime.data:
            try:
                closetime = datetime.strptime(
                    form.closetime.data, "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                if (closetime - datetime.utcnow()) > timedelta(days=60):
                    return error_response(
                        _("Poll closing time is too far in the future.")
                    )
            except ValueError:
                return error_response(_("Invalid closing time."))

            if datetime.utcnow() > closetime:
                return error_response(_("The closing time is in the past!"))
    elif form.ptype.data == "text":
        ptype = 0

    self_vote = 1 if config.site.self_voting.posts else 0
    post = SubPost.create(
        sid=sub.sid,
        uid=current_user.uid,
        title=form.title.data,
        content=form.content.data if ptype != 1 or config.site.link_post_text else "",
        link=form.link.data if ptype == 1 else None,
        posted=datetime.utcnow(),
        score=self_vote,
        upvotes=self_vote,
        downvotes=0,
        deleted=0,
        comments=0,
        ptype=ptype,
        nsfw=form.nsfw.data if not sub.nsfw else 1,
        noreplies=form.noreplies.data,
        thumbnail=img,
        flair=flair,
    )
    thumbnail_store = [(SubPost, "pid", post.pid)]

    if form.ptype.data == "poll":
        # Create SubPostPollOption objects...
        # noinspection PyUnboundLocalVariable
        poll_options = [{"pid": post.pid, "text": x} for x in options]
        SubPostPollOption.insert_many(poll_options).execute()
        # apply all poll options..
        if form.hideresults.data:
            SubPostMetadata.create(pid=post.pid, key="hide_results", value=1)

        if form.closetime.data:
            # noinspection PyUnboundLocalVariable
            SubPostMetadata.create(
                pid=post.pid,
                key="poll_closes_time",
                value=int(closetime.replace(tzinfo=timezone.utc).timestamp()),
            )

    Sub.update(posts=Sub.posts + 1).where(Sub.sid == sub.sid).execute()
    addr = url_for("sub.view_post", sub=sub.name, pid=post.pid)
    posts = misc.getPostList(
        misc.postListQueryBase(
            nofilter=True, filter_shadowbanned=True, filter_private_posts=True
        ).where(SubPost.pid == post.pid),
        "new",
        1,
    )

    # Set it up so socketio recipient can use their own NSFW setting on NSFW content.
    if posts and posts[0]["nsfw"]:
        posts[0]["blur"] = "placeholder-nsfw-blur"

    defaults = [
        x.value for x in SiteMetadata.select().where(SiteMetadata.key == "default")
    ]
    show_sidebar = sub.sid in defaults or not config.site.recent_activity.defaults_only
    show_sidebar = show_sidebar and not config.site.recent_activity.comments_only
    socketio.emit(
        "thread",
        {
            "addr": addr,
            "sub": sub.name,
            "type": form.ptype.data,
            "show_sidebar": show_sidebar,
            "user": current_user.name,
            "pid": post.pid,
            "sid": sub.sid,
            "title": post.title,
            "nsfw": post.nsfw,
            "noreplies": post.noreplies,
            "post_url": url_for("sub.view_post", sub=sub.name, pid=post.pid),
            "sub_url": url_for("sub.view_sub", sub=sub.name),
            "html": engine.get_template("shared/post.html").render(
                {"posts": posts, "sub": False}
            ),
        },
        namespace="/snt",
        room="/all/new",
    )

    # XXX: The auto-upvote is placed *after* broadcasting the post via socketio so that the upvote arrow
    # does not appear highlighted to everybody.
    if config.site.self_voting.posts:
        SubPostVote.create(uid=current_user.uid, pid=post.pid, positive=True)
        User.update(given=User.given + 1).where(User.uid == current_user.uid).execute()
        # We send a yourvote message so that the upvote arrow *does* appear highlighted to the creator.
        socketio.emit(
            "yourvote",
            {"pid": post.pid, "status": 1, "score": post.score},
            namespace="/snt",
            room="user" + current_user.uid,
        )

    if fileid:
        upload = UserUploads.create(
            pid=post.pid,
            uid=current_user.uid,
            fileid=fileid,
            thumbnail=img if img else "",
            status=0,
        )
        thumbnail_store.append((UserUploads, "xid", upload.xid))

    if img == "deferred":
        if fileid:
            create_thumbnail(fileid, thumbnail_store)
        else:
            create_thumbnail_external(form.link.data, thumbnail_store)

    misc.workWithMentions(form.content.data, None, post, sub)
    misc.workWithMentions(form.title.data, None, post, sub)

    # We check if automatic "Ready Steady Check" badge assignment is enabled in config
    # Then we assign the badge only if the user does not already have it
    if config.site.auto_rsc:
        user_badges = Badges.badges_for_user(current_user.uid)
        badge_ids = [badge.bid for badge in user_badges]
        if 5 not in badge_ids:
            Badges.assign_userbadge(current_user.uid, 5)

    return redirect(addr)


@bp.route("/random")
def random_sub():
    """Here we get a random sub"""
    rsub = Sub.select(Sub.name).order_by(pdb.random()).limit(1)
    return redirect(url_for("sub.view_sub", sub=rsub.get().name))


@bp.route("/createsub", methods=["GET", "POST"])
@login_required
@ratelimit(POSTING_LIMIT)
def create_sub():
    """Here we can view the create sub form"""
    form = CreateSubForm()
    if not form.validate():
        return engine.get_template("sub/create.html").render(
            {"error": misc.get_errors(form, True), "csubform": form}
        )

    if not misc.allowedNames.match(form.subname.data):
        return engine.get_template("sub/create.html").render(
            {"error": _("Sub name has invalid characters"), "csubform": form}
        )

    if form.subname.data.lower() in (
        "all",
        "new",
        "hot",
        "top",
        "commented",
        "admin",
        "home",
    ):
        return engine.get_template("sub/create.html").render(
            {"error": _("Invalid sub name"), "csubform": form}
        )

    try:
        Sub.get(fn.Lower(Sub.name) == form.subname.data.lower())
        return engine.get_template("sub/create.html").render(
            {"error": _("Sub is already registered"), "csubform": form}
        )
    except Sub.DoesNotExist:
        pass

    if config.site.sub_creation_admin_only and not current_user.admin:
        return engine.get_template("sub/create.html").render(
            {
                "error": _(
                    "Only Site Admins may create new subs. Please contact an administrator to request a new sub.",
                    level=config.site.sub_creation_min_level,
                ),
                "csubform": form,
            }
        )

    level = misc.get_user_level(current_user.uid)[0]
    if not config.app.development:
        min_level = config.site.sub_creation_min_level
        if min_level != 0 and level < min_level and not current_user.admin:
            return engine.get_template("sub/create.html").render(
                {
                    "error": _(
                        "You must be at least level %(level)i.", level=min_level
                    ),
                    "csubform": form,
                }
            )

        owned = (
            SubMod.select()
            .where(SubMod.uid == current_user.uid)
            .where((SubMod.power_level == 0) & (~SubMod.invite))
            .count()
        )
        if owned >= 20 and not current_user.admin:
            return engine.get_template("sub/create.html").render(
                {
                    "error": _(
                        "You cannot own more than %(max)i subs.",
                        max=config.site.sub_ownership_limit,
                    ),
                    "csubform": form,
                }
            )

        if min_level != 0 and owned >= level - 1 and not current_user.admin:
            return engine.get_template("sub/create.html").render(
                {
                    "error": _(
                        "You cannot own more than %(max)i subs. Try leveling up your account.",
                        max=level - 1,
                    ),
                    "csubform": form,
                }
            )

    sub = Sub.create(
        sid=uuid.uuid4(),
        name=form.subname.data,
        title=form.title.data,
        nsfw=form.nsfw.data,
        private=form.private.data,
    )

    smd = [dict(sid=sub.sid, key="mod", value=current_user.uid)]
    for key in ["allow_text_posts", "allow_link_posts", "allow_upload_posts"]:
        smd.append(dict(sid=sub.sid, key=key, value="1"))
    SubMetadata.insert_many(smd).execute()

    SubMod.create(sid=sub.sid, uid=current_user.uid, power_level=0)
    SubStylesheet.create(sid=sub.sid, content="", source="/* CSS here */")

    # admin/site log
    misc.create_sublog(
        misc.LOG_TYPE_SUB_CREATE, uid=current_user.uid, sid=sub.sid, admin=True
    )

    SubSubscriber.create(uid=current_user.uid, sid=sub.sid, status=1)

    return redirect(url_for("sub.view_sub", sub=form.subname.data))
