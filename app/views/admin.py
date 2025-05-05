"""Admin endpoints"""

import time
import re
import datetime
import random
from io import BytesIO

from peewee import fn, JOIN
import pyotp
import qrcode
from flask import (
    Blueprint,
    abort,
    redirect,
    url_for,
    session,
    request,
    send_file,
)
from flask_login import login_required, current_user
from flask_babel import _

from .. import misc, auth_provider
from ..config import config
from ..forms import (
    CsrfTokenOnlyForm,
    TOTPForm,
    LogOutForm,
    UseInviteCodeForm,
    AssignUserBadgeForm,
    EditModForm,
    BanDomainForm,
    WikiForm,
    CreateInviteCodeForm,
    UpdateInviteCodeForm,
    EditBadgeForm,
    NewBadgeForm,
    SetSubOfTheDayForm,
    ChangeConfigSettingForm,
)
from ..models import (
    UserMetadata,
    User,
    Sub,
    SubPost,
    SubPostComment,
    SubPostCommentVote,
    SubPostVote,
    SiteMetadata,
    SubSubscriber,
)
from ..models import UserUploads, InviteCode, Wiki
from ..misc import engine, getReports, getSubMods
from ..badges import badges

bp = Blueprint("admin", __name__)


@bp.route("/admin/auth", methods=["GET", "POST"])
@login_required
def auth():
    if not current_user.can_admin:
        abort(404)

    if config.auth.provider == "KEYCLOAK" and config.auth.keycloak.use_oidc:
        # If the user is an admin, perform L2 auth via keycloak (should ask for TOTP)
        return redirect(auth_provider.get_login_url(acr="aal2"))

    form = TOTPForm()
    try:
        user_secret = UserMetadata.get(
            (UserMetadata.uid == current_user.uid) & (UserMetadata.key == "totp_secret")
        )
    except UserMetadata.DoesNotExist:
        user_secret = UserMetadata.create(
            uid=current_user.uid, key="totp_secret", value=pyotp.random_base32(64)
        )

    template = "admin/totp_setup.html"
    try:
        UserMetadata.get(
            (UserMetadata.uid == current_user.uid)
            & (UserMetadata.key == "totp_setup_finished")
        )
        setup = False
        template = "admin/totp.html"
    except UserMetadata.DoesNotExist:
        setup = True
        pass

    if form.validate_on_submit():
        totp = pyotp.TOTP(user_secret.value)
        if totp.verify(form.totp.data):
            session["apriv"] = time.time()
            if setup:
                UserMetadata.create(
                    uid=current_user.uid, key="totp_setup_finished", value="1"
                )
            return redirect(url_for("admin.index"))
        else:
            return engine.get_template(template).render(
                {"authform": form, "error": _("Invalid or expired token.")}
            )

    return engine.get_template(template).render({"authform": form, "error": None})


@bp.route("/totp_image", methods=["GET"])
@login_required
def get_totp_image():
    """
    Returns a QR code used to set up TOTP
    """
    if not current_user.can_admin:
        abort(404)

    try:
        user_secret = UserMetadata.get(
            (UserMetadata.uid == current_user.uid) & (UserMetadata.key == "totp_secret")
        )
    except UserMetadata.DoesNotExist:
        user_secret = UserMetadata.create(
            uid=current_user.uid, key="totp_secret", value=pyotp.random_base32(64)
        )

    try:
        UserMetadata.get(
            (UserMetadata.uid == current_user.uid)
            & (UserMetadata.key == "totp_setup_finished")
        )
        # TOTP setup already finished, we won't reveal the secret anymore
        return abort(403)
    except UserMetadata.DoesNotExist:
        pass

    uri = pyotp.totp.TOTP(user_secret.value).provisioning_uri(
        name=current_user.name, issuer_name=config.site.name
    )
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(uri)

    img = qr.make_image(fill_color="black", back_color="white")

    img_io = BytesIO()
    img.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    if not current_user.can_admin:
        abort(404)
    form = LogOutForm()
    if form.validate():
        del session["apriv"]
    return redirect(url_for("home.index"))


