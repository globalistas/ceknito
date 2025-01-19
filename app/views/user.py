""" Profile and settings endpoints """
from peewee import fn, JOIN
from flask import Blueprint, abort, redirect, url_for, flash, request
from flask_login import login_required, current_user
from flask_babel import _, Locale
from .do import send_password_recovery_email, uid_from_recovery_token
from .do import info_from_email_confirmation_token
from .. import misc
from ..config import config
from ..auth import auth_provider, AuthError, normalize_email
from ..misc import engine, gevent_required
from ..misc import ratelimit, AUTH_LIMIT, SIGNUP_LIMIT, limit_pagination
from ..forms import (
    CsrfTokenOnlyForm,
    EditUserForm,
    EditIgnoreForm,
    CreateUserMessageForm,
    EditAccountForm,
    DeleteAccountForm,
    PasswordRecoveryForm,
)
from ..forms import PasswordResetForm
from ..models import (
    User,
    UserStatus,
    UserUploads,
    UserMessageBlock,
    UserContentBlock,
    UserContentBlockMethod,
    Sub,
    SubMod,
    SubPost,
    SubPostComment,
    UserSaved,
    InviteCode,
)
from ..badges import badges as badges_module

bp = Blueprint("user", __name__)


def view_deleted_user(user):
    return engine.get_template("user/profile.html").render(
        {
            "user": user,
            "level": None,
            "progress": None,
            "postCount": None,
            "commentCount": None,
            "givenScore": None,
            "postScore": None,
            "commentScore": None,
            "userScore": None,
            "invitecodeinfo": None,
            "badges": None,
            "owns": None,
            "mods": None,
            "habits": None,
            "target_user_is_admin": None,
            "msgform": None,
            "ignform": None,
        }
    )


def view_shadownbanned_user(user):
    return engine.get_template("user/profile.html").render(
        {
            "user": user,
            "level": 0,
            "progress": 0,
            "postCount": 0,
            "commentCount": 0,
            "postScore": 0,
            "commentScore": 0,
            "userScore": 0,
            "givenScore": [0, 0, 0],
            "invitecodeinfo": "",
            "badges": "",
            "owns": "",
            "mods": "",
            "habits": "",
            "target_user_is_admin": None,
            "msgform": CreateUserMessageForm(),
            "ignform": EditIgnoreForm(view_messages=None, view_content=None),
        }
    )


@bp.route("/u/<user>")
def view(user):
    """WIP: View user's profile, posts, comments, badges, etc"""
    try:
        user = User.get(fn.lower(User.name) == user.lower())
    except User.DoesNotExist:
        abort(404)

    if user.status == 10 and not current_user.is_admin():
        return view_deleted_user(user)

    if (
        user.status == 6
        and not current_user.can_admin
        and not user.uid == current_user.uid
    ):
        return view_shadownbanned_user(user)

    modsquery = (
        SubMod.select(Sub.name, SubMod.power_level)
        .join(Sub)
        .where((SubMod.uid == user.uid) & (~SubMod.invite) & (Sub.private != 1))
    )
    owns = [x.sub.name for x in modsquery if x.power_level == 0]
    mods = [x.sub.name for x in modsquery if 1 <= x.power_level <= 2]
    invitecodeinfo = misc.getInviteCodeInfo(user.uid)
    badges = badges_module.badges_for_user(user.uid)
    pcount = SubPost.select().where(SubPost.uid == user.uid).count()
    ccount = SubPostComment.select().where(SubPostComment.uid == user.uid).count()
    user_is_admin = misc.is_target_user_admin(user.uid)
    habit = Sub.select(Sub.name, fn.Count(SubPost.pid).alias("count")).join(
        SubPost, JOIN.LEFT_OUTER, on=(SubPost.sid == Sub.sid)
    )
    habit = (
        habit.where(
            (SubPost.uid == user.uid) & (Sub.private == 0)  # Exclude private subs
        )
        .group_by(Sub.sid)
        .order_by(fn.Count(SubPost.pid).desc())
        .limit(10)
    )

    level, xp = misc.get_user_level(user.uid)

    if xp > 0:
        currlv = (level**2) * 10
        nextlv = ((level + 1) ** 2) * 10

        required_xp = nextlv - currlv
        progress = ((xp - currlv) / required_xp) * 100
    else:
        progress = 0

    givenScore = misc.getUserGivenScore(user.uid)

    postScore = misc.getUserPostScore(user.uid)

    commentScore = misc.getUserCommentScore(user.uid)

    userScore = f"{user.score:,}"

    messages = content = "show"
    if (
        current_user.uid != user.uid
        and not user_is_admin
        and not current_user.can_admin
    ):
        blocked = (
            User.select(
                UserMessageBlock.id.is_null(False).alias("hide_messages"),
                UserContentBlock.method,
            )
            .join(
                UserMessageBlock,
                JOIN.LEFT_OUTER,
                on=(
                    (UserMessageBlock.uid == current_user.uid)
                    & (UserMessageBlock.target == user.uid)
                ),
            )
            .join(
                UserContentBlock,
                JOIN.LEFT_OUTER,
                on=(
                    (UserContentBlock.uid == current_user.uid)
                    & (UserContentBlock.target == user.uid)
                ),
            )
            .dicts()
            .get()
        )

        if blocked["hide_messages"]:
            messages = "hide"
        else:
            messages = "show"
        if blocked["method"] is None:
            content = "show"
        elif blocked["method"] == UserContentBlockMethod.BLUR:
            content = "blur"
        else:
            content = "hide"
    ignform = EditIgnoreForm(view_messages=messages, view_content=content)

    return engine.get_template("user/profile.html").render(
        {
            "user": user,
            "level": level,
            "progress": progress,
            "postCount": pcount,
            "commentCount": ccount,
            "givenScore": givenScore,
            "postScore": postScore,
            "userScore": userScore,
            "commentScore": commentScore,
            "invitecodeinfo": invitecodeinfo,
            "badges": badges,
            "owns": owns,
            "mods": mods,
            "habits": habit,
            "target_user_is_admin": user_is_admin,
            "msgform": CreateUserMessageForm(),
            "ignform": ignform,
        }
    )