@bp.route("/")
@login_required
def index():
    """WIP: View users. assign badges, etc"""
    if not current_user.can_admin:
        abort(404)

    if not current_user.admin:
        return redirect(url_for("admin.auth"))

    users = User.select().count()
    subs = Sub.select().count()
    posts = SubPost.select().count()
    comms = SubPostComment.select().count()
    ups = SubPostVote.select().where(SubPostVote.positive == 1).count()
    downs = SubPostVote.select().where(SubPostVote.positive == 0).count()
    ups += SubPostCommentVote.select().where(SubPostCommentVote.positive == 1).count()
    downs += SubPostCommentVote.select().where(SubPostCommentVote.positive == 0).count()

    invite = UseInviteCodeForm()
    invite.minlevel.data = config.site.invite_level
    invite.maxcodes.data = config.site.invite_max

    subOfTheDay = SetSubOfTheDayForm()

    return engine.get_template("admin/admin.html").render(
        {
            "subs": subs,
            "posts": posts,
            "ups": ups,
            "downs": downs,
            "users": users,
            "comms": comms,
            "subOfTheDay": subOfTheDay,
            "useinvitecodeform": invite,
            "csrf_form": CsrfTokenOnlyForm(),
            "ann": misc.getAnnouncement(),
        }
    )


@bp.route("/users", defaults={"page": 1})
@bp.route("/users/<int:page>")
@login_required
def users(page):
    """WIP: View users."""
    if not current_user.is_admin():
        abort(404)

    sort_by = request.args.get("sort", "joindate")  # Default sort by last login
    sort_dir = request.args.get("dir", "desc")  # Default direction descending

    postcount = (
        SubPost.select(SubPost.uid, fn.Count(SubPost.pid).alias("post_count"))
        .group_by(SubPost.uid)
        .alias("post_count")
    )
    commcount = (
        SubPostComment.select(
            SubPostComment.uid, fn.Count(SubPostComment.cid).alias("comment_count")
        )
        .group_by(SubPostComment.uid)
        .alias("j2")
    )
    last_login_query = UserMetadata.select(
        UserMetadata.uid, UserMetadata.value.alias("last_login")
    ).where(UserMetadata.key == "last_login")

    users = User.select(
        User.name,
        User.status,
        User.uid,
        User.joindate,
        User.email,
        postcount.c.post_count.alias("post_count"),
        commcount.c.comment_count,
        last_login_query.c.last_login.alias("last_login"),
    )
    users = users.join(postcount, JOIN.LEFT_OUTER, on=User.uid == postcount.c.uid)
    users = users.join(commcount, JOIN.LEFT_OUTER, on=User.uid == commcount.c.uid)
    users = users.join(
        last_login_query, JOIN.LEFT_OUTER, on=(User.uid == last_login_query.c.uid)
    )

    if sort_by == "name":
        order_clause = User.name if sort_dir == "asc" else User.name.desc()
    elif sort_by == "posts":
        # Use SQL COALESCE to handle NULL values
        order_clause = (
            fn.COALESCE(postcount.c.post_count, 0)
            if sort_dir == "asc"
            else fn.COALESCE(postcount.c.post_count, 0).desc()
        )
    elif sort_by == "comments":
        order_clause = (
            fn.COALESCE(commcount.c.comment_count, 0)
            if sort_dir == "asc"
            else fn.COALESCE(commcount.c.comment_count, 0).desc()
        )
    elif sort_by == "status":
        order_clause = User.status if sort_dir == "asc" else User.status.desc()
    elif sort_by == "email":
        order_clause = User.email if sort_dir == "asc" else User.email.desc()
    elif sort_by == "login":
        if sort_dir == "asc":
            # For ascending order, NULL values first, then oldest to newest dates
            order_clause = fn.COALESCE(
                last_login_query.c.last_login, "1970-01-01 00:00:00"
            )
        else:
            # For descending order, newest dates first, then NULL values
            order_clause = fn.COALESCE(
                last_login_query.c.last_login, "1970-01-01 00:00:00"
            ).desc()
    elif sort_by == "joindate":
        order_clause = User.joindate if sort_dir == "asc" else User.joindate.desc()

    users = users.order_by(order_clause).paginate(page, 50).dicts()

    return engine.get_template("admin/users.html").render(
        {
            "users": users,
            "page": page,
            "term": None,
            "admin_route": "admin.users",
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        }
    )


@bp.route("/subsubscribers", defaults={"page": 1})
@bp.route("/subsubscribers/<sub>/<int:page>")
@login_required
def subsubscribers(sub, page):
    """WIP: View sub subscribers."""
    if not current_user.is_admin():
        abort(404)

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        abort(404)

    users = (
        User.select(User.name)
        .join(SubSubscriber)
        .where(
            (SubSubscriber.sid == sub.sid)
            & (SubSubscriber.uid == User.uid)
            & (SubSubscriber.status == 1)
        )
        .order_by(User.name.asc())
        .paginate(page, 50)
        .dicts()
    )
    return engine.get_template("admin/subsubscribers.html").render(
        {
            "sub": sub,
            "users": users,
            "page": page,
            "admin_route": "admin.subsubscribers",
        }
    )


@bp.route("/userbadges")
@login_required
def userbadges():
    """WIP: Assign user badges."""
    if not current_user.is_admin():
        abort(404)

    form = AssignUserBadgeForm()
    form.badge.choices = [(badge.bid, badge.name) for badge in badges]
    ct = UserMetadata.select().where(UserMetadata.key == "badge").count()

    return engine.get_template("admin/userbadges.html").render(
        {
            "badges": badges,
            "assignuserbadgeform": form,
            "ct": ct,
            "admin_route": "admin.userbadges",
        }
    )


@bp.route("/userbadges/new", methods=["GET", "POST"])
@login_required
def newbadge():
    """Edit badge information."""
    if not current_user.is_admin():
        abort(404)

    form = NewBadgeForm()
    form.trigger.choices = [(None, "No Trigger")] + [
        (trigger, trigger) for trigger in badges.triggers()
    ]
    if form.validate_on_submit():
        icon = request.files.get(form.icon.name)
        badges.new_badge(
            name=form.name.data,
            alt=form.alt.data,
            score=form.score.data,
            trigger=form.trigger.data,
            rank=form.rank.data,
            icon=icon,
        )
        return redirect(url_for("admin.userbadges"))
    return engine.get_template("admin/editbadge.html").render(
        {
            "form": form,
            "badge": None,
            "new": True,
        }
    )


@bp.route("/userbadges/edit/<int:badge>", methods=["GET", "POST"])
@login_required
def editbadge(badge):
    """Edit badge information."""
    if not current_user.is_admin():
        abort(404)

    badge = badges[badge]

    form = EditBadgeForm()
    form.trigger.choices = [(None, "No Trigger")] + [
        (trigger, trigger) for trigger in badges.triggers()
    ]
    if form.validate_on_submit():
        icon = request.files.get(form.icon.name)
        badges.update_badge(
            bid=badge.bid,
            name=form.name.data,
            alt=form.alt.data,
            score=form.score.data,
            trigger=form.trigger.data,
            rank=form.rank.data,
            icon=icon,
        )
        return redirect(url_for("admin.userbadges"))
    form.name.data = badge.name
    form.alt.data = badge.alt
    form.score.data = badge.score
    form.trigger.data = badge.trigger
    form.rank.data = badge.rank
    return engine.get_template("admin/editbadge.html").render(
        {
            "form": form,
            "badge": badge,
            "new": False,
        }
    )


@bp.route("/userbadges/delete/<int:badge>", methods=["POST"])
@login_required
def deletebadge(badge):
    """Edit badge information."""
    if not current_user.is_admin():
        abort(404)

    badges.delete_badge(badge)
    return redirect(url_for("admin.userbadges"))