@bp.route("/u/<user>/posts", defaults={"page": 1})
@bp.route("/u/<user>/posts/<int:page>")
@limit_pagination
def view_user_posts(user, page):
    """WIP: View user's recent posts"""
    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        abort(404)
    if user.status == 10 and not current_user.is_admin():
        return view_deleted_user(user)

    include_deleted_posts = user.uid == current_user.uid or current_user.is_admin()
    # filter_private_posts = user.uid == current_user.uid or current_user.is_admin()

    if current_user.is_a_mod:
        modded_subs = [s.sid for s in misc.getModSubs(current_user.uid, 2)]
        if modded_subs:
            if not include_deleted_posts:
                include_deleted_posts = modded_subs
            #  TODO Make this work
            # if not filter_private_posts:
            #     filter_private_posts = modded_subs

    posts = misc.getPostList(
        misc.postListQueryBase(
            include_deleted_posts=include_deleted_posts,
            filter_private_posts=True,
            noAllFilter=not current_user.is_admin(),
            noUserFilter=True,
            filter_shadowbanned=True,
        ).where(User.uid == user.uid),
        "new",
        page,
    )
    return engine.get_template("user/posts.html").render(
        {
            "page": page,
            "sort_type": "user.view_user_posts",
            "posts": posts,
            "user": user,
        }
    )


@bp.route("/u/<user>/savedposts", defaults={"page": 1})
@bp.route("/u/<user>/savedposts/<int:page>")
@login_required
def view_user_savedposts(user, page):
    """WIP: View user's saved posts"""
    if current_user.name.lower() == user.lower():
        posts = misc.getPostList(
            misc.postListQueryBase(noAllFilter=True, noUserFilter=True)
            .join(UserSaved, on=(UserSaved.pid == SubPost.pid))
            .where(UserSaved.uid == current_user.uid),
            "new",
            page,
        )
        return engine.get_template("user/savedposts.html").render(
            {
                "page": page,
                "sort_type": "user.view_user_savedposts",
                "posts": posts,
                "user": current_user,
            }
        )
    else:
        abort(403)


@bp.route("/u/<user>/comments", defaults={"page": 1})
@bp.route("/u/<user>/comments/<int:page>")
@limit_pagination
def view_user_comments(user, page):
    """WIP: View user's recent comments"""
    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        abort(404)
    if user.status == 10 and not current_user.is_admin():
        return view_deleted_user(user)

    include_deleted_comments = user.uid == current_user.uid or current_user.is_admin()
    if not include_deleted_comments and current_user.is_a_mod:
        modded_subs = [s.sid for s in misc.getModSubs(current_user.uid, 2)]
        if modded_subs:
            include_deleted_comments = modded_subs

    comments = misc.getUserComments(
        user.uid,
        page,
        include_deleted_comments=include_deleted_comments,
        filter_private_comments=True,
        filter_shadowbanned=True,
    )
    postmeta = misc.get_postmeta_dicts((c["pid"] for c in comments))
    return engine.get_template("user/comments.html").render(
        {
            "user": user,
            "page": page,
            "comments": comments,
            "postmeta": postmeta,
            "highlight": "",
        }
    )