@bp.route("/invitecodes", defaults={"page": 1}, methods=["GET", "POST"])
@bp.route("/invitecodes/<int:page>", methods=["GET", "POST"])
@login_required
def invitecodes(page):
    """
    View and configure Invite Codes
    """

    def map_style(code):
        if code["uses"] >= code["max_uses"]:
            return "expired"
        elif (
            code["expires"] is not None and code["expires"] < datetime.datetime.utcnow()
        ):
            return "expired"
        else:
            return ""

    if not current_user.is_admin():
        abort(404)

    invite_codes = (
        InviteCode.select(
            InviteCode.id,
            InviteCode.code,
            User.name.alias("created_by"),
            InviteCode.created.alias("created"),
            InviteCode.expires,
            InviteCode.uses,
            InviteCode.max_uses,
        )
        .join(User)
        .order_by(InviteCode.created.desc())
        .paginate(page, 50)
        .dicts()
    )

    code_users = (
        UserMetadata.select(
            User.name.alias("used_by"), User.status, UserMetadata.value.alias("code")
        )
        .where(
            (UserMetadata.key == "invitecode")
            & (UserMetadata.value << set([x["code"] for x in invite_codes]))
        )
        .join(User)
        .order_by(User.joindate)
        .dicts()
    )

    used_by = {}
    for user in code_users:
        if not user["code"] in used_by:
            used_by[user["code"]] = []
        used_by[user["code"]].append((user["used_by"], user["status"]))

    update_form = UpdateInviteCodeForm()

    for code in invite_codes:
        code["style"] = map_style(code)
        code["used_by"] = used_by.get(code["code"], [])
        code["created"] = code["created"].strftime("%Y-%m-%dT%H:%M:%SZ")
        if code["expires"] is not None:
            code["expires"] = code["expires"].strftime("%Y-%m-%dT%H:%M:%SZ")

    invite_form = UseInviteCodeForm()
    invite_form.maxcodes.data = config.site.invite_max
    invite_form.minlevel.data = config.site.invite_level

    form = CreateInviteCodeForm()

    if form.validate_on_submit():
        invite = InviteCode()
        invite.user = current_user.uid
        if form.code.data:
            invite.code = form.code.data
        else:
            invite.code = "".join(
                random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(32)
            )

        if form.expires.data:
            invite.expires = form.expires.data

        invite.max_uses = form.uses.data
        invite.save()
        return redirect(url_for("admin.invitecodes", page=page))

    if update_form.validate_on_submit():
        if update_form.etype.data == "at" and update_form.expires.data is None:
            update_form.etype.data = "never"
        ids = [
            invite_codes[int(code.id.split("-")[1])]["id"] for code in update_form.codes
        ]
        if ids:
            if update_form.etype.data == "now":
                expires = datetime.datetime.utcnow()
            elif update_form.etype.data == "never" or update_form.expires.data is None:
                expires = None
            else:
                expires = form.expires.data
            InviteCode.update(expires=expires).where(InviteCode.id << ids).execute()
        return redirect(url_for("admin.invitecodes", page=page))

    return engine.get_template("admin/invitecodes.html").render(
        {
            "useinvitecodeform": invite_form,
            "invite_codes": invite_codes,
            "page": page,
            "error": misc.get_errors(form, True),
            "form": form,
            "update_form": update_form,
        }
    )


@bp.route("/admins")
@login_required
def view():
    """WIP: View admins."""
    if not current_user.is_admin():
        abort(404)
    admins = UserMetadata.select().where(UserMetadata.key == "admin")

    postcount = (
        SubPost.select(SubPost.uid, fn.Count(SubPost.pid).alias("post_count"))
        .group_by(SubPost.uid)
        .alias("post_count")
    )
    commcount = (
        SubPostComment.select(
            SubPostComment.uid, fn.Count(SubPostComment.cid).alias("comment_count")
        )
        .group_by(SubPostComment.uid)
        .alias("j2")
    )
    last_login_query = UserMetadata.select(
        UserMetadata.uid, UserMetadata.value.alias("last_login")
    ).where(UserMetadata.key == "last_login")

    users = User.select(
        User.name,
        User.status,
        User.uid,
        User.joindate,
        User.email,
        postcount.c.post_count.alias("post_count"),
        commcount.c.comment_count,
        last_login_query.c.last_login.alias("last_login"),
    )
    users = users.join(postcount, JOIN.LEFT_OUTER, on=User.uid == postcount.c.uid)
    users = users.join(commcount, JOIN.LEFT_OUTER, on=User.uid == commcount.c.uid)
    users = users.join(
        last_login_query, JOIN.LEFT_OUTER, on=(User.uid == last_login_query.c.uid)
    )
    users = (
        users.where(User.uid << [x.uid for x in admins])
        .order_by(User.joindate.asc())
        .dicts()
    )

    return engine.get_template("admin/users.html").render(
        {
            "users": users,
            "admin_route": "admin.view",
            "page": None,
            "term": None,
            "sort_by": None,
            "sort_dir": None,
        }
    )