@bp.route("/u/<user>/savedcomments", defaults={"page": 1})
@bp.route("/u/<user>/savedcomments/<int:page>")
@login_required
def view_user_savedcomments(user, page):
    """WIP: View user's saved comments"""
    include_deleted_comments = User.uid == current_user.uid or current_user.is_admin()
    if not include_deleted_comments and current_user.is_a_mod:
        modded_subs = [s.sid for s in misc.getModSubs(current_user.uid, 2)]
        if modded_subs:
            include_deleted_comments = modded_subs
    if current_user.name.lower() == user.lower():
        comments = misc.getUserSavedComments(
            current_user.uid,
            page,
            include_deleted_comments=include_deleted_comments,
        )
        postmeta = misc.get_postmeta_dicts((c["pid"] for c in comments))
        return engine.get_template("user/savedcomments.html").render(
            {
                "user": current_user,
                "page": page,
                "comments": comments,
                "postmeta": postmeta,
                "highlight": "",
            }
        )
    else:
        abort(403)


@bp.route("/uploads", defaults={"page": 1})
@bp.route("/uploads/<int:page>")
@login_required
def view_user_uploads(page):
    """View user uploads"""
    uploads = (
        UserUploads.select()
        .join(SubPost)
        .where((UserUploads.uid == current_user.uid) & (SubPost.deleted != 1))
        .paginate(page, 30)
    )
    return engine.get_template("user/uploads.html").render(
        {
            "user": current_user,
            "page": page,
            "uploads": uploads,
        }
    )


@bp.route("/settings/invite")
@login_required
def invite_codes():
    if not config.site.require_invite_code:
        return redirect("/settings")
    codes = (
        InviteCode.select()
        .where(InviteCode.user == current_user.uid)
        .order_by(InviteCode.created.desc())
    )
    maxcodes = int(misc.getMaxCodes(current_user.uid))
    created = codes.count()
    avail = 0
    if (maxcodes - created) >= 0:
        avail = maxcodes - created
    return engine.get_template("user/settings/invitecode.html").render(
        {
            "codes": codes,
            "created": created,
            "max": maxcodes,
            "avail": avail,
            "user": User.get(User.uid == current_user.uid),
            "csrf_form": CsrfTokenOnlyForm(),
        }
    )


@bp.route("/settings/subs")
@login_required
def edit_subs():
    return engine.get_template("user/topbar.html").render({})


@bp.route("/settings")
@login_required
def edit_user():
    styles = "nostyles" in current_user.prefs
    if "nsfw" not in current_user.prefs:
        nsfw_option = "hide"
    elif "nsfw_blur" in current_user.prefs:
        nsfw_option = "blur"
    else:
        nsfw_option = "show"
    exp = "labrat" in current_user.prefs
    noscroll = "noscroll" in current_user.prefs
    nochat = "nochat" in current_user.prefs
    email_notify = "email_notify" in current_user.prefs

    form = EditUserForm(
        show_nsfw=nsfw_option,
        disable_sub_style=styles,
        experimental=exp,
        noscroll=noscroll,
        nochat=nochat,
        language=current_user.language,
        email_notify=email_notify,
    )
    languages = config.app.languages
    form.language.choices = []
    for i in languages:
        form.language.choices.append(
            (i, Locale(*i.split("_")).display_name.capitalize())
        )
    return engine.get_template("user/settings/preferences.html").render(
        {"edituserform": form, "user": User.get(User.uid == current_user.uid)}
    )


@bp.route("/settings/account")
@login_required
@ratelimit(AUTH_LIMIT)
def edit_account():
    return engine.get_template("user/settings/account.html").render(
        {"form": EditAccountForm(), "user": User.get(User.uid == current_user.uid)}
    )


@bp.route("/settings/account/confirm-email/<token>")
@gevent_required  # Contacts Keycloak if configured.
@ratelimit(AUTH_LIMIT)
def confirm_email_change(token):
    info = info_from_email_confirmation_token(token)
    try:
        user = User.get(User.uid == info["uid"])
    except (TypeError, User.DoesNotExist):
        flash(_("The link you used is invalid or has expired"), "error")
        return redirect(url_for("user.edit_account"))

    if user.status == UserStatus.OK:
        try:
            auth_provider.confirm_pending_email(user, info["email"])
            flash(_("Your password recovery email address is now confirmed!"))
            return redirect(url_for("user.edit_account"))
        except AuthError:
            flash(
                _("Unable to confirm your new email address. Please try again later."),
                "error",
            )
    return redirect(url_for("home.index"))


@bp.route("/settings/delete")
@login_required
def delete_account():
    return engine.get_template("user/settings/delete.html").render(
        {"form": DeleteAccountForm(), "user": User.get(User.uid == current_user.uid)}
    )


@bp.route("/recover", methods=["GET", "POST"])
@gevent_required  # Starts an async task (email).
@ratelimit(SIGNUP_LIMIT)
def password_recovery():
    """Endpoint for the password recovery form"""
    if current_user.is_authenticated:
        return redirect(url_for("home.index"))
    form = PasswordRecoveryForm()
    captcha = misc.create_captcha()
    if not form.validate():
        return engine.get_template("user/password_recovery.html").render(
            {"lpform": form, "error": misc.get_errors(form, True), "captcha": captcha}
        )
    if not misc.validate_captcha(form.ctok.data, form.captcha.data):
        return engine.get_template("user/password_recovery.html").render(
            {"lpform": form, "error": _("Invalid captcha."), "captcha": captcha}
        )
    try:
        email = normalize_email(form.email.data)
        user = User.get(fn.Lower(User.email) == email.lower())
        if user.status == UserStatus.OK:
            send_password_recovery_email(user)
    except User.DoesNotExist:
        # Yield no information.
        pass
    return redirect(url_for("user.recovery_email_sent"))


@bp.route("/recovery/email-sent")
def recovery_email_sent():
    return engine.get_template("user/check-your-email.html").render(
        {"reason": "recovery", "email": "", "show_resend_link": False}
    )


@bp.route("/reset/<token>")
@ratelimit(SIGNUP_LIMIT)
def password_reset(token):
    """The page that actually resets the password"""
    user = None
    try:
        user = User.get(User.uid == uid_from_recovery_token(token))
    except User.DoesNotExist:
        pass
    if not user or user.status != UserStatus.OK:
        flash(_("Password reset link was invalid or expired"), "error")
        return redirect(url_for("user.password_recovery"))

    if current_user.is_authenticated:
        return redirect(url_for("home.index"))

    form = PasswordResetForm(key=token, user=user.uid)
    return engine.get_template("user/password_reset.html").render({"lpform": form})


@bp.route("/settings/blocks", defaults={"page": 1})
@bp.route("/settings/blocks/<int:page>")
@login_required
def view_ignores(page):
    """View user's blocked users."""
    if current_user.can_admin:
        abort(404)
    menu = request.args.get("menu", "user")

    def add_form(ig):
        messages = "hide" if ig["hide_messages"] else "show"
        if ig["method"] is None:
            content = "show"
        elif ig["method"] == UserContentBlockMethod.HIDE:
            content = "hide"
        else:
            content = "blur"
        ig["form"] = EditIgnoreForm(view_messages=messages, view_content=content)
        return ig

    query = (
        User.select(
            User.name,
            User.uid.alias("target"),
            UserMessageBlock.id.is_null(False).alias("hide_messages"),
            UserContentBlock.method,
        )
        .join(
            UserMessageBlock,
            JOIN.LEFT_OUTER,
            on=(
                (UserMessageBlock.uid == current_user.uid)
                & (UserMessageBlock.target == User.uid)
            ),
        )
        .join(
            UserContentBlock,
            JOIN.LEFT_OUTER,
            on=(
                (UserContentBlock.uid == current_user.uid)
                & (UserContentBlock.target == User.uid)
            ),
        )
        .where(
            (User.status == UserStatus.OK)
            & (UserContentBlock.id.is_null(False) | UserMessageBlock.id.is_null(False))
        )
        .order_by(fn.lower(User.name))
        .paginate(page, 25)
        .dicts()
    )
    igns = [add_form(ig) for ig in query]

    return engine.get_template("user/ignores.html").render(
        {
            "igns": igns,
            "user": User.get(User.uid == current_user.uid),
            "page": page,
            "menu": menu,
        }
    )