@bp.route("/usersearch/<term>")
@login_required
def users_search(term):
    """WIP: Search users."""
    if not current_user.is_admin():
        abort(404)
    term = re.sub(r"[^A-Za-z0-9.\-_]+", "", term)

    postcount = (
        SubPost.select(SubPost.uid, fn.Count(SubPost.pid).alias("post_count"))
        .group_by(SubPost.uid)
        .alias("post_count")
    )
    commcount = (
        SubPostComment.select(
            SubPostComment.uid, fn.Count(SubPostComment.cid).alias("comment_count")
        )
        .group_by(SubPostComment.uid)
        .alias("j2")
    )
    last_login_query = UserMetadata.select(
        UserMetadata.uid, UserMetadata.value.alias("last_login")
    ).where(UserMetadata.key == "last_login")

    users = User.select(
        User.name,
        User.status,
        User.uid,
        User.joindate,
        User.email,
        postcount.c.post_count,
        commcount.c.comment_count,
        last_login_query.c.last_login.alias("last_login"),
    )
    users = users.join(postcount, JOIN.LEFT_OUTER, on=User.uid == postcount.c.uid)
    users = users.join(commcount, JOIN.LEFT_OUTER, on=User.uid == commcount.c.uid)
    users = users.join(
        last_login_query, JOIN.LEFT_OUTER, on=(User.uid == last_login_query.c.uid)
    )
    users = users.where(User.name.contains(term)).order_by(User.joindate.desc()).dicts()

    return engine.get_template("admin/users.html").render(
        {
            "users": users,
            "term": term,
            "admin_route": "admin.users_search",
            "page": None,
            "sort_by": None,
            "sort_dir": None,
        }
    )


@bp.route("/subs", defaults={"page": 1})
@bp.route("/subs/<int:page>")
@login_required
def subs(page):
    """WIP: View subs. Assign new owners"""
    if not current_user.is_admin():
        abort(404)
    subs = Sub.select().order_by(Sub.name).paginate(page, 50)
    subMods = {sub.sid: getSubMods(sub.sid) for sub in subs}

    return engine.get_template("admin/subs.html").render(
        {
            "subs": subs,
            "page": page,
            "term": None,
            "admin_route": "admin.subs",
            "editmodform": EditModForm(),
            "subMods": subMods,
        }
    )


@bp.route("/subsearch/<term>", defaults={"page": 1})
@bp.route("/subsearch/<term>/<int:page>")
@login_required
def subs_search(term, page):
    """Search for a sub with pagination."""
    if not current_user.is_admin():
        abort(404)

    term = re.sub(r"[^A-Za-zÁ-ž0-9.\-_]+", "", term)

    subs = (
        Sub.select()
        .where(Sub.name.contains(term))
        .order_by(Sub.name)
        .paginate(page, 50)
    )
    subMods = {sub.sid: getSubMods(sub.sid) for sub in subs}

    return engine.get_template("admin/subs.html").render(
        {
            "subs": subs,
            "term": term,
            "page": page,
            "subMods": subMods,
            "admin_route": "admin.subs_search",
            "editmodform": EditModForm(),
        }
    )


@bp.route("/posts/all/", defaults={"page": 1})
@bp.route("/posts/all/<int:page>")
@login_required
def posts(page):
    """WIP: View posts."""
    if not current_user.is_admin():
        abort(404)
    posts = misc.getPostList(
        misc.postListQueryBase(
            include_deleted_posts=True,
            filter_private_posts=False,
            filter_shadowbanned=False,
        ),
        "new",
        page,
        page_size=50,
    )
    return engine.get_template("admin/posts.html").render(
        {
            "page": page,
            "posts": posts,
            "posts_count": len(posts),
            "admin_route": "admin.posts",
        }
    )


@bp.route("/postvoting/<term>", defaults={"page": 1})
@bp.route("/postvoting/<term>/<int:page>")
@login_required
def post_voting(page, term):
    """WIP: View post voting habits"""
    if not current_user.is_admin():
        abort(404)

    try:
        user = User.get(fn.Lower(User.name) == term.lower())
        msg = []

        votes = (
            SubPostVote.select(
                SubPostVote.positive,
                SubPostVote.pid,
                User.name,
                SubPostVote.datetime,
                SubPost.title,
                Sub.name.alias("sub"),
            )
            .join(SubPost, JOIN.LEFT_OUTER, on=(SubPost.pid == SubPostVote.pid))
            .join(Sub)
            .switch(SubPost)
            .join(User, JOIN.LEFT_OUTER, on=(SubPost.uid == User.uid))
            .where((SubPostVote.uid == user.uid) & (SubPost.uid != user.uid))
            .order_by(SubPostVote.datetime.desc())
            .paginate(page, 50)
            .dicts()
        )

        votes_count = (
            SubPostVote.select()
            .join(SubPost, JOIN.LEFT_OUTER)
            .where((SubPostVote.uid == user.uid) & (SubPost.uid != user.uid))
            .count()
        )

    except User.DoesNotExist:
        votes = []
        msg = "user not found"
        votes_count = 0

    return engine.get_template("admin/postvoting.html").render(
        {
            "page": page,
            "msg": msg,
            "admin_route": "admin.post_voting",
            "votes": votes,
            "votes_count": votes_count,
            "term": term,
        }
    )


@bp.route("/commentvoting/<term>", defaults={"page": 1})
@bp.route("/commentvoting/<term>/<int:page>")
@login_required
def comment_voting(page, term):
    if not current_user.is_admin():
        abort(404)

    try:
        user = User.get(fn.Lower(User.name) == term.lower())
        msg = []
        votes = (
            SubPostCommentVote.select(
                SubPostCommentVote.positive,
                SubPostCommentVote.cid,
                SubPostComment.uid,
                SubPostComment.content,
                User.name,
                SubPostCommentVote.datetime,
                SubPost.pid,
                Sub.name.alias("sub"),
            )
            .join(
                SubPostComment,
                JOIN.LEFT_OUTER,
                on=(SubPostComment.cid == SubPostCommentVote.cid),
            )
            .join(SubPost)
            .join(Sub)
            .switch(SubPostComment)
            .join(User, JOIN.LEFT_OUTER, on=(SubPostComment.uid == User.uid))
            .where(SubPostCommentVote.uid == user.uid)
            .order_by(SubPostCommentVote.datetime.desc())
            .paginate(page, 50)
            .dicts()
        )

        votes_count = (
            SubPostCommentVote.select()
            .where(SubPostCommentVote.uid == user.uid)
            .count()
        )

    except User.DoesNotExist:
        votes = []
        msg = "user not found"
        votes_count = 0

    return engine.get_template("admin/commentvoting.html").render(
        {
            "page": page,
            "msg": msg,
            "admin_route": "admin.comment_voting",
            "votes": votes,
            "votes_count": votes_count,
            "term": term,
        }
    )


@bp.route("/post/search/<term>")
@login_required
def post_search(term):
    """WIP: Post search result."""
    if not current_user.is_admin():
        abort(404)
    term = re.sub(r"[^A-Za-zÁ-ž0-9.\-_]+", "", term)
    try:
        post = SubPost.get(SubPost.pid == term)
    except SubPost.DoesNotExist:
        return abort(404)

    votes = (
        SubPostVote.select(SubPostVote.positive, SubPostVote.datetime, User.name)
        .join(User)
        .where(SubPostVote.pid == post.pid)
        .dicts()
    )
    upcount = post.votes.where(SubPostVote.positive == "1").count()
    downcount = post.votes.where(SubPostVote.positive == "0").count()

    pcount = post.uid.posts.count()
    ccount = post.uid.comments.count()
    comms = (
        SubPostComment.select(
            SubPostComment.score,
            SubPostComment.content,
            SubPostComment.status,
            SubPostComment.cid,
            User.name,
        )
        .join(User)
        .where(SubPostComment.pid == post.pid)
        .order_by(SubPostComment.time.desc())
        .dicts()
    )

    return engine.get_template("admin/post.html").render(
        {
            "sub": post.sid,
            "post": post,
            "votes": votes,
            "ccount": ccount,
            "pcount": pcount,
            "upcount": upcount,
            "downcount": downcount,
            "comms": comms,
            "comms_count": len(comms),
            "user": post.uid,
        }
    )


@bp.route("/domains/<domain_type>", defaults={"page": 1})
@bp.route("/domains/<domain_type>/<int:page>")
@login_required
def domains(domain_type, page):
    """WIP: View Banned Domains"""
    if not current_user.is_admin():
        abort(404)
    if domain_type == "email":
        key = "banned_email_domain"
        title = _("Banned Email Domains")
    elif domain_type == "link":
        key = "banned_domain"
        title = _("Banned Domains")
    else:
        return abort(404)
    domains = (
        SiteMetadata.select()
        .where(SiteMetadata.key == key)
        .order_by(SiteMetadata.value)
    )
    return engine.get_template("admin/domains.html").render(
        {
            "domains": domains,
            "dtitle": title,
            "domain_type": domain_type,
            "page": page,
            "bandomainform": BanDomainForm(),
        }
    )


@bp.route("/uploads", defaults={"page": 1})
@bp.route("/uploads/<int:page>")
@login_required
def user_uploads(page):
    """View user uploads"""
    if not current_user.is_admin():
        abort(404)
    uploads = (
        UserUploads.select(
            UserUploads.thumbnail,
            UserUploads.xid,
            UserUploads.uid,
            UserUploads.pid,
            UserUploads.fileid,
            SubPost.deleted,
            User.name,  # Include User.name
        )
        .join(SubPost)
        .join(User, on=(User.uid == UserUploads.uid))  # Join User table
        .order_by(UserUploads.pid.desc())
        .paginate(page, 30)
        .dicts()
    )
    users = (
        User.select(User.name).join(UserMetadata).where(UserMetadata.key == "canupload")
    )
    return engine.get_template("admin/uploads.html").render(
        {
            "page": page,
            "uploads": uploads,
            "users": users,
        }
    )


@bp.route("/reports", defaults={"page": 1})
@bp.route("/reports/<int:page>")
@login_required
def reports(page):
    if not current_user.is_admin():
        abort(404)

    reports = getReports("admin", "all", page)

    return engine.get_template("admin/reports.html").render(
        {
            "reports": reports,
            "page": page,
            "sub": False,
            "subInfo": False,
            "subMods": False,
        }
    )


@bp.route("/configuration")
@login_required
def configure():
    if not current_user.is_admin():
        abort(404)

    form = ChangeConfigSettingForm()

    config_data = sorted(config.get_mutable_items(), key=(lambda x: x["name"]))
    return engine.get_template("admin/configuration.html").render(
        {"form": form, "config_data": config_data}
    )


@bp.route("/wiki", defaults={"page": 1})
@bp.route("/wiki/<int:page>")
@login_required
def wiki(page):
    if not current_user.is_admin():
        abort(404)

    pages = Wiki.select().where(Wiki.is_global)

    return engine.get_template("admin/wiki.html").render({"wikis": pages, "page": page})


@bp.route("/wiki/create", methods=["GET", "POST"])
@login_required
def create_wiki():
    if not current_user.is_admin():
        abort(404)

    form = WikiForm()

    if form.validate_on_submit():
        Wiki.create(
            slug=form.slug.data,
            title=form.title.data,
            content=form.content.data,
            is_global=True,
            sub=None,
        )
        return redirect(url_for("admin.wiki"))
    return engine.get_template("admin/createwiki.html").render(
        {"form": form, "error": misc.get_errors(form, True)}
    )


@bp.route("/wiki/edit/<slug>", methods=["GET"])
@login_required
def edit_wiki(slug):
    if not current_user.is_admin():
        abort(404)

    form = WikiForm()
    try:
        wiki_page = Wiki.select().where(Wiki.slug == slug).where(Wiki.is_global).get()
    except Wiki.DoesNotExist:
        return abort(404)

    form.slug.data = wiki_page.slug
    form.content.data = wiki_page.content
    form.title.data = wiki_page.title

    return engine.get_template("admin/createwiki.html").render(
        {"form": form, "error": misc.get_errors(form, True)}
    )


@bp.route("/wiki/edit/<slug>", methods=["POST"])
@login_required
def edit_wiki_save(slug):
    if not current_user.is_admin():
        abort(404)

    form = WikiForm()
    try:
        wiki_page = Wiki.select().where(Wiki.slug == slug).where(Wiki.is_global).get()
    except Wiki.DoesNotExist:
        return abort(404)

    if form.validate_on_submit():
        wiki_page.slug = form.slug.data
        wiki_page.title = form.title.data
        wiki_page.content = form.content.data
        wiki_page.updated = datetime.datetime.utcnow()
        wiki_page.save()
        return redirect(url_for("admin.wiki"))

    return engine.get_template("admin/createwiki.html").render(
        {"form": form, "error": misc.get_errors(form, True)}
    )


@bp.route("/wiki/delete/<slug>", methods=["GET"])
@login_required
def delete_wiki(slug):
    if not current_user.is_admin():
        abort(404)

    # XXX: This could be an ajax call
    try:
        wiki_page = Wiki.select().where(Wiki.slug == slug).where(Wiki.is_global).get()
    except Wiki.DoesNotExist:
        return abort(404)

    wiki_page.delete_instance()
    return redirect(url_for("admin.wiki"))
