""" /do/ views (AJAX stuff) """

import json
import re
import time
import datetime
import uuid
import random
from collections import defaultdict
from flask import Blueprint, redirect, url_for, session, abort, jsonify, current_app
from flask import request, flash, Markup
from flask_login import login_user, login_required, logout_user, current_user
from flask_babel import _, ngettext, force_locale
from itsdangerous import URLSafeTimedSerializer
from itsdangerous.exc import SignatureExpired, BadSignature
from bs4 import BeautifulSoup
from ..config import config
from .. import forms, misc, storage, tasks
from ..socketio import socketio
from ..auth import (
    auth_provider,
    email_validation_is_required,
    AuthError,
    normalize_email,
)
from ..forms import (
    LogOutForm,
    CreateSubFlair,
    CsrfTokenOnlyForm,
    CreateSubRule,
    NoReplyCommentForm,
    LockCommentForm,
    NoReplyPostForm,
)
from ..forms import EditSubForm, EditUserForm, EditIgnoreForm, EditSubCSSForm
from ..forms import EditModForm, BanUserSubForm, DeleteAccountForm, EditAccountForm
from ..forms import EditSubTextPostForm, AssignUserBadgeForm
from ..forms import PostComment, CreateUserMessageForm, CreateUserMessageReplyForm
from ..forms import DeletePost, UndeletePost
from ..forms import (
    SearchForm,
    EditMod2Form,
    EditMemberForm,
    SetSubOfTheDayForm,
    AssignSubUserFlair,
)
from ..forms import DeleteSubFlair, DeleteSubRule, CreateReportNote
from ..forms import UseInviteCodeForm, SecurityQuestionForm, DistinguishForm
from ..forms import BanDomainForm, SetOwnUserFlairForm, ChangeConfigSettingForm
from ..forms import AnnouncePostForm, LiteralBooleanForm, ViewCommentsForm
from ..badges import badges
from ..misc import (
    cache,
    send_email,
    allowedNames,
    get_errors,
    engine,
    ensure_locale_loaded,
    ratelimit,
    POSTING_LIMIT,
    AUTH_LIMIT,
    is_domain_banned,
    gevent_required,
)
from ..models import (
    SubPost,
    SubPostComment,
    Sub,
    Message,
    User,
    UserMessageBlock,
    UserContentBlock,
    UserContentBlockMethod,
    UserMetadata,
    SubMetadata,
    UserSaved,
    SubUserFlair,
)
from ..models import (
    SubMod,
    SubBan,
    SubPostCommentHistory,
    InviteCode,
    SubPostContentHistory,
)
from ..models import (
    SubStylesheet,
    SubSubscriber,
    SubUploads,
    UserUploads,
    SiteMetadata,
    SubPostMetadata,
    SubPostReport,
)
from ..models import (
    SubPostVote,
    SubPostCommentVote,
    SubPostCommentView,
    SubFlair,
    SubPostPollOption,
    SubPostPollVote,
    SubPostTitleHistory,
    SubRule,
    SubPostCommentReport,
    SubUserFlairChoice,
)
from ..models import (
    rconn,
    UserStatus,
    MessageType,
    MessageMailbox,
    UserUnreadMessage,
    UserMessageMailbox,
)
from ..tasks import create_thumbnail
from ..notifications import notifications
from peewee import fn, JOIN

do = Blueprint("do", __name__)

# allowedCSS = re.compile("\'(^[0-9]{1,5}[a-zA-Z ]+$)|none\'")


@do.errorhandler(429)
def ratelimit_handler(*__):
    ensure_locale_loaded()
    return (
        jsonify(
            status="error", error=[_("Whoa, calm down and wait a bit, then try again.")]
        ),
        200,
    )


@do.route("/do/logout", methods=["POST"])
@login_required
def logout():
    """Logout endpoint"""
    form = LogOutForm()
    if form.validate():
        if config.auth.provider == "KEYCLOAK" and config.auth.keycloak.use_oidc:
            auth_provider.logout()

        session.pop("apriv", None)
        logout_user()

    return redirect(url_for("home.index"))


@do.route("/do/search", defaults={"stype": "home.search"}, methods=["POST"])
@do.route("/do/search/<stype>", methods=["POST"])
def search(stype):
    """Search endpoint"""
    if stype not in (
        "home.search",
        "home.subs",
        "admin.users",
        "admin.post_voting",
        "admin.subs",
        "admin.post",
    ):
        abort(404)
    if not stype.endswith("search"):
        stype += "_search"

    if not current_user.is_admin() and stype.startswith("admin"):
        abort(403)
    form = SearchForm()
    term = re.sub(r'[^A-Za-zÁ-ž0-9.,\-_\'" ]+', "", form.term.data)
    # Store search context in session
    session["search_context"] = {
        "sub": request.form.get("sub"),
        "sub_name": request.form.get("sub_name"),
        "subonlysearch": request.form.get("subonlysearch"),
    }
    return redirect(url_for(stype, term=term))


@do.route("/do/edit_account", methods=["POST"])
@gevent_required  # Contacts Keycloak if configured, does async task (email).
@login_required
def edit_account():
    form = EditAccountForm()
    if email_validation_is_required():
        del form.email_optional
    else:
        del form.email_required

    if form.validate():
        user = User.get(User.uid == current_user.uid)
        if email_validation_is_required():
            email = form.email_required.data
        else:
            email = form.email_optional.data

        if email:
            email = normalize_email(email)
            if is_domain_banned(email, domain_type="email"):
                return json.dumps(
                    {
                        "status": "error",
                        "error": [
                            _("We do not accept emails from your email provider.")
                        ],
                    }
                )
            user_from_email = auth_provider.get_user_by_email(email)
            if user_from_email is not None and user.uid != user_from_email.uid:
                return json.dumps(
                    {
                        "status": "error",
                        "error": [_("E-mail address is already in use.")],
                    }
                )

        if not auth_provider.validate_password(user, form.oldpassword.data):
            return json.dumps({"status": "error", "error": [_("Incorrect password")]})

        messages = None
        if form.password.data or email != user.email:
            if form.password.data:
                try:
                    auth_provider.change_password(
                        user, form.oldpassword.data, form.password.data
                    )
                except AuthError:
                    return json.dumps(
                        {
                            "status": "error",
                            "error": [
                                _("Password change failed. Please try again later")
                            ],
                        }
                    )
            if email != user.email:
                if not email_validation_is_required():
                    user.email = email
                    user.save()
                else:
                    auth_provider.set_pending_email(user, email)
                    send_email_confirmation_link_email(user, email)
                    messages = [
                        _(
                            "To confirm, click the link in the email we sent to you. "
                            "You may want to check your spam folder, just in case ;)"
                        )
                    ]
        return json.dumps({"status": "ok", "message": messages})
    return json.dumps({"status": "error", "error": get_errors(form)})


def send_email_confirmation_link_email(user, new_email):
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="email-change")
    token = s.dumps({"uid": user.uid, "email": new_email})
    send_email(
        new_email,
        _(
            "Confirm your email address for your %(site)s account",
            site=config.site.name,
        ),
        text_content=engine.get_template("user/email/confirm-email-change.txt").render(
            dict(user=user, token=token)
        ),
        html_content=engine.get_template("user/email/confirm-email-change.html").render(
            dict(user=user, token=token)
        ),
    )


def info_from_email_confirmation_token(token):
    try:
        s = URLSafeTimedSerializer(
            current_app.config["SECRET_KEY"], salt="email-change"
        )
        return s.loads(token, max_age=24 * 60 * 60)  # TODO in config?
    except (SignatureExpired, BadSignature):
        return None


@do.route("/do/delete_account", methods=["POST"])
@login_required
def delete_user():
    form = DeleteAccountForm()
    if form.validate():
        usr = User.get(User.uid == current_user.uid)
        if not auth_provider.validate_password(usr, form.password.data):
            return jsonify(status="error", error=[_("Wrong password")])

        if form.consent.data != _("YES"):
            return jsonify(status="error", error=[_('Type "YES" in the box')])

        auth_provider.change_user_status(usr, 10)
        logout_user()

        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/edit_user", methods=["POST"])
@login_required
def edit_user():
    """Edit user endpoint"""
    form = EditUserForm()
    if form.validate():
        old_highlight_setting = "highlight_unseen_comments" in current_user.prefs
        usr = User.get(User.uid == current_user.uid)
        usr.language = form.language.data
        session["language"] = form.language.data
        usr.save()
        current_user.update_prefs("labrat", form.experimental.data)
        current_user.update_prefs("nostyles", form.disable_sub_style.data)
        current_user.update_prefs("nsfw", form.show_nsfw.data != "hide")
        current_user.update_prefs("nsfw_blur", form.show_nsfw.data == "blur")
        current_user.update_prefs("noscroll", form.noscroll.data)
        current_user.update_prefs("nochat", form.nochat.data)
        current_user.update_prefs("email_notify", form.email_notify.data)
        current_user.update_prefs("comment_sort", form.comment_sort.data, boolean=False)

        new_highlight_setting = form.highlight_unseen_comments.data
        current_user.update_prefs("highlight_unseen_comments", new_highlight_setting)

        # If the user is enabling highlight_unseen_comments for the first time
        if new_highlight_setting and not old_highlight_setting:
            misc.mark_all_comments_viewed(current_user.uid)

        return json.dumps({"status": "ok"})
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/delete_post", methods=["POST"])
@login_required
def delete_post():
    """Post deletion endpoint"""
    form = DeletePost()

    if form.validate():
        try:
            post = SubPost.get(SubPost.pid == form.post.data)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=[_("Post does not exist")])

        sub = Sub.get(Sub.sid == post.sid)

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if post.deleted != 0:
            return jsonify(status="error", error=[_("Post was already deleted")])

        if (
            not current_user.is_mod(post.sid)
            and not current_user.is_admin()
            and not post.uid_id == current_user.uid
        ):
            return jsonify(status="error", error=[_("Not authorized")])

        if post.uid_id == current_user.uid:
            deletion = 1
        else:
            if not form.reason.data:
                return jsonify(
                    status="error", error=[_("Cannot delete without reason")]
                )
            as_admin = not current_user.is_mod(post.sid)
            postlink, sublink = misc.post_and_sub_markdown_links(post)
            target_language = User.get_by_id(pk=post.uid.get_id()).language
            if target_language == "sk":
                locale_language = "sk_SK"
            elif target_language == "cs":
                locale_language = "cs_CZ"
            elif target_language == "en":
                locale_language = "en_US"
            elif target_language == "es":
                locale_language = "es_ES"
            elif target_language == "ru":
                locale_language = "ru_RU"
            else:
                locale_language = (
                    "en_US"  # Default language if no target language found
                )

            with force_locale(locale_language):
                if as_admin:
                    deletion = 3
                    content = _(
                        "The site administrators deleted your post %(postlink)s from %(sublink)s. "
                        "Reason: %(reason)s",
                        sublink=sublink,
                        postlink=postlink,
                        reason=form.reason.data,
                    )
                else:
                    deletion = 2
                    content = _(
                        "The moderators of %(sublink)s deleted your post %(postlink)s. "
                        "Reason: %(reason)s",
                        sublink=sublink,
                        postlink=postlink,
                        reason=form.reason.data,
                    )

                misc.create_notification_message(
                    mfrom=current_user.uid,
                    as_admin=as_admin,
                    sub=post.sid.get_id(),
                    to=post.uid.get_id(),
                    subject=_("Moderation action: post deleted"),
                    content=content,
                )
            misc.create_sublog(
                misc.LOG_TYPE_SUB_DELETE_POST,
                current_user.uid,
                post.sid,
                comment=form.reason.data,
                link=url_for("site.view_post_inbox", pid=post.pid),
                admin=True
                if (not current_user.is_mod(post.sid) and current_user.is_admin())
                else False,
                target=post.uid,
            )

        related_reports = SubPostReport.select().where(SubPostReport.pid == post.pid)
        for report in related_reports:
            misc.create_reportlog(
                misc.LOG_TYPE_REPORT_POST_DELETED,
                current_user.uid,
                report.id,
                log_type="post",
                desc=form.reason.data,
            )

        # time limited to prevent socket spam
        if (
            (
                datetime.datetime.utcnow() - post.posted.replace(tzinfo=None)
            ).total_seconds()
        ) < 86400:
            socketio.emit(
                "deletion",
                {"pid": post.pid},
                namespace="/snt",
            )

        # check if the post is an announcement. Unannounce if it is.
        try:
            ann = (
                SiteMetadata.select()
                .where(SiteMetadata.key == "announcement")
                .where(SiteMetadata.value == post.pid)
                .get()
            )
            ann.delete_instance()
            cache.delete_memoized(misc.getAnnouncementPid)
            cache.delete_memoized(misc.getAnnouncement)
        except SiteMetadata.DoesNotExist:
            pass

        # Check if the post is sticky.  Unstick if so.
        try:
            is_sticky = SubMetadata.get(
                (SubMetadata.sid == post.sid_id)
                & (SubMetadata.key == "sticky")
                & (SubMetadata.value == post.pid)
            )
            is_sticky.delete_instance()
            misc.create_sublog(
                misc.LOG_TYPE_SUB_STICKY_DEL,
                current_user.uid,
                post.sid,
                link=url_for("sub.view_post", sub=post.sid.name, pid=post.pid),
            )
        except SubMetadata.DoesNotExist:
            pass

        Sub.update(posts=Sub.posts - 1).where(Sub.sid == post.sid).execute()

        post.deleted = deletion
        post.save()

        return jsonify(status="ok")
    return jsonify(status="ok", error=get_errors(form))


@do.route("/do/undelete_post", methods=["POST"])
@login_required
def undelete_post():
    """Post un-deletion endpoint"""
    form = UndeletePost()

    if form.validate():
        try:
            post = SubPost.get(SubPost.pid == form.post.data)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=[_("Post does not exist")])

        sub = Sub.get(Sub.sid == post.sid)

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if post.deleted == 0:
            return jsonify(status="error", error=[_("Post is not deleted")])

        if post.deleted == 1:
            return jsonify(
                status="error", error=[_("Can not un-delete a self-deleted post")]
            )

        if not (
            current_user.is_admin()
            or (current_user.is_mod(post.sid) and post.deleted == 2)
        ):
            return jsonify(status="error", error=[_("Not authorized")])

        if not form.reason.data:
            return jsonify(status="error", error=[_("Cannot un-delete without reason")])
        deletion = 0
        as_admin = not current_user.is_mod(post.sid)
        postlink, sublink = misc.post_and_sub_markdown_links(post)
        target_language = User.get_by_id(pk=post.uid.get_id()).language
        if target_language == "sk":
            locale_language = "sk_SK"
        elif target_language == "cs":
            locale_language = "cs_CZ"
        elif target_language == "en":
            locale_language = "en_US"
        elif target_language == "es":
            locale_language = "es_ES"
        elif target_language == "ru":
            locale_language = "ru_RU"
        else:
            locale_language = "en_US"  # Default language if no target language found

        with force_locale(locale_language):
            if as_admin:
                content = _(
                    "The site administrators restored your post %(postlink)s to %(sublink)s. "
                    "Reason: %(reason)s",
                    sublink=sublink,
                    postlink=postlink,
                    reason=form.reason.data,
                )
            else:
                content = _(
                    "The moderators of %(sublink)s restored your post %(postlink)s. "
                    "Reason: %(reason)s",
                    sublink=sublink,
                    postlink=postlink,
                    reason=form.reason.data,
                )

            misc.create_notification_message(
                mfrom=current_user.uid,
                as_admin=as_admin,
                sub=post.sid.get_id(),
                to=post.uid.get_id(),
                subject=_("Moderation action: post restored"),
                content=content,
            )

        misc.create_sublog(
            misc.LOG_TYPE_SUB_UNDELETE_POST,
            current_user.uid,
            post.sid,
            comment=form.reason.data,
            link=url_for("site.view_post_inbox", pid=post.pid),
            admin=True
            if (not current_user.is_mod(post.sid) and current_user.is_admin())
            else False,
            target=post.uid,
        )

        related_reports = SubPostReport.select().where(SubPostReport.pid == post.pid)
        for report in related_reports:
            misc.create_reportlog(
                misc.LOG_TYPE_REPORT_POST_UNDELETED,
                current_user.uid,
                report.id,
                log_type="post",
                desc=form.reason.data,
            )

        Sub.update(posts=Sub.posts + 1).where(Sub.sid == post.sid).execute()

        post.deleted = deletion
        post.save()

        return jsonify(status="ok")
    return jsonify(status="ok", error=get_errors(form))


@do.route("/do/edit_sub_css/<sub>", methods=["POST"])
@login_required
def edit_sub_css(sub):
    """Edit sub endpoint"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        return jsonify(status="error", error=[_("Not authorized")])

    form = EditSubCSSForm()
    if form.validate():
        styles = SubStylesheet.get(SubStylesheet.sid == sub.sid)
        dcss = misc.validate_css(form.css.data, sub.sid)
        if dcss[0] != 0:
            return jsonify(
                status="error",
                error=["Error on {0}:{1}: {2}".format(dcss[1], dcss[2], dcss[0])],
            )

        styles.content = dcss[1]
        styles.source = form.css.data
        styles.save()
        misc.create_sublog(misc.LOG_TYPE_SUB_CSS_CHANGE, current_user.uid, sub.sid)

        return json.dumps(
            {"status": "ok", "addr": url_for("sub.view_sub", sub=sub.name)}
        )
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/edit_sub/<sub>", methods=["POST"])
@login_required
def edit_sub(sub):
    """Edit sub endpoint"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    if current_user.is_mod(sub.sid, 1) or current_user.is_admin():
        form = EditSubForm()
        if form.validate():
            if not (
                form.allow_text_posts.data
                or form.allow_link_posts.data
                or form.allow_upload_posts.data
                or form.allow_polls.data
            ):
                return json.dumps(
                    {
                        "status": "error",
                        "error": [_("At least one kind of post must be permitted")],
                    }
                )
            sub.title = form.title.data
            sub.sidebar = form.sidebar.data
            sub.nsfw = form.nsfw.data
            sub.commentscore_delay = form.commentscore_delay.data
            sub.save()

            sub.update_metadata("restricted", form.restricted.data)
            sub.update_metadata("ucf", form.usercanflair.data)
            sub.update_metadata("umf", form.usermustflair.data)
            sub.update_metadata("user_can_flair_self", form.user_can_flair_self.data)
            sub.update_metadata("freeform_user_flairs", form.freeform_user_flairs.data)
            sub.update_metadata("allow_text_posts", form.allow_text_posts.data)
            sub.update_metadata("allow_link_posts", form.allow_link_posts.data)
            sub.update_metadata("allow_upload_posts", form.allow_upload_posts.data)
            sub.update_metadata("allow_polls", form.allow_polls.data)
            sub.update_metadata("sublog_private", form.sublogprivate.data)
            sub.update_metadata(
                "sub_banned_users_private", form.subbannedusersprivate.data
            )
            sub.update_metadata(
                "disable_auto_expandos", form.disable_auto_expandos.data
            )
            sub.update_metadata("enable_flairpicker", form.enable_flairpicker.data)

            if form.subsort.data != "None":
                sub.update_metadata("sort", form.subsort.data, boolean=False)

            misc.create_sublog(misc.LOG_TYPE_SUB_SETTINGS, current_user.uid, sub.sid)

            if not current_user.is_mod(sub.sid, 1) and current_user.is_admin():
                misc.create_sitelog(
                    misc.LOG_TYPE_SUB_SETTINGS,
                    current_user.uid,
                    comment="/s/" + sub.name,
                    link=url_for("sub.view_sub", sub=sub.name),
                )

            return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))
        return jsonify(status="error", error=get_errors(form))
    else:
        abort(403)


@do.route("/do/mod_set_flair/<sub>", methods=["POST"])
@login_required
def mod_assign_flair(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    if not current_user.is_mod(sub.sid) and not current_user.is_admin():
        return jsonify(status="error", error=[_("Not authorized")])

    form = AssignSubUserFlair()
    flairs = SubUserFlairChoice.select().where(SubUserFlairChoice.sub == sub.sid)
    for flair in flairs:
        form.flair_id.choices.append((flair.id, flair.flair))

    if not form.validate():
        return jsonify(status="error", error=get_errors(form))

    if form.flair_id.data == "-1" and not form.text.data:
        return jsonify(status="error", error=[_("No flair text")])

    try:
        user = User.get((User.name == form.user.data) & (User.status != 10))
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    try:
        user_flair = SubUserFlair.get(
            (SubUserFlair.user == user.uid) & (SubUserFlair.sub == sub.sid)
        )
    except SubUserFlair.DoesNotExist:
        if form.flair_id.data == "-2":
            return jsonify(status="ok")
        user_flair = SubUserFlair(sub=sub, user=user.uid)

    if form.flair_id.data == "-2":
        user_flair.delete_instance()
    elif form.flair_id.data == "-1":
        user_flair.flair = form.text.data
        user_flair.flair_choice = None
    else:
        try:
            flair_choice = SubUserFlairChoice.get(
                (SubUserFlairChoice.sub == sub.sid)
                & (SubUserFlairChoice.id == form.flair_id.data)
            )
        except SubUserFlairChoice.DoesNotExist:
            return jsonify(status="error", error=[_("Flair does not exist")])
        user_flair.flair = flair_choice.flair
        user_flair.flair_choice = flair_choice.id

    user_flair.save()
    cache.delete_memoized(misc.get_user_flair, sub.sid, current_user.uid)
    return jsonify(status="ok")


@do.route("/do/set_user_flair_text/<sub>", methods=["POST"])
@login_required
def set_user_flair_text(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    form = SetOwnUserFlairForm()

    if not form.validate():
        return jsonify(status="error", error=get_errors(form))

    flair = form.flair.data.strip()
    sub_info = misc.getSubData(sub.sid)

    if not sub_info.get("freeform_user_flairs", 0) == "1":
        return jsonify(
            status="error", error=[_("Free-form user flairs are disabled for this sub")]
        )

    try:
        user_flair = SubUserFlair.get(
            (SubUserFlair.user == current_user.uid) & (SubUserFlair.sub == sub.sid)
        )
    except SubUserFlair.DoesNotExist:
        user_flair = SubUserFlair(sub=sub, user=current_user.uid)

    user_flair.flair = flair
    user_flair.flair_choice = None
    user_flair.save()
    cache.delete_memoized(misc.get_user_flair, sub.sid, current_user.uid)
    return jsonify(status="ok")


@do.route("/do/user_flair/<sub>/<flair_id>", methods=["POST"])
@login_required
def set_user_flair_choice(sub, flair_id):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    sub_info = misc.getSubData(sub.sid)

    if not sub_info.get("user_can_flair_self", 0) == "1" and not current_user.is_mod(
        sub.sid
    ):
        return jsonify(status="error", error=[_("Not authorized")])

    try:
        flair_choice = SubUserFlairChoice.get(
            (SubUserFlairChoice.sub == sub.sid) & (SubUserFlairChoice.id == flair_id)
        )
    except SubUserFlairChoice.DoesNotExist:
        return jsonify(status="error", error=[_("Flair does not exist")])

    try:
        user_flair = SubUserFlair.get(
            (SubUserFlair.user == current_user.uid) & (SubUserFlair.sub == sub.sid)
        )
    except SubUserFlair.DoesNotExist:
        user_flair = SubUserFlair(sub=sub, user=current_user.uid)

    user_flair.flair = flair_choice.flair
    user_flair.flair_choice = flair_choice.id
    user_flair.save()
    cache.delete_memoized(misc.get_user_flair, sub.sid, current_user.uid)
    return jsonify(status="ok")


@do.route("/do/delete_user_flair/<sub>", methods=["POST"])
@login_required
def delete_user_own_flair(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    sub_info = misc.getSubData(sub.sid)

    if (
        not sub_info.get("freeform_user_flairs", 0) == "1"
        and not sub_info.get("user_can_flair_self", 0) == "1"
    ):
        return jsonify(status="error", error=[_("Not authorized")])

    try:
        user_flair = SubUserFlair.get(
            (SubUserFlair.user == current_user.uid) & (SubUserFlair.sub == sub.sid)
        )
        user_flair.delete_instance()
    except SubUserFlair.DoesNotExist:
        pass
    cache.delete_memoized(misc.get_user_flair, sub.sid, current_user.uid)
    return jsonify(status="ok")


@do.route("/do/flair/<sub>/<pid>/<fl>", methods=["POST"])
@login_required
def assign_post_flair(sub, pid, fl):
    """Assign a post's flair"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    try:
        post = SubPost.get(SubPost.pid == pid)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=[_("Post does not exist")])

    form = CsrfTokenOnlyForm()
    if form.validate():
        if current_user.is_mod(sub.sid) or (
            post.uid_id == current_user.uid and sub.get_metadata("ucf") == "1"
        ):
            try:
                flair = SubFlair.get((SubFlair.xid == fl) & (SubFlair.sid == sub.sid))
            except SubFlair.DoesNotExist:
                return jsonify(status="error", error=_("Flair does not exist"))

            post.flair = flair.text
            post.save()

            return jsonify(status="ok")
        else:
            return jsonify(status="error", error=_("Not authorized"))
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/remove_post_flair/<sub>/<pid>", methods=["POST"])
def remove_post_flair(sub, pid):
    """Deletes a post's flair"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    try:
        post = SubPost.get(SubPost.pid == pid)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=[_("Post does not exist")])

    if current_user.is_mod(sub.sid) or (
        post.uid_id == current_user.uid
        and sub.get_metadata("ucf") == "1"
        and sub.get_metadata("umf") != "1"
    ):
        if not post.flair:
            return jsonify(status="error", error=_("Post has no flair"))
        else:
            post.flair = None
            post.save()

        return jsonify(status="ok")
    else:
        abort(403)


@do.route("/do/edit_mod", methods=["POST"])
@login_required
def edit_mod():
    """Admin endpoint used for sub transfers."""
    if not current_user.is_admin():
        abort(403)
    form = EditModForm()

    try:
        sub = Sub.get(fn.Lower(Sub.name) == form.sub.data.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    try:
        user = User.get(fn.Lower(User.name) == form.user.data.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    if form.validate():
        # Get the previous owner
        try:
            sm = SubMod.get((SubMod.sid == sub.sid) & (SubMod.power_level == 0))
            # Reduce em to regular mod.
            sm.power_level = 1
            sm.save()
        except SubMod.DoesNotExist:
            pass

        try:
            sm = SubMod.get((SubMod.sid == sub.sid) & (SubMod.uid == user.uid))
            sm.power_level = 0
            sm.invite = False
            sm.save()
        except SubMod.DoesNotExist:
            SubMod.create(sid=sub.sid, uid=user.uid, power_level=0)

        misc.create_sublog(
            misc.LOG_TYPE_SUB_TRANSFER,
            current_user.uid,
            sub.sid,
            comment=user.name,
            admin=True,
        )

        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/assign_userbadge", methods=["POST"])
@login_required
def assign_userbadge():
    """Admin endpoint used for assigning a user badge."""
    if not current_user.is_admin():
        abort(403)
    badgeTuple = [(badge.bid, badge.name) for badge in badges]
    form = AssignUserBadgeForm()
    form.badge.choices = badgeTuple
    bid = int(form.badge.data)

    if bid not in [x[0] for x in badgeTuple]:
        return jsonify(status="error", error=[_("Badge does not exist")])

    try:
        user = User.get(fn.Lower(User.name) == form.user.data.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    if form.validate():
        badges.assign_userbadge(user.uid, bid)
        # TODO log it, create new log type and save to sitelog ??
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/remove_userbadge", methods=["POST"])
@login_required
def remove_userbadge():
    """Admin endpoint used for removing a user badge."""
    if not current_user.is_admin():
        abort(403)
    badgeList = [(badge.bid, badge.name) for badge in badges]
    form = AssignUserBadgeForm()
    form.badge.choices = badgeList
    bid = int(form.badge.data)

    if bid not in [x[0] for x in badgeList]:
        return jsonify(status="error", error=[_("Badge does not exist")])

    try:
        user = User.get(fn.Lower(User.name) == form.user.data.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    if form.validate():
        badges.unassign_userbadge(user.uid, bid)
        # TODO log it, create new log type and save to sitelog ??
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/subscribe/<sid>", methods=["POST"])
@login_required
def subscribe_to_sub(sid):
    """Subscribe to sub"""
    try:
        sub = Sub.get(Sub.sid == sid)
    except Sub.DoesNotExist:
        return jsonify(status="error", error=_("sub not found"))

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    if current_user.has_subscribed(sid):
        return jsonify(status="ok", message=_("already subscribed"))

    if sub.sid == config.site.ann_sub:
        return jsonify(status="error", message=_("Sub is announcement sub"))

    form = CsrfTokenOnlyForm()
    if form.validate():
        if current_user.has_blocked(sid):
            ss = SubSubscriber.get(
                (SubSubscriber.uid == current_user.uid)
                & (SubSubscriber.sid == sid)
                & (SubSubscriber.status == 2)
            )
            ss.delete_instance()

        SubSubscriber.create(
            time=datetime.datetime.utcnow(), uid=current_user.uid, sid=sid, status=1
        )
        Sub.update(subscribers=Sub.subscribers + 1).where(Sub.sid == sid).execute()
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/unsubscribe/<sid>", methods=["POST"])
@login_required
def unsubscribe_from_sub(sid):
    """Unsubscribe from sub"""
    try:
        sub = Sub.get(Sub.sid == sid)
    except Sub.DoesNotExist:
        return jsonify(status="error", error=_("sub not found"))

    if not current_user.has_subscribed(sid):
        return jsonify(status="ok", message=_("not subscribed"))

    if sub.sid == config.site.ann_sub:
        return jsonify(status="error", message=_("Sub is announcement sub"))

    form = CsrfTokenOnlyForm()
    if form.validate():
        ss = SubSubscriber.get(
            (SubSubscriber.uid == current_user.uid)
            & (SubSubscriber.sid == sid)
            & (SubSubscriber.status == 1)
        )
        ss.delete_instance()

        Sub.update(subscribers=Sub.subscribers - 1).where(Sub.sid == sid).execute()
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/block/<sid>", methods=["POST"])
@login_required
def block_sub(sid):
    """Block sub"""
    try:
        sub = Sub.get(Sub.sid == sid)
    except Sub.DoesNotExist:
        return jsonify(status="error", error=_("sub not found"))

    if current_user.has_blocked(sid):
        return jsonify(status="ok", message=_("already blocked"))

    if sub.sid == config.site.ann_sub:
        return jsonify(status="error", message=_("Sub is announcement sub"))

    form = CsrfTokenOnlyForm()
    if form.validate():
        if current_user.has_subscribed(sub.name):
            ss = SubSubscriber.get(
                (SubSubscriber.uid == current_user.uid)
                & (SubSubscriber.sid == sid)
                & (SubSubscriber.status == 1)
            )
            ss.delete_instance()
            Sub.update(subscribers=Sub.subscribers - 1).where(Sub.sid == sid).execute()

        SubSubscriber.create(
            time=datetime.datetime.utcnow(), uid=current_user.uid, sid=sid, status=2
        )
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/unblock/<sid>", methods=["POST"])
@login_required
def unblock_sub(sid):
    """Unblock sub"""
    try:
        sub = Sub.get(Sub.sid == sid)
    except Sub.DoesNotExist:
        return jsonify(status="error", error=_("sub not found"))

    if not current_user.has_blocked(sid):
        return jsonify(status="ok", message=_("sub not blocked"))

    if sub.sid == config.site.ann_sub:
        return jsonify(status="error", message=_("Sub is announcement sub"))

    form = CsrfTokenOnlyForm()
    if form.validate():
        ss = SubSubscriber.get(
            (SubSubscriber.uid == current_user.uid)
            & (SubSubscriber.sid == sub.sid)
            & (SubSubscriber.status == 2)
        )
        ss.delete_instance()
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/get_txtpost/<pid>", methods=["GET"])
def get_txtpost(pid):
    """Sub text post expando get endpoint"""
    try:
        post = misc.getSinglePost(pid)
    except SubPost.DoesNotExist:
        return abort(404)

    sub = Sub.get(Sub.sid == post["sid"])
    if sub.status != 0 and not current_user.is_admin():
        abort(403)

    post["visibility"] = ""
    if post["deleted"] == 1:
        if current_user.uid == post["uid"]:
            post["visibility"] = "user-self-del"
        elif current_user.is_admin():
            post["visibility"] = "admin-self-del"
        elif current_user.is_mod(post["sid"], 1):
            post["visibility"] = "mod-self-del"
        else:
            post["visibility"] = "none"
    elif post["deleted"] == 2:
        if (
            current_user.is_admin()
            or current_user.is_mod(post["sid"], 1)
            or current_user.uid == post["uid"]
        ):
            post["visibility"] = "mod-del"
        else:
            post["visibility"] = "none"

    if post["userstatus"] == 10 and post["deleted"] == 1:
        post["visibility"] = "none"

    if post["visibility"] == "none":
        abort(404)

    cont = misc.our_markdown(post["content"])
    if post["ptype"] == 3:
        pollData = {"has_voted": False}
        postmeta = misc.metadata_to_dict(
            SubPostMetadata.select().where(SubPostMetadata.pid == pid)
        )
        # poll. grab options and votes.
        options = (
            SubPostPollOption.select(
                SubPostPollOption.id,
                SubPostPollOption.text,
                fn.Count(SubPostPollVote.id).alias("votecount"),
            )
            .join(
                SubPostPollVote,
                JOIN.LEFT_OUTER,
                on=(SubPostPollVote.vid == SubPostPollOption.id),
            )
            .where(SubPostPollOption.pid == pid)
            .group_by(SubPostPollOption.id)
            .order_by(SubPostPollOption.id)
        )
        pollData["options"] = options
        total_votes = SubPostPollVote.select().where(SubPostPollVote.pid == pid).count()
        pollData["total_votes"] = total_votes
        if current_user.is_authenticated:
            # Check if user has already voted on this poll.
            try:
                u_vote = SubPostPollVote.get(
                    (SubPostPollVote.pid == pid)
                    & (SubPostPollVote.uid == current_user.uid)
                )
                pollData["has_voted"] = True
                pollData["voted_for"] = u_vote.vid_id
            except SubPostPollVote.DoesNotExist:
                pollData["has_voted"] = False

        # Check if the poll is open
        pollData["poll_open"] = True
        if "poll_closed" in postmeta:
            pollData["poll_open"] = False

        if "poll_closes_time" in postmeta:
            pollData["poll_closes"] = datetime.datetime.utcfromtimestamp(
                int(postmeta["poll_closes_time"])
            ).isoformat()
            if int(postmeta["poll_closes_time"]) < time.time():
                pollData["poll_open"] = False

        cont = engine.get_template("sub/postpoll.html").render(
            {"post": post, "pollData": pollData, "postmeta": postmeta}
        )

    return jsonify(status="ok", content=cont)


@do.route("/do/distinguish", methods=["POST"])
@login_required
def distinguish():
    """Allows a mod or admin to distinguish a comment or post"""

    form = DistinguishForm()
    cid = form.cid.data
    pid = form.pid.data
    as_admin = form.as_admin.data
    if cid:
        try:
            item = SubPostComment.get(SubPostComment.cid == cid)
            post = SubPost.get(SubPost.pid == item.pid)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=[_("Post/Comment not found")])
    elif pid:
        try:
            item = SubPost.get(SubPost.pid == pid)
            post = item
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=[_("Post not found")])
    else:
        return jsonify(status="error", error=[_("Error")])

    if form.pid is None and form.cid is None:
        return jsonify(status="error", error=[_("Nothing to distinguish")])

    if str(item.uid) != str(current_user.uid):
        return jsonify(status="error", error=[_("You are not the author of the item")])

    is_mod = current_user.is_mod(post.sid, 1)
    if not (is_mod or current_user.is_admin()):
        return jsonify(status="error", error=[_("You must be a mod or admin")])

    if item.distinguish != 0 and item.distinguish is not None:
        item.distinguish = 0
    elif is_mod and not as_admin:
        item.distinguish = 1
    else:
        item.distinguish = 2

    item.save()
    return jsonify(status="ok")


@do.route("/do/lock_comments/<int:post>", methods=["POST"])
@login_required
def toggle_lock_comments(post):
    """Allows a mod or admin to lock a post to new comments."""
    try:
        post = SubPost.get(SubPost.pid == post)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=_("Post does not exist"))

    if not current_user.is_mod(post.sid_id):
        abort(403)

    form = DeletePost()

    if form.validate():
        try:
            smd = (
                SubPostMetadata.select()
                .where(
                    (SubPostMetadata.key == "lock-comments")
                    & (SubPostMetadata.pid == post.pid)
                )
                .get()
            )
            smd.value = "0" if smd.value == "1" else "1"
            smd.save()
        except SubPostMetadata.DoesNotExist:
            smd = SubPostMetadata.create(pid=post.pid, key="lock-comments", value="1")

        misc.create_sublog(
            misc.LOG_TYPE_LOCK_COMMENTS,
            current_user.uid,
            post.sid_id,
            comment=smd.value,
            link=url_for("site.view_post_inbox", pid=post.pid),
            target=post.uid,
        )

    return jsonify(status="ok")


@do.route("/do/edit_txtpost/<pid>", methods=["POST"])
@login_required
def edit_txtpost(pid):
    """Sub text post creation endpoint"""
    form = EditSubTextPostForm()
    if form.validate():
        try:
            post = SubPost.get(SubPost.pid == pid)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=[_("Post not found")])

        sub = Sub.get(Sub.sid == post.sid)

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if post.deleted != 0:
            return jsonify(status="error", error=[_("Post was deleted")])

        if current_user.is_subban(post.sid):
            return jsonify(status="error", error=[_("You are banned on this sub.")])

        if misc.is_archived(post):
            return jsonify(status="error", error=[_("Post is archived")])

        dt = datetime.datetime.utcnow()
        SubPostContentHistory.create(pid=post.pid, content=post.content, datetime=dt)

        post.content = form.content.data
        # Only save edited time if it was posted more than five minutes ago
        if (
            datetime.datetime.utcnow() - post.posted.replace(tzinfo=None)
        ).seconds > 300:
            post.edited = datetime.datetime.utcnow()
        post.save()
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/grabtitle", methods=["POST"])
@gevent_required  # Makes external HTTP request.
@login_required
@ratelimit(POSTING_LIMIT)
def grab_title():
    """Safely grabs the <title> from a page"""
    url = request.json.get("u").strip()
    if not url:
        abort(400)
    return tasks.grab_title(url)


@do.route("/do/sendcomment/<pid>", methods=["POST"])
@login_required
@ratelimit(POSTING_LIMIT)
def create_comment(pid):
    """Here we send comments."""
    form = PostComment()
    if form.validate():
        if pid == "0":
            pid = form.post.data

        if not current_user.is_admin() and not config.site.enable_posting:
            return (
                jsonify(
                    status="error", error=[_("Posting has been temporarily disabled")]
                ),
                400,
            )

        try:
            post = SubPost.get(SubPost.pid == pid)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=[_("Post does not exist")]), 400
        if post.deleted:
            return jsonify(status="error", error=[_("Post was deleted")]), 400

        if misc.is_archived(post):
            return jsonify(status="error", error=[_("Post is archived")]), 400

        postmeta = misc.metadata_to_dict(
            SubPostMetadata.select().where(SubPostMetadata.pid == pid)
        )
        if postmeta.get("lock-comments"):
            return (
                jsonify(status="error", error=[_("Comments are closed on this post.")]),
                400,
            )

        try:
            sub = Sub.get(Sub.sid == post.sid_id)
        except Sub.DoesNotExist:
            return jsonify(status="error", error=_("Internal error")), 400

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if current_user.is_subban(sub):
            return (
                jsonify(
                    status="error",
                    error=[_("You are currently banned from commenting")],
                ),
                400,
            )

        if form.parent.data != "0":
            try:
                parent = SubPostComment.get(SubPostComment.cid == form.parent.data)
            except SubPostComment.DoesNotExist:
                return (
                    jsonify(status="error", error=[_("Parent comment does not exist")]),
                    400,
                )

            # XXX: We check both for None and 0 because I've found both on a Phuks snapshot...
            if (
                parent.status is not None and parent.status != 0
            ) or parent.pid.pid != post.pid:
                return (
                    jsonify(status="error", error=[_("Parent comment does not exist")]),
                    400,
                )

        self_vote = 1 if config.site.self_voting.comments else 0
        comment = SubPostComment.create(
            pid=pid,
            uid=current_user.uid,
            content=form.comment.data.encode(),
            parentcid=form.parent.data if form.parent.data != "0" else None,
            time=datetime.datetime.utcnow(),
            cid=uuid.uuid4(),
            best_score=misc.best_score(self_vote, self_vote, self_vote),
            score=self_vote,
            upvotes=self_vote,
            downvotes=0,
        )
        SubPost.update(comments=SubPost.comments + 1).where(
            SubPost.pid == post.pid
        ).execute()

        if config.site.self_voting.comments:
            SubPostCommentVote.create(
                cid=comment.cid, uid=current_user.uid, positive=True
            )
            User.update(given=User.given + 1).where(
                User.uid == current_user.uid
            ).execute()

        socketio.emit(
            "threadcomments",
            {"pid": post.pid, "comments": post.comments + 1},
            namespace="/snt",
            room=post.pid,
        )
        comment_text = BeautifulSoup(
            misc.our_markdown(comment.content.decode()), features="lxml"
        ).findAll(string=True)
        comment_res = misc.word_truncate("".join(comment_text).replace("\n", " "), 150)
        defaults = [
            x.value for x in SiteMetadata.select().where(SiteMetadata.key == "default")
        ]
        if config.site.recent_activity.live and sub.private == 0:
            show_sidebar = (
                sub.sid in defaults or not config.site.recent_activity.defaults_only
            )
            show_sidebar = (
                show_sidebar and not config.site.recent_activity.comments_only
            )

            socketio.emit(
                "comment",
                {
                    # "sub": sub.name,
                    "show_sidebar": show_sidebar,
                    "user": current_user.name,
                    # "pid": post.pid,
                    # "sid": sub.sid,
                    "nsfw": post.nsfw or sub.nsfw,
                    # "private": sub.private,
                    "content": comment_res,
                    "comment_url": url_for(
                        "sub.view_perm", sub=sub.name, cid=comment.cid, pid=pid
                    )
                    + "#comment-"
                    + str(comment.cid),
                    "sub_url": url_for("sub.view_sub", sub=sub.name),
                },
                namespace="/snt",
                # room=post.pid,
            )

        # 5 - send pm to parent
        if form.parent.data != "0":
            parent = SubPostComment.get(SubPostComment.cid == form.parent.data)
            to = parent.uid.uid
            ntype = "COMMENT_REPLY"
            # Check if notifications are disabled for the parent comment
            if parent.noreplies == 1:
                # Notifications are disabled, so don't send a notification to the comment author
                to = None
        else:
            to = post.uid.uid
            ntype = "POST_REPLY"
            # Check if notifications are disabled for the post
            if post.noreplies == 1:
                # Notifications are disabled, so don't send a notification to the post author
                to = None

        if to and to != current_user.uid and not current_user.is_shadowbanned:
            if ntype in {"COMMENT_REPLY", "POST_REPLY"}:
                notifications.send(
                    ntype,
                    sub=post.sid,
                    post=post.pid,
                    comment=comment.cid,
                    sender=current_user.uid,
                    target=to,
                )

        subMods = misc.getSubMods(sub.sid)
        include_history = current_user.is_mod(sub.sid, 1) or current_user.is_admin()

        # 6 - Process mentions
        misc.workWithMentions(form.comment.data, to, post, sub, cid=comment.cid)
        renderedComment = engine.get_template("sub/postcomments.html").render(
            {
                "post": misc.getSinglePost(post.pid),
                "postmeta": misc.metadata_to_dict(
                    SubPostMetadata.select().where(SubPostMetadata.pid == post.pid)
                ),
                "comments": misc.get_comment_tree(
                    post.pid,
                    sub.sid,
                    [{"cid": str(comment.cid), "parentcid": None}],
                    uid=current_user.uid,
                    include_history=include_history,
                    filter_shadowbanned=True,
                ),
                "subInfo": misc.getSubData(sub.sid),
                "commentscore_delay": sub.commentscore_delay,
                "subMods": subMods,
                "highlight": str(comment.cid),
                "sort": "new",
            }
        )

        return json.dumps(
            {
                "status": "ok",
                "addr": url_for(
                    "sub.view_perm", sub=sub.name, pid=pid, cid=comment.cid
                ),
                "comment": renderedComment,
                "cid": str(comment.cid),
            }
        )
    return json.dumps({"status": "error", "error": get_errors(form)}), 400


@do.route("/do/upload_image", methods=["POST"])
@ratelimit(POSTING_LIMIT)
@login_required
def upload_image():
    # Early return if user can't upload
    if not current_user.canupload:
        return jsonify(
            status="error", error=_("You do not have sufficient level to upload images")
        )
    # Check daily upload limit
    if not current_user.is_admin():
        today = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        daily_uploads = (
            UserUploads.select()
            .where(UserUploads.uid == current_user.uid)
            .where(UserUploads.timestamp > today)
            .count()
        )

        if daily_uploads >= config.site.daily_site_upload_limit:
            return jsonify(
                status="error", error=_("You have uploaded too many images today")
            )

    # Get pid first to avoid unnecessary file processing if invalid
    pid = request.form.get("pid")
    if pid:
        # Check if pid exists and is valid
        post = SubPost.get_or_none(SubPost.pid == pid)
        if not post:
            return jsonify(status="error", error=_("Invalid post ID"))

    filename, success = storage.upload_file()
    if not success:
        return jsonify(status="error", error=filename)

    try:
        # Create a UserUploads record
        upload = UserUploads.create(
            uid=current_user.uid,
            fileid=filename,
            status=1,
            pid=pid if pid else None,
            thumbnail=None,
            timestamp=datetime.datetime.utcnow(),
        )

        # Spawn thumbnail creation task
        create_thumbnail(filename, [(UserUploads, "xid", upload.xid)])

        image_url = storage.file_url(filename)
        return jsonify(status="ok", image_url=image_url)

    except Exception as e:
        print(f"Error processing upload: {e}")
        return jsonify(status="error", error=_("Failed to process upload"))


@do.route("/do/sendmsg", methods=["POST"])
@ratelimit(POSTING_LIMIT)
@login_required
def create_sendmsg():
    """User PM message creation endpoint"""
    form = CreateUserMessageForm()
    if form.validate():
        try:
            user = User.get(fn.Lower(User.name) == form.to.data.lower())
        except User.DoesNotExist:
            return json.dumps({"status": "error", "error": [_("User does not exist")]})
        misc.create_message(
            mfrom=current_user.uid,
            to=user.uid,
            subject=form.subject.data,
            content=form.content.data,
            mtype=MessageType.USER_TO_USER,
        )
        return json.dumps({"status": "ok", "sentby": current_user.get_id()})
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/replymsg", methods=["POST"])
@ratelimit(POSTING_LIMIT)
@login_required
def create_replymsg():
    """User PM message reply creation endpoint"""
    form = CreateUserMessageReplyForm()
    if form.validate():
        try:
            message = Message.get(Message.mid == int(form.mid.data))
        except (ValueError, Message.DoesNotExist):
            return json.dumps(
                {"status": "error", "error": [_("Message does not exist")]}
            )
        if message.receivedby.uid != current_user.uid:
            return json.dumps({"status": "error", "error": [_("Invalid message")]})

        misc.create_message_reply(message=message, content=form.content.data)
        return json.dumps({"status": "ok", "sentby": current_user.uid})

    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/ban_user_sub/<sub>", methods=["POST"])
@login_required
def ban_user_sub(sub):
    """Ban user from sub endpoint"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if not current_user.is_mod(sub.sid, 2):
        return jsonify(status="error", error=[_("Not authorized")])
    form = BanUserSubForm()
    if form.validate():
        try:
            user = User.get(fn.Lower(User.name) == form.user.data.lower())
        except User.DoesNotExist:
            return jsonify(status="error", error=[_("User does not exist")])

        # XXX: This is all SDBH does so it stays commented out for now
        # try:
        #    SubMod.get((SubMod.sid == sub.sid) & (SubMod.uid == user.uid))
        #    return jsonify(status='error', error=['User is a moderator'])
        # except SubMod.DoesNotExist:
        #    pass

        expires = None
        days = ""
        if form.expires.data:
            try:
                expires = datetime.datetime.strptime(
                    form.expires.data, "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                seconds = (expires - datetime.datetime.utcnow()).total_seconds()
                days = int(round(seconds / (60 * 60 * 24), 0))
                if days > 365:
                    return jsonify(
                        status="error",
                        error=[_("Expiration time too far into the future")],
                    )
            except ValueError:
                return jsonify(status="error", error=[_("Invalid expiration time")])

            if datetime.datetime.utcnow() > expires:
                return jsonify(
                    status="error", error=[_("Expiration date is in the past")]
                )

        if misc.is_sub_banned(sub, uid=user.uid):
            return jsonify(status="error", error=[_("Already banned")])

        sublink = misc.sub_markdown_link(sub.name)
        target_language = user.language
        if target_language == "sk":
            locale_language = "sk_SK"
        elif target_language == "cs":
            locale_language = "cs_CZ"
        elif target_language == "en":
            locale_language = "en_US"
        elif target_language == "es":
            locale_language = "es_ES"
        elif target_language == "ru":
            locale_language = "ru_RU"
        else:
            locale_language = "en_US"  # Default language if no target language found

        with force_locale(locale_language):
            if expires is None:
                if not current_user.is_mod(sub.sid, 1):
                    return jsonify(
                        status="error",
                        error=[_("Janitors may only create temporary bans")],
                    )
                subject = _("Moderation action: permanent ban")
                content = _(
                    "You have been permanently banned from %(sublink)s. Reason: %(reason)s",
                    sublink=sublink,
                    reason=form.reason.data,
                )
            else:
                subject = _("Moderation action: temporary ban")
                content = ngettext(
                    "You have been banned from %(sublink)s for %(num)d day. Reason: %(reason)s",
                    "You have been banned from %(sublink)s for %(num)d days. Reason: %(reason)s",
                    days,
                    sublink=sublink,
                    reason=form.reason.data,
                )

            misc.create_notification_message(
                mfrom=current_user.uid,
                as_admin=False,
                sub=sub.sid,
                to=user.uid,
                subject=subject,
                content=content,
            )
        SubBan.create(
            sid=sub.sid,
            uid=user.uid,
            reason=form.reason.data,
            created_by=current_user.uid,
            expires=expires,
        )

        misc.create_sublog(
            misc.LOG_TYPE_SUB_BAN,
            current_user.uid,
            sub.sid,
            target=user.uid,
            comment=form.reason.data,
            link=str(days),
        )

        related_post_reports = (
            SubPostReport.select()
            .join(SubPost)
            .where(SubPost.uid == user.uid)
            .join(Sub)
            .where(Sub.sid == sub.sid)
        )
        related_comment_reports = (
            SubPostCommentReport.select()
            .join(SubPostComment)
            .where(SubPostComment.uid == user.uid)
            .join(SubPost)
            .join(Sub)
            .where(Sub.sid == sub.sid)
        )

        if expires:
            desc = _(
                "banned for: %(reason)s, until %(expires)s",
                reason=form.reason.data,
                expires=expires,
            )
        else:
            desc = _("banned for: %(reason)s, permanent", reason=form.reason.data)

        for report in related_post_reports:
            misc.create_reportlog(
                misc.LOG_TYPE_REPORT_USER_SUB_BANNED,
                current_user.uid,
                report.id,
                log_type="post",
                desc=desc,
            )
        for report in related_comment_reports:
            misc.create_reportlog(
                misc.LOG_TYPE_REPORT_USER_SUB_BANNED,
                current_user.uid,
                report.id,
                log_type="comment",
                desc=desc,
            )

        cache.delete_memoized(misc.is_sub_banned, sub, uid=user.uid)
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/inv_mod/<sub>", methods=["POST"])
@login_required
def inv_mod(sub):
    """User PM for Mod2 invite endpoint"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    try:
        SubMod.get(
            (SubMod.sid == sub.sid)
            & (SubMod.uid == current_user.uid)
            & (SubMod.power_level == 0)
            & (~SubMod.invite)
        )
        is_owner = True
    except SubMod.DoesNotExist:
        is_owner = False

    if is_owner or current_user.is_admin():
        form = EditMod2Form()
        if form.validate():
            try:
                user = User.get(fn.Lower(User.name) == form.user.data.lower())
            except User.DoesNotExist:
                return jsonify(status="error", error=[_("User does not exist")])

            try:
                SubMod.get(
                    (SubMod.sid == sub.sid)
                    & (SubMod.uid == user.uid)
                    & (~SubMod.invite)
                )
                return jsonify(status="error", error=[_("User is already a mod")])
            except SubMod.DoesNotExist:
                pass

            try:
                SubMod.get(
                    (SubMod.sid == sub.sid) & (SubMod.uid == user.uid) & SubMod.invite
                )
                return jsonify(status="error", error=[_("User has a pending invite")])
            except SubMod.DoesNotExist:
                pass

            if form.level.data in ("1", "2"):
                power_level = int(form.level.data)
            else:
                return jsonify(status="error", error=[_("Invalid power level")])

            moddedCount = (
                SubMod.select()
                .where(
                    (SubMod.uid == user.uid)
                    & (1 <= SubMod.power_level <= 2)
                    & (~SubMod.invite)
                )
                .count()
            )
            if moddedCount >= 20:
                # TODO: Adjust by level
                return jsonify(
                    status="error", error=[_("User can't mod more than 20 subs")]
                )
            target_language = user.language
            if target_language == "sk":
                locale_language = "sk_SK"
            elif target_language == "cs":
                locale_language = "cs_CZ"
            elif target_language == "en":
                locale_language = "en_US"
            elif target_language == "es":
                locale_language = "es_ES"
            elif target_language == "ru":
                locale_language = "ru_RU"
            else:
                locale_language = (
                    "en_US"  # Default language if no target language found
                )

            with force_locale(locale_language):
                misc.create_notification_message(
                    mfrom=current_user.uid,
                    as_admin=not is_owner,
                    sub=sub.sid,
                    to=user.uid,
                    subject=_("Invitation to become a moderator"),
                    content=_(
                        "%(userlink)s invited you to moderate %(sublink)s. "
                        "Please [click here](%(invitelink)s) to accept or reject the invitation.",
                        userlink=misc.user_markdown_link(current_user.name),
                        sublink=misc.sub_markdown_link(sub.name),
                        invitelink=url_for("sub.edit_sub_mods", sub=sub.name),
                    ),
                )

            SubMod.create(
                sid=sub.sid, user=user.uid, power_level=power_level, invite=True
            )

            misc.create_sublog(
                misc.LOG_TYPE_SUB_MOD_INVITE,
                current_user.uid,
                sub.sid,
                target=user.uid,
                admin=True if (not is_owner and current_user.is_admin()) else False,
            )

            return jsonify(status="ok")
        return json.dumps({"status": "error", "error": get_errors(form)})
    else:
        abort(403)


@do.route("/do/inv_member/<sub>", methods=["POST"])
@login_required
def inv_member(sub):
    """User PM for member invite endpoint"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    try:
        SubMod.get(
            (SubMod.sid == sub.sid)
            & (SubMod.uid == current_user.uid)
            & (SubMod.power_level == 0)
            & (~SubMod.invite)
        )
        is_owner = True
    except SubMod.DoesNotExist:
        is_owner = False

    if is_owner or current_user.is_admin():
        form = EditMemberForm()
        if form.validate():
            try:
                user = User.get(fn.Lower(User.name) == form.user.data.lower())
            except User.DoesNotExist:
                return jsonify(status="error", error=[_("User does not exist")])

            try:
                SubSubscriber.get(
                    (SubSubscriber.sid == sub.sid) & (SubSubscriber.uid == user.uid)
                )
                return jsonify(status="error", error=[_("User is already a member")])
            except SubSubscriber.DoesNotExist:
                pass

            try:
                SubMetadata.get(
                    (SubMetadata.sid == sub.sid)
                    & (SubMetadata.key == "member_invite")
                    & (SubMetadata.value == user.uid)
                )
                return jsonify(status="error", error=[_("User has a pending invite")])
            except SubMetadata.DoesNotExist:
                pass

            target_language = user.language
            if target_language == "sk":
                locale_language = "sk_SK"
            elif target_language == "cs":
                locale_language = "cs_CZ"
            elif target_language == "en":
                locale_language = "en_US"
            elif target_language == "es":
                locale_language = "es_ES"
            elif target_language == "ru":
                locale_language = "ru_RU"
            else:
                locale_language = (
                    "en_US"  # Default language if no target language found
                )

            with force_locale(locale_language):
                misc.create_notification_message(
                    mfrom=current_user.uid,
                    as_admin=not is_owner,
                    sub=sub.sid,
                    to=user.uid,
                    subject=_("Invitation to become a member"),
                    content=_(
                        "%(userlink)s invited you to become a member of %(sublink)s. "
                        "Please [click here](%(invitelink)s) to accept or reject the invitation.",
                        userlink=misc.user_markdown_link(current_user.name),
                        sublink=misc.sub_markdown_link(sub.name),
                        invitelink=url_for("sub.edit_sub_members", sub=sub.name),
                    ),
                )

            SubMetadata.create(key="member_invite", value=user.uid, sid=sub.sid)

            misc.create_sublog(
                misc.LOG_TYPE_SUB_MEMBER_INVITE,
                current_user.uid,
                sub.sid,
                target=user.uid,
                admin=True if (not is_owner and current_user.is_admin()) else False,
            )

            return jsonify(status="ok")
        return json.dumps({"status": "error", "error": get_errors(form)})
    else:
        abort(403)


@do.route("/do/remove_sub_ban/<sub>/<user>", methods=["POST"])
@login_required
def remove_sub_ban(sub, user):
    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    form = CsrfTokenOnlyForm()
    if form.validate():
        if current_user.is_mod(sub.sid, 2) or current_user.is_admin():
            try:
                sb = SubBan.get(
                    (SubBan.sid == sub.sid)
                    & (SubBan.uid == user.uid)
                    & (
                        SubBan.effective
                        & (
                            (SubBan.expires.is_null(True))
                            | (SubBan.expires > datetime.datetime.utcnow())
                        )
                    )
                )
            except SubBan.DoesNotExist:
                return jsonify(status="error", error=[_("User is not banned")])

            if (
                not current_user.is_mod(sub.sid, 1)
                and sb.created_by_id != current_user.uid
            ):
                return jsonify(
                    status="error",
                    error=[_("Janitors may only remove bans placed by themselves")],
                )

            sb.effective = False
            sb.expires = datetime.datetime.utcnow()
            sb.save()
            as_admin = not current_user.is_mod(sub.sid, 1) and current_user.is_admin()
            target_language = user.language
            if target_language == "sk":
                locale_language = "sk_SK"
            elif target_language == "cs":
                locale_language = "cs_CZ"
            elif target_language == "en":
                locale_language = "en_US"
            elif target_language == "es":
                locale_language = "es_ES"
            elif target_language == "ru":
                locale_language = "ru_RU"
            else:
                locale_language = (
                    "en_US"  # Default language if no target language found
                )

            with force_locale(locale_language):
                misc.create_notification_message(
                    mfrom=current_user.uid,
                    as_admin=as_admin,
                    sub=sub.sid,
                    to=user.uid,
                    subject=_("Moderation action: ban removed"),
                    content=_(
                        "You are no longer banned from posting in %(sublink)s.",
                        sublink=misc.sub_markdown_link(sub.name),
                    ),
                )

            misc.create_sublog(
                misc.LOG_TYPE_SUB_UNBAN,
                current_user.uid,
                sub.sid,
                target=user.uid,
                admin=as_admin,
            )

            related_post_reports = (
                SubPostReport.select()
                .join(SubPost)
                .where(SubPost.uid == user.uid)
                .join(Sub)
                .where(Sub.sid == sub.sid)
            )
            related_comment_reports = (
                SubPostCommentReport.select()
                .join(SubPostComment)
                .where(SubPostComment.uid == user.uid)
                .join(SubPost)
                .join(Sub)
                .where(Sub.sid == sub.sid)
            )
            for report in related_post_reports:
                misc.create_reportlog(
                    misc.LOG_TYPE_REPORT_USER_SUB_UNBANNED,
                    current_user.uid,
                    report.id,
                    log_type="post",
                )
            for report in related_comment_reports:
                misc.create_reportlog(
                    misc.LOG_TYPE_REPORT_USER_SUB_UNBANNED,
                    current_user.uid,
                    report.id,
                    log_type="comment",
                )

            cache.delete_memoized(misc.is_sub_banned, sub, uid=user.uid)
            return jsonify(status="ok", msg=_("Ban removed"))
        else:
            abort(403)
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/remove_mod2/<sub>/<user>", methods=["POST"])
@login_required
def remove_mod2(sub, user):
    """Remove Mod2"""
    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    form = CsrfTokenOnlyForm()
    if form.validate():
        isTopMod = current_user.is_mod(sub.sid, 0)
        if (
            isTopMod
            or current_user.is_admin()
            or (current_user.uid == user.uid and current_user.is_mod(sub.sid))
        ):
            try:
                mod = SubMod.get(
                    (SubMod.sid == sub.sid)
                    & (SubMod.uid == user.uid)
                    & (SubMod.power_level != 0)
                    & (~SubMod.invite)
                )
            except SubMod.DoesNotExist:
                return jsonify(status="error", error=[_("User is not mod")])

            mod.delete_instance()
            SubMetadata.create(sid=sub.sid, key="xmod2", value=user.uid)

            misc.create_sublog(
                misc.LOG_TYPE_SUB_MOD_REMOVE,
                current_user.uid,
                sub.sid,
                target=user.uid,
                admin=True if (not isTopMod and current_user.is_admin()) else False,
            )

            return jsonify(
                status="ok", resign=True if current_user.uid == user.uid else False
            )
        else:
            return jsonify(status="error", error=[_("Access denied")])
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/revoke_mod2inv/<sub>/<user>", methods=["POST"])
@login_required
def revoke_mod2inv(sub, user):
    """revoke Mod2 inv"""
    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])
    form = CsrfTokenOnlyForm()
    if form.validate():
        isTopMod = current_user.is_mod(sub.sid, 0)
        if isTopMod or current_user.is_admin():
            try:
                submod = SubMod.get(
                    (SubMod.sid == sub.sid) & (SubMod.uid == user.uid) & SubMod.invite
                )
            except SubMetadata.DoesNotExist:
                return jsonify(
                    status="error",
                    error=[_("User has not been invited to moderate the sub")],
                )
            target_language = user.language
            if target_language == "sk":
                locale_language = "sk_SK"
            elif target_language == "cs":
                locale_language = "cs_CZ"
            elif target_language == "en":
                locale_language = "en_US"
            elif target_language == "es":
                locale_language = "es_ES"
            elif target_language == "ru":
                locale_language = "ru_RU"
            else:
                locale_language = (
                    "en_US"  # Default language if no target language found
                )

            with force_locale(locale_language):
                misc.create_notification_message(
                    mfrom=current_user.uid,
                    as_admin=not isTopMod,
                    sub=sub.sid,
                    to=user.uid,
                    subject=_("Moderation invitation revoked"),
                    content=_(
                        "%(userlink)s cancelled your invitation to moderate %(sublink)s.",
                        userlink=misc.user_markdown_link(current_user.name),
                        sublink=misc.sub_markdown_link(sub.name),
                    ),
                )
            submod.delete_instance()

            misc.create_sublog(
                misc.LOG_TYPE_SUB_MOD_INV_CANCEL,
                current_user.uid,
                sub.sid,
                target=user.uid,
                admin=not isTopMod,
            )

            return jsonify(status="ok")
        else:
            return jsonify(status="error", error=[_("Access denied")])
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/revoke_memberinv/<sub>/<user>", methods=["POST"])
@login_required
def revoke_memberinv(sub, user):
    """Revoke the invite for a user to become a member of a subreddit"""
    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    try:
        SubMod.get(
            (SubMod.sid == sub.sid)
            & (SubMod.uid == current_user.uid)
            & (SubMod.power_level == 0)
            & (~SubMod.invite)
        )
        is_owner = True
    except SubMod.DoesNotExist:
        is_owner = False

    if is_owner or current_user.is_admin():
        form = CsrfTokenOnlyForm()
        if form.validate():
            try:
                # Check if the user has a pending invite for membership
                sub_metadata = SubMetadata.get(
                    (SubMetadata.sid == sub.sid)
                    & (SubMetadata.key == "member_invite")
                    & (SubMetadata.value == user.uid)
                )

                # Revoke the invite by deleting the SubMetadata entry
                sub_metadata.delete_instance()

                # Send notification that the invite has been revoked
                target_language = user.language
                if target_language == "sk":
                    locale_language = "sk_SK"
                elif target_language == "cs":
                    locale_language = "cs_CZ"
                elif target_language == "en":
                    locale_language = "en_US"
                elif target_language == "es":
                    locale_language = "es_ES"
                elif target_language == "ru":
                    locale_language = "ru_RU"
                else:
                    locale_language = "en_US"  # Default if no target language

                with force_locale(locale_language):
                    misc.create_notification_message(
                        mfrom=current_user.uid,
                        as_admin=not is_owner,
                        sub=sub.sid,
                        to=user.uid,
                        subject=_("Invitation to become a member revoked"),
                        content=_(
                            "%(userlink)s revoked your invitation to become a member of %(sublink)s.",
                            userlink=misc.user_markdown_link(current_user.name),
                            sublink=misc.sub_markdown_link(sub.name),
                        ),
                    )

                # Create a sub log for this action
                misc.create_sublog(
                    misc.LOG_TYPE_SUB_MEMBER_INV_CANCEL,
                    current_user.uid,
                    sub.sid,
                    target=user.uid,
                    admin=not is_owner,
                )

                return jsonify(status="ok")
            except SubMetadata.DoesNotExist:
                return jsonify(
                    status="error",
                    error=[_("User does not have a pending invite for membership")],
                )
        return json.dumps({"status": "error", "error": get_errors(form)})
    else:
        abort(403)


@do.route("/do/remove_member/<sub>/<user>", methods=["POST"])
@login_required
def remove_member(sub, user):
    """
    Remove a member of a subreddit (unsubscribe them).
    """
    try:
        # Fetch user by name (case-insensitive)
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    try:
        # Fetch sub by name (case-insensitive)
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    # Check if the sub is disabled and current user is not an admin
    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    # Determine if the current user is the owner
    is_owner = (
        SubMod.select()
        .where(
            (SubMod.sid == sub.sid)
            & (SubMod.uid == current_user.uid)
            & (SubMod.power_level == 0)
            & (~SubMod.invite)
        )
        .exists()
        or current_user.is_admin()
    )

    if not is_owner:
        abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return jsonify(status="error", error=get_errors(form))

    # Check if the user is subscribed to the sub
    is_subscribed = (
        SubSubscriber.select()
        .where(
            (SubSubscriber.uid == user.uid)
            & (SubSubscriber.sid == sub.sid)
            & (SubSubscriber.status == 1)
        )
        .exists()
    )

    if not is_subscribed:
        return jsonify(status="error", error=[_("User is not a member")])

    try:
        # Fetch and delete the subscription instance
        ss = SubSubscriber.get(
            (SubSubscriber.uid == user.uid)
            & (SubSubscriber.sid == sub.sid)
            & (SubSubscriber.status == 1)
        )
        ss.delete_instance()

        # Update sub's subscriber count
        Sub.update(subscribers=Sub.subscribers - 1).where(Sub.sid == sub.sid).execute()

        # Send notification
        locale_language = {
            "sk": "sk_SK",
            "cs": "cs_CZ",
            "en": "en_US",
            "es": "es_ES",
            "ru": "ru_RU",
        }.get(user.language, "en_US")

        with force_locale(locale_language):
            misc.create_notification_message(
                mfrom=current_user.uid,
                as_admin=not is_owner,
                sub=sub.sid,
                to=user.uid,
                subject=_("You are no longer subscribed"),
                content=_(
                    "%(userlink)s removed you from %(sublink)s.",
                    userlink=misc.user_markdown_link(current_user.name),
                    sublink=misc.sub_markdown_link(sub.name),
                ),
            )

        # Create a sub log entry
        misc.create_sublog(
            misc.LOG_TYPE_SUB_MEMBER_REMOVE,
            current_user.uid,
            sub.sid,
            target=user.uid,
            admin=not is_owner,
        )

        return jsonify(status="ok")
    except Exception as e:
        print(f"Error removing member: {e}")
        return jsonify(status="error", error=[_("An unexpected error occurred")])


@do.route("/do/accept_modinv/<sub>/<user>", methods=["POST"])
@login_required
def accept_modinv(sub, user):
    """Accept mod invite"""
    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    form = CsrfTokenOnlyForm()
    if form.validate():
        try:
            modi = SubMod.get(
                (SubMod.sid == sub.sid) & (SubMod.uid == user.uid) & SubMod.invite
            )
        except SubMod.DoesNotExist:
            return jsonify(
                status="error", error=_("You have not been invited to mod this sub")
            )

        moddedCount = (
            SubMod.select()
            .where(
                (SubMod.uid == user.uid)
                & (1 <= SubMod.power_level <= 2)
                & (~SubMod.invite)
            )
            .count()
        )
        if moddedCount >= 20:
            return jsonify(status="error", error=[_("You can't mod more than 20 subs")])

        modi.invite = False
        modi.save()
        SubMetadata.delete().where(
            (SubMetadata.sid == sub.sid)
            & (SubMetadata.key == "xmod2")
            & (SubMetadata.value == user.uid)
        ).execute()

        misc.create_sublog(
            misc.LOG_TYPE_SUB_MOD_ACCEPT, current_user.uid, sub.sid, target=user.uid
        )

        if not current_user.has_subscribed(sub.name):
            SubSubscriber.create(uid=current_user.uid, sid=sub.sid, status=1)
            Sub.update(subscribers=Sub.subscribers + 1).where(
                Sub.sid == sub.sid
            ).execute()
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/refuse_mod2inv/<sub>", methods=["POST"])
@login_required
def refuse_mod2inv(sub):
    """refuse Mod2"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    form = CsrfTokenOnlyForm()
    if form.validate():
        try:
            modi = SubMod.get(
                (SubMod.sid == sub.sid)
                & (SubMod.uid == current_user.uid)
                & SubMod.invite
            )
        except SubMetadata.DoesNotExist:
            return jsonify(
                status="error", error=_("You have not been invited to mod this sub")
            )

        modi.delete_instance()
        misc.create_sublog(
            misc.LOG_TYPE_SUB_MOD_INV_REJECT,
            current_user.uid,
            sub.sid,
            target=current_user.uid,
        )
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/accept_memberinv/<sub>/<user>", methods=["POST"])
@login_required
def accept_memberinv(sub, user):
    """Accept member invite"""
    try:
        # Fetch the user (case-insensitive)
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    try:
        # Fetch the sub (case-insensitive)
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    # Check if the sub is active or if the user is an admin
    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    # Validate CSRF token
    form = CsrfTokenOnlyForm()
    if form.validate():
        # Check if the current user was invited to the sub
        if not current_user.is_memberinv(sub.sid):
            return jsonify(
                status="error",
                error=_("You have not been invited to become a member of this sub"),
            )

        # Remove the invitation
        SubMetadata.delete().where(
            (SubMetadata.sid == sub.sid)
            & (SubMetadata.key == "member_invite")
            & (SubMetadata.value == user.uid)
        ).execute()

        # Log the acceptance in sub logs
        misc.create_sublog(
            misc.LOG_TYPE_SUB_MEMBER_ACCEPT, current_user.uid, sub.sid, target=user.uid
        )

        # Handle subscription if the user is not already subscribed
        if not current_user.has_subscribed(sub.name):
            SubSubscriber.create(uid=current_user.uid, sid=sub.sid, status=1)
            Sub.update(subscribers=Sub.subscribers + 1).where(
                Sub.sid == sub.sid
            ).execute()

        # Generate URL for the sub page
        addr = url_for("sub.view_sub", sub=sub.name)

        # Return success response with redirect address
        return jsonify(status="ok", addr=addr)

    # Handle form validation errors
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/refuse_memberinv/<sub>/<user>", methods=["POST"])
@login_required
def refuse_memberinv(sub, user):
    """refuse member invite"""
    try:
        # Fetch the user (case-insensitive)
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return jsonify(status="error", error=[_("User does not exist")])

    try:
        # Fetch the sub (case-insensitive)
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    # Check if the sub is active or if the user is an admin
    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    # Validate CSRF token
    form = CsrfTokenOnlyForm()
    if form.validate():
        # Check if the current user was invited to the sub
        if not current_user.is_memberinv(sub.sid):
            return jsonify(
                status="error",
                error=_("You have not been invited to become a member of this sub"),
            )

        # Remove the invitation
        SubMetadata.delete().where(
            (SubMetadata.sid == sub.sid)
            & (SubMetadata.key == "member_invite")
            & (SubMetadata.value == user.uid)
        ).execute()

        # Log the acceptance in sub logs
        misc.create_sublog(
            misc.LOG_TYPE_SUB_MEMBER_REFUSE, current_user.uid, sub.sid, target=user.uid
        )

        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/read_pm/<mid>", methods=["POST"])
@login_required
def read_pm(mid):
    """Mark PM as read"""
    try:
        Message.get(Message.mid == mid)
    except Message.DoesNotExist:
        return jsonify(status="error", error=[_("Message not found")])
    try:
        um = UserUnreadMessage.get(
            (UserUnreadMessage.uid == current_user.uid) & (UserUnreadMessage.mid == mid)
        )
    except UserUnreadMessage.DoesNotExist:
        return jsonify(status="ok")

    um.delete_instance()
    socketio.emit(
        "notification",
        {"count": misc.get_notification_count(current_user.uid)},
        namespace="/snt",
        room="user" + current_user.uid,
    )
    return jsonify(status="ok", mid=mid)


@do.route("/do/readall_msgs", methods=["POST"])
@login_required
def readall_msgs():
    """Mark all messages in the inbox as read"""
    unreads = misc.select_unread_messages(current_user.uid, UserUnreadMessage.id)
    UserUnreadMessage.delete().where(
        UserUnreadMessage.id << [u["id"] for u in unreads.dicts()]
    ).execute()

    socketio.emit(
        "notification",
        {"count": misc.get_notification_count(current_user.uid)},
        namespace="/snt",
        room="user" + current_user.uid,
    )
    return jsonify(status="ok")


@do.route("/do/delete_pm/<mid>", methods=["POST"])
@login_required
def delete_pm(mid):
    """Delete PM"""
    try:
        message = Message.get(Message.mid == mid)
        if message.receivedby_id != current_user.uid:
            return jsonify(status="error", error=_("Message does not exist"))

        UserUnreadMessage.delete().where(
            (UserUnreadMessage.uid == current_user.uid) & (UserUnreadMessage.mid == mid)
        ).execute()
        UserMessageMailbox.update(mailbox=MessageMailbox.DELETED).where(
            (UserMessageMailbox.uid == current_user.uid)
            & (UserMessageMailbox.mid == mid)
        ).execute()
        socketio.emit(
            "notification",
            {"count": misc.get_notification_count(current_user.uid)},
            namespace="/snt",
            room="user" + current_user.uid,
        )
        return jsonify(status="ok")
    except Message.DoesNotExist:
        return jsonify(status="error", error=_("Message does not exist"))


@do.route("/do/edit_title", methods=["POST"])
@ratelimit(POSTING_LIMIT)
@login_required
def edit_title():
    form = DeletePost()
    if form.validate():
        if not form.reason.data:
            return jsonify(status="error", error=_("Missing title"))

        if len(form.reason.data.strip(misc.WHITESPACE)) < 3:
            return jsonify(status="error", error=_("Title too short."))

        try:
            post = SubPost.get(SubPost.pid == form.post.data)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=_("Post does not exist"))
        sub = Sub.get(Sub.sid == post.sid)

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if current_user.is_subban(sub):
            return jsonify(status="error", error=_("You are banned on this sub."))

        if post.is_title_editable():
            return jsonify(
                status="error", error=_("You cannot edit the post title anymore")
            )

        if post.uid.uid != current_user.uid:
            return jsonify(status="error", error=_("You did not post this!"))

        dt = datetime.datetime.utcnow()
        SubPostTitleHistory.create(pid=post.pid, title=post.title, datetime=dt)

        post.title = form.reason.data
        post.save()
        socketio.emit(
            "threadtitle",
            {"pid": post.pid, "title": form.reason.data},
            namespace="/snt",
            room=post.pid,
        )

        return jsonify(status="ok")
    return jsonify(status="error", error=_("Bork bork"))


@do.route("/do/save_pm/<mid>", methods=["POST"])
@login_required
def save_pm(mid):
    """Save/Archive PM"""
    try:
        message = Message.get(Message.mid == mid)
        if message.receivedby_id != current_user.uid:
            return jsonify(status="error", error=_("Message does not exist"))

        (
            UserMessageMailbox.update(mailbox=MessageMailbox.SAVED).where(
                (UserMessageMailbox.uid == current_user.uid)
                & (UserMessageMailbox.mid == mid)
            )
        ).execute()

        return jsonify(status="ok")
    except Message.DoesNotExist:
        return jsonify(status="error", error=_("Message does not exist"))


@do.route("/do/admin/deleteannouncement", methods=["POST"])
@login_required
def deleteannouncement():
    """Removes the current announcement"""
    if not current_user.is_admin():
        abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        abort(400)

    try:
        ann = SiteMetadata.get(SiteMetadata.key == "announcement")
        post = SubPost.get(SubPost.pid == ann.value)
    except SiteMetadata.DoesNotExist:
        return redirect(url_for("admin.index"))

    flash(_("Removed announcement") + f": {Markup.escape(post.title)}")
    ann.delete_instance()
    misc.create_sitelog(
        misc.LOG_TYPE_UNANNOUNCE,
        uid=current_user.uid,
        link=url_for("sub.view_post", sub=post.sid.name, pid=post.pid),
    )

    cache.delete_memoized(misc.getAnnouncementPid)
    cache.delete_memoized(misc.getAnnouncement)
    socketio.emit("rmannouncement", {}, namespace="/snt")
    return redirect(url_for("admin.index"))


@do.route("/do/makeannouncement", methods=["POST"])
@login_required
def make_announcement():
    """Flagging post as announcement - not api"""
    if not current_user.is_admin():
        abort(403)

    form = AnnouncePostForm()

    if form.validate():
        try:
            curr_ann = SiteMetadata.get(SiteMetadata.key == "announcement")
            if int(curr_ann.value) == form.post.data:
                return jsonify(status="error", error=_("Post already announced"))
            deleteannouncement()
        except SiteMetadata.DoesNotExist:
            pass

        try:
            post = SubPost.get(SubPost.pid == form.post.data)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=_("Post does not exist"))

        SiteMetadata.create(key="announcement", value=post.pid)

        misc.create_sitelog(
            misc.LOG_TYPE_ANNOUNCEMENT,
            uid=current_user.uid,
            link=url_for("sub.view_post", sub=post.sid.name, pid=post.pid),
        )

        cache.delete_memoized(misc.getAnnouncementPid)
        cache.delete_memoized(misc.getAnnouncement)
        socketio.emit(
            "announcement",
            {
                "cont": engine.get_template("shared/announcement.html").render(
                    {"ann": misc.getAnnouncement()}
                )
            },
            namespace="/snt",
        )
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/ban_domain/<domain_type>", methods=["POST"])
def ban_domain(domain_type):
    """Add domain to ban list"""
    if not current_user.is_admin():
        abort(403)
    if domain_type == "email":
        key = "banned_email_domain"
        action = misc.LOG_TYPE_EMAIL_DOMAIN_BAN
    elif domain_type == "link":
        key = "banned_domain"
        action = misc.LOG_TYPE_DOMAIN_BAN
    else:
        return abort(404)

    form = BanDomainForm()

    if form.validate():
        try:
            SiteMetadata.get(
                (SiteMetadata.key == key) & (SiteMetadata.value == form.domain.data)
            )
            return jsonify(status="error", error=[_("Domain is already banned")])
        except SiteMetadata.DoesNotExist:
            SiteMetadata.create(key=key, value=form.domain.data)
            misc.create_sitelog(action, current_user.uid, comment=form.domain.data)
            return jsonify(status="ok")

    return jsonify(status="error", error=get_errors(form))


@do.route("/do/remove_banned_domain/<domain_type>/<domain>", methods=["POST"])
def remove_banned_domain(domain_type, domain):
    """Remove domain if ban list"""
    if not current_user.is_admin():
        abort(403)
    if domain_type == "email":
        key = "banned_email_domain"
        action = misc.LOG_TYPE_EMAIL_DOMAIN_UNBAN
    elif domain_type == "link":
        key = "banned_domain"
        action = misc.LOG_TYPE_DOMAIN_UNBAN
    else:
        return abort(404)

    try:
        sm = SiteMetadata.get(
            (SiteMetadata.key == key) & (SiteMetadata.value == domain)
        )
        sm.delete_instance()
    except SiteMetadata.DoesNotExist:
        return jsonify(status="error", error=_("Domain is not banned"))

    misc.create_sitelog(action, current_user.uid, comment=domain)

    return json.dumps({"status": "ok"})


@do.route("/do/admin/enable_posting", methods=["POST"])
@login_required
def enable_posting():
    """Emergency Mode: disable posting"""
    if not current_user.is_admin():
        abort(404)

    form = LiteralBooleanForm()
    if not form.validate():
        abort(400)
    state = form.value.data

    config.update_value("site.enable_posting", state)
    misc.create_sitelog(
        misc.LOG_TYPE_ADMIN_CONFIG_CHANGE,
        current_user.uid,
        comment=f"site.enable_posting/{state}",
    )

    return redirect(url_for("admin.index"))


@do.route("/do/admin/enable_sendemails", methods=["POST"])
@login_required
def enable_sendemails():
    """Emergency Mode: disable posting"""
    if not current_user.is_admin():
        abort(404)

    form = LiteralBooleanForm()
    if not form.validate():
        abort(400)
    state = form.value.data

    config.update_value("site.send_email", state)
    misc.create_sitelog(
        misc.LOG_TYPE_ADMIN_CONFIG_CHANGE,
        current_user.uid,
        comment=f"site.send_email/{state}",
    )

    return redirect(url_for("admin.index"))


@do.route("/do/admin/enable_registration", methods=["POST"])
@login_required
def enable_registration():
    """Isolation Mode: disable registration"""
    if not current_user.is_admin():
        abort(404)

    form = LiteralBooleanForm()
    if not form.validate():
        abort(400)
    state = form.value.data

    config.update_value("site.enable_registration", state)
    misc.create_sitelog(
        misc.LOG_TYPE_ADMIN_CONFIG_CHANGE,
        current_user.uid,
        comment=f"site.enable_registration/{state}",
    )

    return redirect(url_for("admin.index"))


@do.route("/do/admin/require_captchas", methods=["POST"])
@login_required
def enable_captchas():
    """Enable or disable the captcha solving requirement."""
    if not current_user.is_admin():
        abort(404)

    form = LiteralBooleanForm()
    if not form.validate():
        abort(400)
    state = form.value.data

    config.update_value("site.require_captchas", state)
    misc.create_sitelog(
        misc.LOG_TYPE_ADMIN_CONFIG_CHANGE,
        current_user.uid,
        comment=f"site.require_captchas/{state}",
    )
    return redirect(url_for("admin.index"))


@do.route("/do/save_post/<pid>", methods=["POST"])
def save_post(pid):
    """Save a post to your Saved Posts"""
    try:
        SubPost.get(SubPost.pid == pid)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=[_("Post does not exist")])
    try:
        UserSaved.get((UserSaved.uid == current_user.uid) & (UserSaved.pid == pid))
        return jsonify(status="error", error=[_("Already saved")])
    except UserSaved.DoesNotExist:
        UserSaved.create(uid=current_user.uid, pid=pid)
        return jsonify(status="ok")


@do.route("/do/remove_saved_post/<pid>", methods=["POST"])
def remove_saved_post(pid):
    """Remove a saved post"""
    try:
        SubPost.get(SubPost.pid == pid)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=[_("Post does not exist")])

    try:
        sp = UserSaved.get((UserSaved.uid == current_user.uid) & (UserSaved.pid == pid))
        sp.delete_instance()
        return jsonify(status="ok")
    except UserSaved.DoesNotExist:
        return jsonify(status="error", error=[_("Post was not saved")])


@do.route("/do/save_comment/<cid>", methods=["POST"])
def save_comment(cid):
    """Save a comment to your Saved Comments"""
    try:
        SubPostComment.get(SubPostComment.cid == cid)
    except SubPostComment.DoesNotExist:
        return jsonify(status="error", error=[_("Comment does not exist")])
    try:
        UserSaved.get((UserSaved.uid == current_user.uid) & (UserSaved.cid == cid))
        return jsonify(status="error", error=[_("Already saved")])
    except UserSaved.DoesNotExist:
        UserSaved.create(uid=current_user.uid, cid=cid)
        return jsonify(status="ok")


@do.route("/do/remove_saved_comment/<cid>", methods=["POST"])
def remove_saved_comment(cid):
    """Remove a saved comment"""
    try:
        SubPostComment.get(SubPostComment.cid == cid)
    except SubPostComment.DoesNotExist:
        return jsonify(status="error", error=[_("Comment does not exist")])

    try:
        sp = UserSaved.get((UserSaved.uid == current_user.uid) & (UserSaved.cid == cid))
        sp.delete_instance()
        return jsonify(status="ok")
    except UserSaved.DoesNotExist:
        return jsonify(status="error", error=[_("Comment was not saved")])


@do.route("/do/useinvitecode", methods=["POST"])
def use_invite_code():
    """Enable invite code to register"""
    if not current_user.is_admin():
        abort(404)

    form = UseInviteCodeForm()

    old_values = {
        key: config.get_value(key)
        for key in [
            "site.require_invite_code",
            "site.invitations_visible_to_users",
            "site.invite_level",
            "site.invite_max",
        ]
    }
    if form.validate():
        config.update_value("site.require_invite_code", form.enableinvitecode.data)
        config.update_value(
            "site.invitations_visible_to_users", form.invitations_visible_to_users.data
        )
        config.update_value("site.invite_level", form.minlevel.data)
        config.update_value("site.invite_max", form.maxcodes.data)

    for key, old_value in old_values.items():
        if old_value != config.get_value(key):
            misc.create_sitelog(
                misc.LOG_TYPE_ADMIN_CONFIG_CHANGE,
                current_user.uid,
                comment=f"{key}/{config.get_value(key)}",
            )

    return jsonify(status="ok")


@do.route("/do/create_invite", methods=["POST"])
@login_required
def invite_codes():
    if not config.site.require_invite_code:
        return redirect("/settings")

    if not CsrfTokenOnlyForm().validate():
        abort(400)

    created = InviteCode.select().where(InviteCode.user == current_user.uid).count()
    maxcodes = int(misc.getMaxCodes(current_user.uid))
    if (maxcodes - created) <= 0:
        return redirect("/settings/invite")

    code = "".join(
        random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(32)
    )
    InviteCode.create(user=current_user.uid, code=code, expires=None, max_uses=1)
    return redirect("/settings/invite")


@do.route("/do/stick/<int:post>", methods=["POST"])
def toggle_sticky(post):
    """Toggles post stickyness"""
    try:
        post = SubPost.get(SubPost.pid == post)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=_("Post does not exist"))

    if not current_user.is_mod(post.sid_id):
        abort(403)

    form = DeletePost()

    if form.validate():
        try:
            is_sticky = SubMetadata.get(
                (SubMetadata.sid == post.sid_id)
                & (SubMetadata.key == "sticky")
                & (SubMetadata.value == post.pid)
            )
            is_sticky.delete_instance()
            misc.create_sublog(
                misc.LOG_TYPE_SUB_STICKY_DEL,
                current_user.uid,
                post.sid,
                link=url_for("sub.view_post", sub=post.sid.name, pid=post.pid),
            )
        except SubMetadata.DoesNotExist:
            stickies = SubMetadata.select().where(
                (SubMetadata.sid == post.sid_id) & (SubMetadata.key == "sticky")
            )
            if stickies.count() >= 3:
                return jsonify(
                    status="error", error=_("This sub already has three sticky posts")
                )
            SubMetadata.create(sid=post.sid_id, key="sticky", value=post.pid)
            misc.create_sublog(
                misc.LOG_TYPE_SUB_STICKY_ADD,
                current_user.uid,
                post.sid,
                link=url_for("sub.view_post", sub=post.sid.name, pid=post.pid),
            )

        cache.delete_memoized(misc.getStickyPid, post.sid_id)
        cache.delete_memoized(misc.getStickiesMemoized, post.sid_id)
    return jsonify(status="ok")


@do.route("/do/stick_comment/<comment>", methods=["POST"])
def set_sticky_comment(comment):
    """Set or unset comment stickyness."""
    try:
        comment = (
            SubPostComment.select().join(SubPost).where(SubPostComment.cid == comment)
        )[0]
    except IndexError:
        return jsonify(status="error", error=_("Comment does not exist"))

    is_mod = current_user.is_mod(comment.pid.sid_id)
    if (not is_mod and not current_user.admin) or current_user.uid != comment.uid_id:
        abort(403)

    form = DeletePost()

    if form.validate():
        try:
            sticky = SubPostMetadata.get(
                (SubPostMetadata.pid == comment.pid)
                & (SubPostMetadata.key == "sticky_cid")
            )
            if sticky.value == comment.cid:
                sticky.delete_instance()
                comment.distinguish = 0
            else:
                sticky.value = comment.cid
                sticky.save()
                comment.distinguish = 1 if is_mod else 2
        except SubPostMetadata.DoesNotExist:
            SubPostMetadata.create(pid=comment.pid, key="sticky_cid", value=comment.cid)
            comment.distinguish = 1 if is_mod else 2
        comment.save()

    return jsonify(status="ok")


@do.route("/do/sticky_sort/<int:post>", methods=["POST"])
def toggle_sort(post):
    """Toggles comment sort for a post."""
    try:
        post = SubPost.get(SubPost.pid == post)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=_("Post does not exist"))

    if not current_user.is_mod(post.sid_id):
        abort(403)

    form = DeletePost()

    if form.validate():
        try:
            smd = (
                SubPostMetadata.select()
                .where(
                    (SubPostMetadata.key == "sort") & (SubPostMetadata.pid == post.pid)
                )
                .get()
            )
            smd.value = "best" if smd.value == "new" else "new"
            if (
                smd.value == "best"
                and post.posted < misc.get_best_comment_sort_init_date()
            ):
                smd.value = "top"
            smd.save()
        except SubPostMetadata.DoesNotExist:
            smd = SubPostMetadata.create(pid=post.pid, key="sort", value="new")

        misc.create_sublog(
            misc.LOG_TYPE_STICKY_SORT_NEW
            if smd.value == "new"
            else misc.LOG_TYPE_STICKY_SORT_TOP_OR_BEST,
            current_user.uid,
            post.sid,
            link=url_for("sub.view_post", sub=post.sid.name, pid=post.pid),
        )
        return jsonify(
            status="ok",
            redirect=url_for(
                "sub.view_post", sub=post.sid.name, pid=post.pid, sort=smd.value
            ),
        )
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/flair/<sub>/delete_user", methods=["POST"])
@login_required
def delete_user_flair(sub):
    """Removes a flair (from edit flair page)"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    form = DeleteSubFlair()
    if form.validate():
        try:
            flair = SubUserFlairChoice.get(
                (SubUserFlairChoice.sid == sub.sid)
                & (SubUserFlairChoice.id == form.flair.data)
            )
        except SubFlair.DoesNotExist:
            return jsonify(status="error", error=[_("Flair does not exist")])

        flair.delete_instance()
        cache.delete_memoized(misc.get_sub_flair_choices, sub.sid)
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/flair/<sub>/create_user", methods=["POST"])
@login_required
def create_user_flair(sub):
    """Creates a new flair (from edit flair page)"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        abort(404)

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    form = CreateSubFlair()
    if (
        SubUserFlairChoice.select().where(SubUserFlairChoice.sub == sub.sid).count()
        > 100
    ):
        return jsonify(
            status="error", error=[_("Can't have more than 100 flair presets per sub")]
        )

    if form.validate():
        SubUserFlairChoice.create(sid=sub.sid, flair=form.text.data)
        cache.delete_memoized(misc.get_sub_flair_choices, sub.sid)
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/flair/<sub>/delete", methods=["POST"])
@login_required
def delete_flair(sub):
    """Removes a flair (from edit flair page)"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    form = DeleteSubFlair()
    if form.validate():
        try:
            flair = SubFlair.get(
                (SubFlair.sid == sub.sid) & (SubFlair.xid == form.flair.data)
            )
        except SubFlair.DoesNotExist:
            return jsonify(status="error", error=[_("Flair does not exist")])

        flair.delete_instance()
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/flair/<sub>/create", methods=["POST"])
@login_required
def create_flair(sub):
    """Creates a new flair (from edit flair page)"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        abort(404)

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    if SubFlair.select().where(SubFlair.sid == sub.sid).count() > 100:
        return jsonify(
            status="error", error=[_("Can't have more than 100 flair presets per sub")]
        )

    form = CreateSubFlair()
    if (
        SubFlair.select()
        .where(SubFlair.sid == sub.sid, SubFlair.text == form.text.data)
        .exists()
    ):
        return jsonify(
            status="error", error=[_("Flair with the same text already exists")]
        )

    if form.validate():
        SubFlair.create(
            sid=sub.sid,
            text=form.text.data,
            text_color=form.text_color.data,
            bg_color=form.bg_color.data,
            border_color=form.border_color.data,
        )
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/rule/<sub>/delete", methods=["POST"])
@login_required
def delete_rule(sub):
    """Removes a rule (from edit rule page)"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return jsonify(status="error", error=[_("Sub does not exist")])

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    form = DeleteSubRule()
    if form.validate():
        try:
            rule = SubRule.get(
                (SubRule.sid == sub.sid) & (SubRule.rid == form.rule.data)
            )
        except SubRule.DoesNotExist:
            return jsonify(status="error", error=[_("Rule does not exist")])
        rule.delete_instance()
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/rule/<sub>/create", methods=["POST"])
@login_required
def create_rule(sub):
    """Creates a new rule (from edit rule page)"""
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        abort(404)

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    form = CreateSubRule()
    if form.validate():
        allowed_rules = re.compile("^[a-zA-ZÁ-ž0-9.,_ -/'?!:;()~]+$")
        if not allowed_rules.match(form.text.data):
            return jsonify(status="error", error=[_("Rule has invalid characters")])

        SubRule.create(sid=sub.sid, text=form.text.data)
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


def send_password_recovery_email(user):
    rekey = str(uuid.uuid4())
    rconn.setex("recovery-" + rekey, value=user.uid, time=20 * 60)
    if user.email:
        send_email(
            user.email,
            _("Set a new password on %(site)s", site=config.site.name),
            text_content=engine.get_template("user/email/password-recovery.txt").render(
                dict(user=user, token=rekey)
            ),
            html_content=engine.get_template(
                "user/email/password-recovery.html"
            ).render(dict(user=user, token=rekey)),
        )


def uid_from_recovery_token(token):
    value = rconn.get("recovery-" + token)
    return None if value is None else value.decode("utf-8")


def delete_recovery_token(token):
    rconn.delete("recovery-" + token)


@do.route("/do/reset", methods=["POST"])
@gevent_required  # Contacts Keycloak if configured.
@ratelimit(AUTH_LIMIT)
def reset():
    """Password reset. Takes key and uid and changes password"""
    if current_user.is_authenticated:
        abort(403)

    form = forms.PasswordResetForm()
    if form.validate():
        try:
            user = User.get(User.uid == form.user.data)
        except User.DoesNotExist:
            return jsonify(status="error", error=_("Password recovery link expired"))

        if user.status != UserStatus.OK:
            return jsonify(status="error", error=_("Password recovery link expired"))

        # User exists, check if their key is still valid.
        if form.user.data == uid_from_recovery_token(form.key.data):
            delete_recovery_token(form.key.data)
        else:
            return jsonify(status="error", error=_("Password recovery link expired"))

        # All good. Set da password.
        try:
            auth_provider.reset_password(user, form.password.data)
        except AuthError:
            return jsonify(
                status="error",
                error=_("Password change failed. Please try again later."),
            )
        login_user(misc.load_user(user.uid), remember=False)
        session["remember_me"] = False
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/edit_comment", methods=["POST"])
@login_required
def edit_comment():
    """Edits a comment"""
    form = forms.EditCommentForm()
    if form.validate():
        try:
            comment = SubPostComment.get(SubPostComment.cid == form.cid.data)
        except SubPostComment.DoesNotExist:
            return jsonify(status="error", error=[_("Comment does not exist")])

        if comment.uid_id != current_user.uid:
            return jsonify(status="error", error=[_("Not authorized")])

        post = SubPost.get(SubPost.pid == comment.pid)
        sub = Sub.get(Sub.sid == post.sid)

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if current_user.is_subban(sub):
            return jsonify(status="error", error=[_("You are banned on this sub.")])

        if comment.status:
            return jsonify(status="error", error=_("You can't edit a deleted comment"))

        if post.deleted in [1, 2]:
            return jsonify(
                status="error", error=_("You can't edit a comment on a deleted post")
            )

        if misc.is_archived(post):
            return jsonify(status="error", error=_("Post is archived"))

        dt = datetime.datetime.utcnow()
        SubPostCommentHistory.create(
            cid=comment.cid,
            content=comment.content,
            datetime=(comment.lastedit or comment.time),
        )
        comment.content = form.text.data
        comment.lastedit = dt
        comment.save()
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)[0]})


@do.route("/do/delete_comment", methods=["POST"])
@login_required
def delete_comment():
    """deletes a comment"""
    form = forms.DeleteCommentForm()

    if form.validate():
        try:
            comment = SubPostComment.get(SubPostComment.cid == form.cid.data)
        except SubPostComment.DoesNotExist:
            return jsonify(status="error", error=_("Comment does not exist"))

        if comment.status:
            return jsonify(status="error", error=_("Comment is already deleted"))

        post = (
            SubPost.select(SubPost.pid, SubPost.title, Sub.sid, Sub.name)
            .join(Sub)
            .where(SubPost.pid == comment.pid)
            .get()
        )
        sid = post.sid.get_id()
        sub_name = post.sid.name
        sub = Sub.get(Sub.sid == post.sid.get_id())

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if comment.uid_id != current_user.uid and not (
            current_user.is_admin() or current_user.is_mod(sid)
        ):
            return jsonify(status="error", error=_("Not authorized"))

        postlink, sublink = misc.post_and_sub_markdown_links(post)
        commentlink = misc.comment_markdown_link(sub_name, post.pid, comment.cid)

        if comment.uid_id != current_user.uid and (
            current_user.is_admin() or current_user.is_mod(sid)
        ):
            as_admin = not current_user.is_mod(sid)
            target_language = User.get_by_id(pk=comment.uid_id).language
            if target_language == "sk":
                locale_language = "sk_SK"
            elif target_language == "cs":
                locale_language = "cs_CZ"
            elif target_language == "en":
                locale_language = "en_US"
            elif target_language == "es":
                locale_language = "es_ES"
            elif target_language == "ru":
                locale_language = "ru_RU"
            else:
                locale_language = (
                    "en_US"  # Default language if no target language found
                )

            with force_locale(locale_language):
                if as_admin:
                    comment.status = 3
                    content = _(
                        "The site administrators deleted %(commentlink)s you made on the post %(postlink)s. "
                        "Reason: %(reason)s",
                        commentlink=commentlink,
                        postlink=postlink,
                        reason=form.reason.data,
                    )
                else:
                    comment.status = 2
                    content = _(
                        "The moderators of %(sublink)s deleted %(commentlink)s you made on the post %(postlink)s. "
                        "Reason: %(reason)s",
                        sublink=sublink,
                        commentlink=commentlink,
                        postlink=postlink,
                        reason=form.reason.data,
                    )

                misc.create_notification_message(
                    mfrom=current_user.uid,
                    as_admin=as_admin,
                    sub=sid,
                    to=comment.uid.get_id(),
                    subject=_("Moderation action: comment deleted"),
                    content=content,
                )
            misc.create_sublog(
                misc.LOG_TYPE_SUB_DELETE_COMMENT,
                current_user.uid,
                sid,
                comment=form.reason.data,
                link=url_for(
                    "sub.view_perm",
                    sub=sub_name,
                    pid=post.pid,
                    cid=comment.cid,
                    slug="_",
                ),
                admin=True
                if (not current_user.is_mod(sid) and current_user.is_admin())
                else False,
                target=comment.uid,
            )
            related_reports = SubPostCommentReport.select().where(
                SubPostCommentReport.cid == comment.cid
            )
            for rel_report in related_reports:
                misc.create_reportlog(
                    misc.LOG_TYPE_REPORT_COMMENT_DELETED,
                    current_user.uid,
                    rel_report.id,
                    log_type="comment",
                    desc=form.reason.data,
                )
        else:
            comment.status = 1
        if config.site.recent_activity.live and sub.private == 0:
            # time limited to prevent socket spam
            if (
                (
                    datetime.datetime.utcnow() - comment.time.replace(tzinfo=None)
                ).total_seconds()
            ) < 86400:
                socketio.emit(
                    "comment-deletion",
                    {
                        "cid": comment.cid,
                        "comment_url": url_for(
                            "sub.view_perm", sub=sub.name, cid=comment.cid, pid=post.pid
                        )
                        + "#comment-"
                        + str(comment.cid),
                    },
                    namespace="/snt",
                    # room=post.pid,
                )
        comment.save()
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/undelete_comment", methods=["POST"])
@login_required
def undelete_comment():
    """un-deletes a comment"""
    form = forms.UndeleteCommentForm()
    if form.validate():
        try:
            comment = SubPostComment.get(SubPostComment.cid == form.cid.data)
        except SubPostComment.DoesNotExist:
            return jsonify(status="error", error=_("Comment does not exist"))

        post = (
            SubPost.select(SubPost.pid, SubPost.title, Sub.sid, Sub.name)
            .join(Sub)
            .where(SubPost.pid == comment.pid)
            .get()
        )
        sid = post.sid.get_id()
        sub_name = post.sid.name
        sub = Sub.get(Sub.sid == post.sid.get_id())

        if sub.status != 0 and not current_user.is_admin():
            return jsonify(status="error", error=[_("Sub is disabled")])

        if not comment.status:
            return jsonify(status="error", error=_("Comment is not deleted"))

        if comment.status == 1:
            return jsonify(
                status="error", error=_("Can not un-delete a self-deleted comment")
            )

        if not (
            current_user.is_admin()
            or (comment.status == 2 and current_user.is_mod(sid))
        ):
            return jsonify(status="error", error=_("Not authorized"))

        postlink, sublink = misc.post_and_sub_markdown_links(post)
        as_admin = not current_user.is_mod(sid)
        target_language = User.get_by_id(pk=comment.uid_id).language
        if target_language == "sk":
            locale_language = "sk_SK"
        elif target_language == "cs":
            locale_language = "cs_CZ"
        elif target_language == "en":
            locale_language = "en_US"
        elif target_language == "es":
            locale_language = "es_ES"
        elif target_language == "ru":
            locale_language = "ru_RU"
        else:
            locale_language = "en_US"  # Default language if no target language found

        with force_locale(locale_language):
            if as_admin:
                content = _(
                    "The site administrators restored a comment you made on the post %(postlink)s in %(sublink)s. "
                    "Reason: %(reason)s",
                    sublink=sublink,
                    postlink=postlink,
                    reason=form.reason.data,
                )
            else:
                content = _(
                    "The moderators of %(sublink)s restored a comment you made on the post %(postlink)s. "
                    "Reason: %(reason)s",
                    sublink=sublink,
                    postlink=postlink,
                    reason=form.reason.data,
                )

            misc.create_notification_message(
                mfrom=current_user.uid,
                as_admin=as_admin,
                sub=sid,
                to=comment.uid.get_id(),
                subject=_("Moderation action: comment restored"),
                content=content,
            )
        misc.create_sublog(
            misc.LOG_TYPE_SUB_UNDELETE_COMMENT,
            current_user.uid,
            sid,
            comment=form.reason.data,
            link=url_for(
                "sub.view_perm",
                sub=sub_name,
                pid=comment.pid.get_id(),
                cid=comment.cid,
                slug="_",
            ),
            admin=True
            if (not current_user.is_mod(sid) and current_user.is_admin())
            else False,
            target=comment.uid,
        )
        related_reports = SubPostCommentReport.select().where(
            SubPostCommentReport.cid == comment.cid
        )
        for report in related_reports:
            misc.create_reportlog(
                misc.LOG_TYPE_REPORT_COMMENT_UNDELETED,
                current_user.uid,
                report.id,
                log_type="comment",
                desc=form.reason.data,
            )
        comment.status = 0
        comment.save()

        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/vote/<pid>/<value>", methods=["POST"])
def upvote(pid, value):
    """Logs an upvote to a post."""
    form = CsrfTokenOnlyForm()
    if not form.validate():
        return json.dumps({"status": "error", "error": get_errors(form)}), 400
    if not current_user.is_authenticated:
        return jsonify(msg=_("Not authenticated")), 403

    return misc.cast_vote(current_user.uid, "post", pid, value)


@do.route("/do/votecomment/<cid>/<value>", methods=["POST"])
def upvotecomment(cid, value):
    """Logs an upvote to a post."""
    form = CsrfTokenOnlyForm()
    if not form.validate():
        return json.dumps({"status": "error", "error": get_errors(form)})

    if not current_user.is_authenticated:
        return jsonify(msg=_("Not authenticated")), 403

    return misc.cast_vote(current_user.uid, "comment", cid, value)


@do.route("/do/get_children/<int:pid>/<cid>/<lim>", methods=["post"])
@do.route("/do/get_children/<int:pid>/<cid>", methods=["post"], defaults={"lim": ""})
def get_sibling(pid, cid, lim):
    """Gets children comments for <cid>"""
    try:
        post = misc.getSinglePost(pid)
    except SubPost.DoesNotExist:
        return jsonify(status="ok", posts=[])

    sub = Sub.get(Sub.sid == post["sid"])
    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    default_sort = "best" if post["best_sort_enabled"] else "new"
    sort = request.args.get("sort", default=default_sort, type=str)

    if cid == "null":
        cid = "0"
    if cid != "0":
        try:
            root = SubPostComment.get(SubPostComment.cid == cid)
            if root.pid_id != post["pid"]:
                return jsonify(status="ok", posts=[])
        except SubPostComment.DoesNotExist:
            return jsonify(status="ok", posts=[])

    postmeta = misc.metadata_to_dict(
        SubPostMetadata.select().where(SubPostMetadata.pid == pid)
    )
    comments = misc.get_comment_query(pid, sort=sort, filter_shadowbanned=True)

    if not comments.count():
        return engine.get_template("sub/postcomments.html").render(
            {
                "post": post,
                "postmeta": postmeta,
                "commentscore_delay": sub.commentscore_delay,
                "comments": [],
                "subInfo": {},
                "highlight": "",
                "sort": sort,
            }
        )

    include_history = current_user.is_mod(post["sid"], 1) or current_user.is_admin()

    if lim:
        comment_tree = misc.get_comment_tree(
            pid,
            post["sid"],
            comments,
            cid if cid != "0" else None,
            lim,
            provide_context=False,
            uid=current_user.uid,
            include_history=include_history,
            postmeta=postmeta,
            filter_shadowbanned=True,
        )
    elif cid != "0":
        comment_tree = misc.get_comment_tree(
            pid,
            post["sid"],
            comments,
            cid,
            provide_context=False,
            uid=current_user.uid,
            include_history=include_history,
            postmeta=postmeta,
            filter_shadowbanned=True,
        )
    else:
        return engine.get_template("sub/postcomments.html").render(
            {
                "post": post,
                "postmeta": postmeta,
                "commentscore_delay": sub.commentscore_delay,
                "comments": [],
                "subInfo": {},
                "highlight": "",
                "sort": sort,
            }
        )

    if len(comment_tree) > 0 and cid != "0":
        comment_tree = comment_tree[0].get("children", [])
    subInfo = misc.getSubData(post["sid"])
    subMods = misc.getSubMods(post["sid"])

    return engine.get_template("sub/postcomments.html").render(
        {
            "post": post,
            "postmeta": postmeta,
            "commentscore_delay": sub.commentscore_delay,
            "comments": comment_tree,
            "subInfo": subInfo,
            "subMods": subMods,
            "highlight": "",
            "sort": sort,
        }
    )


@do.route("/do/preview", methods=["POST"])
@login_required
def preview():
    """Returns parsed markdown. Used for post and comment previews."""
    form = CsrfTokenOnlyForm()
    if form.validate():
        if request.json.get("text"):
            return jsonify(
                status="ok", text=misc.our_markdown(request.json.get("text"))
            )
        else:
            return jsonify(status="error", error=_("Missing text"))
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/nsfw", methods=["POST"])
@login_required
def toggle_nsfw():
    """Toggles NSFW tag on posts"""
    form = DeletePost()

    if form.validate():
        try:
            post = SubPost.get(SubPost.pid == form.post.data)
        except SubPost.DoesNotExist:
            return json.dumps({"status": "error", "error": _("Post does not exist")})

        if (
            current_user.uid == post.uid_id
            or current_user.is_admin()
            or current_user.is_mod(post.sid)
        ):
            post.nsfw = 1 if post.nsfw == 0 else 0
            post.save()
            return json.dumps({"status": "ok"})
        else:
            return json.dumps({"status": "error", "error": _("Not authorized")})
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/noreplies", methods=["POST"])
@login_required
def toggle_noreplies():
    """Toggles notifications on posts"""
    form = NoReplyPostForm()

    if form.validate():
        try:
            post = SubPost.get(SubPost.pid == form.post.data)
        except SubPost.DoesNotExist:
            return json.dumps({"status": "error", "error": _("Post does not exist")})

        if current_user.uid == post.uid_id:
            post.noreplies = 1 if post.noreplies is None or post.noreplies == 0 else 0
            post.save()
            return json.dumps({"status": "ok"})
        else:
            return json.dumps({"status": "error", "error": _("Not authorized")})
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/noreplies_comment", methods=["POST"])
@login_required
def toggle_noreplies_comment():
    """Toggles notifications on comment"""
    form = NoReplyCommentForm()

    if form.validate():
        try:
            comment = SubPostComment.get(SubPostComment.cid == form.cid.data)
        except SubPostComment.DoesNotExist:
            return jsonify(status="error", error=[_("Comment does not exist")])

        if comment.uid_id != current_user.uid:
            return jsonify(status="error", error=[_("Not authorized")])

        else:
            comment.noreplies = (
                1 if comment.noreplies is None or comment.noreplies == 0 else 0
            )
            comment.save()
            return json.dumps({"status": "ok"})

    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/lock_comment", methods=["POST"])
@login_required
def lock_comment():
    """Toggles lock on a comment. Used for sticky comments."""
    form = LockCommentForm()

    if form.validate():
        try:
            comment = SubPostComment.get(SubPostComment.cid == form.cid.data)
        except SubPostComment.DoesNotExist:
            return jsonify(status="error", error=[_("Comment does not exist")])

        post = (
            SubPost.select(SubPost.pid, SubPost.title, Sub.sid, Sub.name)
            .join(Sub)
            .where(SubPost.pid == comment.pid)
            .get()
        )
        sid = post.sid.get_id()

        if comment.uid_id != current_user.uid and not (
            current_user.is_admin() or current_user.is_mod(sid)
        ):
            return jsonify(status="error", error=_("Not authorized"))

        else:
            comment.locked = 1 if comment.locked is None or comment.locked == 0 else 0
            comment.save()
            return json.dumps({"status": "ok"})

    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/toggle_ignore/<uid>", methods=["POST"])
@login_required
def ignore_user(uid):
    form = CsrfTokenOnlyForm()
    if not form.validate():
        return json.dumps({"status": "error", "error": get_errors(form)})

    try:
        user = (
            User.select(UserMetadata.value)
            .join(
                UserMetadata,
                JOIN.LEFT_OUTER,
                on=((UserMetadata.uid == User.uid) & (UserMetadata.key == "admin")),
            )
            .where(User.uid == uid)
            .dicts()
            .get()
        )
    except User.DoesNotExist:
        return jsonify(status="error", error=_("User not found"))

    if user["value"] == "1":
        return jsonify(status="error", error=_("Site administrators cannot be blocked"))

    try:
        umb = UserMessageBlock.get(
            (UserMessageBlock.uid == current_user.uid)
            & (UserMessageBlock.target == uid)
        )
        umb.delete_instance()
    except UserMessageBlock.DoesNotExist:
        UserMessageBlock.create(uid=current_user.uid, target=uid)

    return jsonify(status="ok")


@do.route("/do/edit_ignore/<uid>", methods=["POST"])
@login_required
def edit_ignore(uid):
    if current_user.can_admin:
        abort(404)

    try:
        user = (
            User.select(User.uid, UserMetadata.value)
            .join(
                UserMetadata,
                JOIN.LEFT_OUTER,
                on=((UserMetadata.uid == User.uid) & (UserMetadata.key == "admin")),
            )
            .where(User.uid == uid)
            .dicts()
            .get()
        )
    except User.DoesNotExist:
        return jsonify(status="error", error=_("User not found"))
    if user["value"] == "1":
        return jsonify(status="error", error=_("Site administrators cannot be blocked"))

    form = EditIgnoreForm()
    if form.validate():
        try:
            umb = UserMessageBlock.get(
                (UserMessageBlock.uid == current_user.uid)
                & (UserMessageBlock.target == uid)
            )
            if form.view_messages.data == "show":
                umb.delete_instance()
        except UserMessageBlock.DoesNotExist:
            if form.view_messages.data == "hide":
                UserMessageBlock.create(uid=current_user.uid, target=uid)

        method = {
            "hide": UserContentBlockMethod.HIDE,
            "blur": UserContentBlockMethod.BLUR,
            "show": None,
        }[form.view_content.data]
        try:
            ucb = UserContentBlock.get(
                (UserContentBlock.uid == current_user.uid)
                & (UserContentBlock.target == uid)
            )
            if form.view_content.data == "show":
                ucb.delete_instance()
            else:
                ucb.method = method
                ucb.save()
        except UserContentBlock.DoesNotExist:
            if method is not None:
                UserContentBlock.create(uid=current_user.uid, target=uid, method=method)
        return jsonify(status="ok")
    return jsonify(status="error", error=get_errors(form))


@do.route("/do/upload/<sub>", methods=["POST"])
@gevent_required  # Launches an async task (thumbnail creation).
@login_required
def sub_upload(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        abort(404)

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    c = SubStylesheet.get(SubStylesheet.sid == sub.sid)
    form = EditSubCSSForm(css=c.source)

    subInfo = misc.getSubData(sub.sid)
    subMods = misc.getSubMods(sub.sid)

    # get remaining space
    remaining = 1024 * 1024 * config.storage.sub_css_max_file_size  # 2M
    ufiles = SubUploads.select().where(SubUploads.sid == sub.sid)
    for uf in ufiles:
        remaining -= uf.size

    fname = request.form.get("name")
    if len(fname) > 10:
        return engine.get_template("sub/css.html").render(
            {
                "sub": sub,
                "form": form,
                "storage": int(remaining - (1024 * 1024)),
                "max_storage": config.storage.sub_css_max_file_size,
                "error": _("File name too long."),
                "files": ufiles,
                "subInfo": subInfo,
                "subMods": subMods,
            }
        )

    if len(fname) < 3:
        return engine.get_template("sub/css.html").render(
            {
                "sub": sub,
                "form": form,
                "storage": int(remaining - (1024 * 1024)),
                "max_storage": config.storage.sub_css_max_file_size,
                "error": _("File name too short or missing."),
                "files": ufiles,
                "subInfo": subInfo,
                "subMods": subMods,
            }
        )

    if not allowedNames.match(fname):
        return engine.get_template("sub/css.html").render(
            {
                "sub": sub,
                "form": form,
                "storage": int(remaining - (1024 * 1024)),
                "max_storage": config.storage.sub_css_max_file_size,
                "error": _("Invalid file name."),
                "files": ufiles,
                "subInfo": subInfo,
                "subMods": subMods,
            }
        )

    # Check for duplicate filenames in the same sub
    try:
        SubUploads.select().where(
            (SubUploads.sid == sub.sid) & (fn.Lower(SubUploads.name) == fname.lower())
        ).get()
        return engine.get_template("sub/css.html").render(
            {
                "sub": sub,
                "form": form,
                "storage": int(remaining - (1024 * 1024)),
                "max_storage": config.storage.sub_css_max_file_size,
                "error": _(
                    "A file with this name already exists. Please choose a different name."
                ),
                "files": ufiles,
                "subInfo": subInfo,
                "subMods": subMods,
            }
        )
    except SubUploads.DoesNotExist:
        # No duplicate found, continue with upload
        pass

    ufile = request.files.getlist("files")[0]
    if ufile.filename == "":
        return engine.get_template("sub/css.html").render(
            {
                "sub": sub,
                "form": form,
                "storage": int(remaining - (1024 * 1024)),
                "max_storage": config.storage.sub_css_max_file_size,
                "error": _("Please select a file to upload."),
                "files": ufiles,
                "subInfo": subInfo,
                "subMods": subMods,
            }
        )

    mtype = storage.mtype_from_file(ufile, allow_video_formats=False)
    if mtype is None:
        return engine.get_template("sub/css.html").render(
            {
                "sub": sub,
                "form": form,
                "storage": int(remaining - (1024 * 1024)),
                "max_storage": config.storage.sub_css_max_file_size,
                "error": _("Invalid file type. Only jpg, png and gif allowed."),
                "files": ufiles,
                "subInfo": subInfo,
                "subMods": subMods,
            }
        )

    try:
        fhash = storage.calculate_file_hash(ufile, size_limit=remaining)
    except storage.SizeLimitExceededError:
        return engine.get_template("sub/css.html").render(
            {
                "sub": sub,
                "form": form,
                "storage": int(remaining - (1024 * 1024)),
                "max_storage": config.storage.sub_css_max_file_size,
                "error": _("Not enough available space to upload file."),
                "files": ufiles,
                "subInfo": subInfo,
                "subMods": subMods,
            }
        )

    basename = str(uuid.uuid5(storage.FILE_NAMESPACE, fhash))
    f_name = storage.store_file(ufile, basename, mtype, remove_metadata=True)
    fsize = storage.get_stored_file_size(f_name)

    sub_upload = SubUploads.create(
        sid=sub.sid, fileid=f_name, thumbnail="deferred", size=fsize, name=fname
    )
    create_thumbnail(f_name, [(SubUploads, "id", sub_upload.id)])
    misc.create_sublog(misc.LOG_TYPE_SUB_CSS_CHANGE, current_user.uid, sub.sid)
    return redirect(url_for("sub.edit_sub_css", sub=sub.name))


@do.route("/do/upload/<sub>/delete/<name>", methods=["POST"])
@login_required
def sub_upload_delete(sub, name):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        jsonify(status="error")  # descriptive errors where?

    if sub.status != 0 and not current_user.is_admin():
        return jsonify(status="error", error=[_("Sub is disabled")])

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return redirect(url_for("sub.edit_sub_css", sub=sub.name))
    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        return jsonify(status="error")

    try:
        img = SubUploads.get((SubUploads.sid == sub.sid) & (SubUploads.name == name))
    except SubUploads.DoesNotExist:
        return jsonify(status="error")
    fileid = img.fileid
    img.delete_instance()
    misc.create_sublog(misc.LOG_TYPE_SUB_CSS_CHANGE, current_user.uid, sub.sid)

    # We won't delete the pic if somebody else is still using it..
    try:
        UserUploads.get(UserUploads.fileid == fileid)
    except UserUploads.DoesNotExist:
        try:
            SubUploads.get(SubUploads.fileid == img.fileid)
        except SubUploads.DoesNotExist:
            # TODO thumbnail does not get deleted
            storage.remove_file(img.fileid)

    return jsonify(status="ok")


@do.route("/do/set_sub_icon/<sub>", methods=["POST"])
@login_required
def set_sub_icon(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        abort(404)
    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        abort(403)

    selected_icon = request.form.get("sub_icon")

    # Handle all icon options
    if not selected_icon:  # "None" option selected
        # Delete the record entirely to represent no icon preference
        SubMetadata.delete().where(
            (SubMetadata.sid == sub.sid) & (SubMetadata.key == "icon")
        ).execute()
    else:  # "Default" or a custom icon selected
        # For custom icons, validate they exist
        if selected_icon != "__default__":
            existing_file = (
                SubUploads.select()
                .where((SubUploads.sid == sub.sid) & (SubUploads.name == selected_icon))
                .first()
            )
            if not existing_file:
                return jsonify(status="error", error=_("Invalid file selected."))

        # Update or create metadata entry
        query = (
            SubMetadata.select()
            .where((SubMetadata.sid == sub.sid) & (SubMetadata.key == "icon"))
            .first()
        )
        if query:
            query.value = selected_icon
            query.save()
        else:
            SubMetadata.create(sid=sub.sid, key="icon", value=selected_icon)

    return redirect(url_for("sub.edit_sub_css", sub=sub.name))


@do.route("/do/admin/create_question", methods=["POST"])
@login_required
def create_question():
    if not current_user.is_admin():
        abort(403)

    form = SecurityQuestionForm()

    if form.validate():
        SiteMetadata.create(
            key="secquestion", value=form.question.data + "|" + form.answer.data
        )
        return jsonify(status="ok")
    return jsonify(status="error")


@do.route("/do/admin/delete_question/<xid>", methods=["POST"])
@login_required
def delete_question(xid):
    if not current_user.is_admin():
        abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return jsonify(status="error")
    try:
        th = SiteMetadata.get(
            (SiteMetadata.key == "secquestion") & (SiteMetadata.xid == xid)
        )
    except SiteMetadata.DoesNotExist:
        return jsonify(status="error")
    th.delete_instance()
    return jsonify(status="ok")


@do.route("/do/admin/ban_user/<username>", methods=["POST"])
@login_required
def ban_user(username):
    if not current_user.is_admin():
        return abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return abort(403)

    try:
        user = User.get(fn.Lower(User.name) == username.lower())
    except User.DoesNotExist:
        return abort(404)

    if user.uid == current_user.uid:
        return abort(403)

    auth_provider.change_user_status(user, 5)
    misc.create_sitelog(misc.LOG_TYPE_USER_BAN, uid=current_user.uid, comment=user.name)

    related_post_reports = (
        SubPostReport.select().join(SubPost).where(SubPost.uid == user.uid)
    )
    related_comment_reports = (
        SubPostCommentReport.select()
        .join(SubPostComment)
        .where(SubPostComment.uid == user.uid)
    )
    for report in related_post_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_BANNED,
            current_user.uid,
            report.id,
            log_type="post",
        )
    for report in related_comment_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_BANNED,
            current_user.uid,
            report.id,
            log_type="comment",
        )

    return redirect(url_for("user.view", user=user.name))


@do.route("/do/admin/shadowban_user/<username>", methods=["POST"])
@login_required
def shadowban_user(username):
    if not current_user.is_admin():
        return abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return abort(403)

    try:
        user = User.get(fn.Lower(User.name) == username.lower())
    except User.DoesNotExist:
        return abort(404)

    if user.uid == current_user.uid:
        return abort(403)

    auth_provider.change_user_status(user, 6)
    misc.create_sitelog(
        misc.LOG_TYPE_USER_SHADOWBAN, uid=current_user.uid, comment=user.name
    )

    related_post_reports = (
        SubPostReport.select().join(SubPost).where(SubPost.uid == user.uid)
    )
    related_comment_reports = (
        SubPostCommentReport.select()
        .join(SubPostComment)
        .where(SubPostComment.uid == user.uid)
    )
    for report in related_post_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_SHADOWBANNED,
            current_user.uid,
            report.id,
            log_type="post",
        )
    for report in related_comment_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_SHADOWBANNED,
            current_user.uid,
            report.id,
            log_type="comment",
        )

    return redirect(url_for("user.view", user=user.name))


@do.route("/do/admin/unban_user/<username>", methods=["POST"])
@login_required
def unban_user(username):
    if not current_user.is_admin():
        return abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return abort(403)

    try:
        user = User.get(fn.Lower(User.name) == username.lower())
    except User.DoesNotExist:
        return abort(404)

    if user.status != 5:
        return jsonify(status="error", error=[_("User is not banned")])

    auth_provider.change_user_status(user, 0)
    misc.create_sitelog(
        misc.LOG_TYPE_USER_UNBAN, uid=current_user.uid, comment=user.name
    )

    related_post_reports = (
        SubPostReport.select().join(SubPost).where(SubPost.uid == user.uid)
    )
    related_comment_reports = (
        SubPostCommentReport.select()
        .join(SubPostComment)
        .where(SubPostComment.uid == user.uid)
    )
    for report in related_post_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_UNBANNED,
            current_user.uid,
            report.id,
            log_type="post",
        )
    for report in related_comment_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_UNBANNED,
            current_user.uid,
            report.id,
            log_type="comment",
        )

    return redirect(url_for("user.view", user=user.name))


@do.route("/do/admin/unshadowban_user/<username>", methods=["POST"])
@login_required
def unshadowban_user(username):
    if not current_user.is_admin():
        return abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return abort(403)

    try:
        user = User.get(fn.Lower(User.name) == username.lower())
    except User.DoesNotExist:
        return abort(404)

    if user.status != 6:
        return jsonify(status="error", error=[_("User is not shadowbanned")])

    auth_provider.change_user_status(user, 0)
    misc.create_sitelog(
        misc.LOG_TYPE_USER_UNSHADOWBAN, uid=current_user.uid, comment=user.name
    )

    related_post_reports = (
        SubPostReport.select().join(SubPost).where(SubPost.uid == user.uid)
    )
    related_comment_reports = (
        SubPostCommentReport.select()
        .join(SubPostComment)
        .where(SubPostComment.uid == user.uid)
    )
    for report in related_post_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_UNSHADOWBANNED,
            current_user.uid,
            report.id,
            log_type="post",
        )
    for report in related_comment_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_USER_SITE_UNSHADOWBANNED,
            current_user.uid,
            report.id,
            log_type="comment",
        )

    return redirect(url_for("user.view", user=user.name))


@do.route("/do/admin/set_random_pwd/<user>", methods=["POST"])
@login_required
def set_random_pwd(user):
    if not current_user.is_admin():
        return abort(403)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return abort(403)

    try:
        user = User.get(fn.Lower(User.name) == user.lower())
    except User.DoesNotExist:
        return abort(404)

    if user.uid == current_user.uid:
        return abort(403)

    password = "".join(
        random.choice(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()-_=+"
        )
        for _ in range(12)
    )

    try:
        auth_provider.reset_password(user, password)
    except AuthError:
        return jsonify(
            status="error",
            error=_("Password change failed. Please try again later."),
        )

    return jsonify(status="ok", password=password)


@do.route("/do/edit_top_bar", methods=["POST"])
@login_required
def edit_top_bar():
    form = CsrfTokenOnlyForm()
    if not form.validate():
        return jsonify(status="error", error=[_("no CSRF")])

    data = request.get_json()
    if not data.get("sids"):
        return jsonify(status="error")

    for i in data.get("sids"):
        # Check if we're being fed good UUIDs
        try:
            uuid.UUID(i, version=4)
        except ValueError:
            return jsonify(status="error")

    # If all the sids are good, we do the thing.
    i = 0
    for k in data.get("sids"):
        i += 1
        try:
            SubSubscriber.update(order=i).where(
                (SubSubscriber.uid == current_user.uid) & (SubSubscriber.sid == k)
            ).execute()
        except SubSubscriber.DoesNotExist:
            pass  # TODO: Add these as status=4 SubSubscriber (after implementing some way to delete those)

    return jsonify(status="ok")


@do.route("/do/admin/undo_votes/<uid>", methods=["POST"])
@login_required
def admin_undo_votes(uid):
    if not current_user.admin:
        abort(403)

    try:
        user = User.get(User.uid == uid)
    except User.DoesNotExist:
        return abort(404)

    form = CsrfTokenOnlyForm()
    if not form.validate():
        return redirect(url_for("user.view", user=user.name))

    post_v = SubPostVote.select().where(SubPostVote.uid == user.uid)
    comm_v = SubPostCommentVote.select().where(SubPostCommentVote.uid == user.uid)
    score_deltas = defaultdict(int)
    delta_given = 0

    for v in post_v:
        try:
            post = (
                SubPost.select(SubPost.pid, SubPost.uid)
                .where(SubPost.pid == v.pid_id)
                .get()
            )
        except SubPost.DoesNotExist:
            # Edge case. An orphan vote.
            v.delete_instance()
            continue
        # Not removing self-votes
        if post.uid_id == user.uid:
            continue
        adjustment = 1 if v.positive else -1
        score_deltas[post.uid_id] -= adjustment
        delta_given -= adjustment
        kwargs = dict(score=SubPost.score - adjustment)
        if v.positive:
            kwargs.update(upvotes=SubPost.upvotes - 1)
        else:
            kwargs.update(downvotes=SubPost.downvotes - 1)
        SubPost.update(**kwargs).where(SubPost.pid == v.pid_id).execute()
        v.delete_instance()
    for v in comm_v:
        try:
            comm = (
                SubPostComment.select(
                    SubPostComment.cid,
                    SubPostComment.score,
                    SubPostComment.uid,
                    SubPostComment.upvotes,
                    SubPostComment.views,
                )
                .where(SubPostComment.cid == v.cid)
                .get()
            )
        except SubPostComment.DoesNotExist:
            # Edge case. An orphan vote.
            v.delete_instance()
            continue
        adjustment = 1 if v.positive else -1
        kwargs = {}
        score_deltas[comm.uid_id] -= adjustment
        delta_given -= adjustment
        if comm.score is not None:
            kwargs.update(score=SubPostComment.score - adjustment)
        if v.positive:
            kwargs.update(upvotes=SubPostComment.upvotes - 1)
        else:
            kwargs.update(downvotes=SubPostComment.downvotes - 1)
        SubPostComment.update(**kwargs).where(SubPostComment.cid == v.cid).execute()
        v.delete_instance()
    for recipient_uid, delta in score_deltas.items():
        User.update(score=User.score + delta).where(User.uid == recipient_uid).execute()
    User.update(given=User.given + delta_given).where(User.uid == uid).execute()
    return redirect(url_for("user.view", user=user.name))


@do.route("/do/cast_vote/<pid>/<oid>", methods=["POST"])
@login_required
def cast_vote(pid, oid):
    form = CsrfTokenOnlyForm()
    if form.validate():
        try:
            post = misc.getSinglePost(pid)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=_("Post does not exist"))

        if post["ptype"] != 3:
            return jsonify(status="error", error=_("Post is not a poll"))

        try:
            SubPostPollOption.get(
                (SubPostPollOption.id == oid) & (SubPostPollOption.pid == pid)
            )
        except SubPostPollOption.DoesNotExist:
            return jsonify(status="error", error=_("Poll option does not exist"))

        # Check if user hasn't voted already.
        try:
            SubPostPollVote.get(
                (SubPostPollVote.uid == current_user.uid) & (SubPostPollVote.pid == pid)
            )
            return jsonify(status="error", error=_("Already voted"))
        except SubPostPollVote.DoesNotExist:
            pass

        # Check if poll is still open...
        try:
            SubPostMetadata.get(
                (SubPostMetadata.pid == pid) & (SubPostMetadata.key == "poll_closed")
            )
            return jsonify(status="error", error=_("Poll is closed"))
        except SubPostMetadata.DoesNotExist:
            pass

        try:
            ca = SubPostMetadata.get(
                (SubPostMetadata.pid == pid)
                & (SubPostMetadata.key == "poll_closes_time")
            )
            if int(ca.value) < time.time():
                return jsonify(status="error", error=_("Poll is closed"))
        except SubPostMetadata.DoesNotExist:
            pass

        try:
            ca = SubPostMetadata.get(
                (SubPostMetadata.pid == pid)
                & (SubPostMetadata.key == "poll_vote_after_level")
            )
            if current_user.get_user_level()[0] < int(ca.value):
                return jsonify(status="error", error=_("Insufficient user level"))
        except SubPostMetadata.DoesNotExist:
            pass

        # Everything OK. Issue vote.
        SubPostPollVote.create(uid=current_user.uid, pid=pid, vid=oid)
    return jsonify(status="ok")


@do.route("/do/remove_vote/<pid>", methods=["POST"])
@login_required
def remove_vote(pid):
    form = CsrfTokenOnlyForm()
    if form.validate():
        try:
            post = misc.getSinglePost(pid)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=_("Post does not exist"))

        if post["ptype"] != 3:
            return jsonify(status="error", error=_("Post is not a poll"))

        # Check if poll is still open...
        try:
            SubPostMetadata.get(
                (SubPostMetadata.pid == pid) & (SubPostMetadata.key == "poll_closed")
            )
            return jsonify(status="error", error=_("Poll is closed"))
        except SubPostMetadata.DoesNotExist:
            pass

        try:
            ca = SubPostMetadata.get(
                (SubPostMetadata.pid == pid)
                & (SubPostMetadata.key == "poll_closes_time")
            )
            if int(ca.value) < time.time():
                return jsonify(status="error", error=_("Poll is closed"))
        except SubPostMetadata.DoesNotExist:
            pass

        # Check if user hasn't voted already.
        try:
            vote = SubPostPollVote.get(
                (SubPostPollVote.uid == current_user.uid) & (SubPostPollVote.pid == pid)
            )
            vote.delete_instance()
        except SubPostPollVote.DoesNotExist:
            pass
    return jsonify(status="ok")


@do.route("/do/mark_viewed", methods=["POST"])
@login_required
def mark_comments_viewed():
    """Mark comments as seen by the user."""
    form = ViewCommentsForm()
    if form.validate():
        cids = json.loads(form.cids.data)
        comments = (
            SubPostComment.select(
                SubPostComment.cid,
                SubPostComment.pid,
                SubPostComment.upvotes,
                SubPostComment.downvotes,
                SubPostComment.views,
                SubPost.posted,
                SubPost.sid,
            )
            .join(SubPost)
            .switch(SubPostComment)
            .join(User)
            .join(
                SubPostCommentView,
                JOIN.LEFT_OUTER,
                on=(
                    (SubPostCommentView.cid == SubPostComment.cid)
                    & (SubPostCommentView.uid == current_user.uid)
                ),
            )
            .where(
                SubPostCommentView.id.is_null(True)
                & (SubPostComment.cid << cids)
                & (SubPostComment.uid != current_user.uid)
            )
        ).dicts()

        comments = list(comments)
        if comments and not misc.is_archived(comments[0]):
            for comment in comments:
                SubPostComment.update(views=SubPostComment.views + 1).where(
                    SubPostComment.cid == comment["cid"]
                ).execute()

            view_records = [
                {"uid": current_user.uid, "cid": comment["cid"], "pid": comment["pid"]}
                for comment in comments
            ]
            SubPostCommentView.insert_many(view_records).execute()

    return jsonify(status="ok")


@do.route("/do/close_poll", methods=["POST"])
@login_required
def close_poll():
    """Closes a poll."""
    form = DeletePost()

    if form.validate():
        try:
            post = SubPost.get(SubPost.pid == form.post.data)
        except SubPost.DoesNotExist:
            return json.dumps({"status": "error", "error": _("Post does not exist")})

        if post.ptype != 3:
            abort(404)

        if (
            current_user.uid == post.uid_id
            or current_user.is_admin()
            or current_user.is_mod(post.sid)
        ):
            # Check if poll's not closed already
            postmeta = misc.metadata_to_dict(
                SubPostMetadata.select().where(SubPostMetadata.pid == post.pid)
            )
            if "poll_closed" in postmeta:
                return json.dumps(
                    {"status": "error", "error": _("Poll already closed.")}
                )

            if "poll_closes_time" in postmeta:
                if int(postmeta["poll_closes_time"]) < time.time():
                    return json.dumps(
                        {"status": "error", "error": _("Poll already closed.")}
                    )

            SubPostMetadata.create(pid=post.pid, key="poll_closed", value="1")
            return json.dumps({"status": "ok"})
        else:
            abort(403)
    return json.dumps({"status": "error", "error": get_errors(form)})


try:
    # noinspection PyUnresolvedReferences
    import callbacks

    callbacks_enabled = True
except ModuleNotFoundError:
    callbacks_enabled = False


@do.route("/do/report", methods=["POST"])
@login_required
@ratelimit(POSTING_LIMIT)
def report():
    form = DeletePost()
    if form.validate():
        try:
            post = misc.getSinglePost(form.post.data)
        except SubPost.DoesNotExist:
            return jsonify(status="error", error=_("Post does not exist"))

        if post["deleted"] != 0:
            return jsonify(status="error", error=_("Post was removed"))

        # check if user already reported the post
        try:
            SubPostReport.get(
                (SubPostReport.pid == post["pid"])
                & (SubPostReport.uid == current_user.uid)
            )
            return jsonify(
                status="error", error=_("You have already reported this post")
            )
        except SubPostReport.DoesNotExist:
            pass

        if len(form.reason.data) < 2:
            return jsonify(status="error", error=_("Report reason too short."))
        elif len(form.reason.data) > 128:
            return jsonify(status="error", error=_("Report reason too long."))

        if not form.send_to_admin.data and misc.is_sub_banned(
            post["sid"], uid=current_user.uid
        ):
            return jsonify(status="error", error=_("You are banned from this sub."))

        # do the reporting.
        SubPostReport.create(
            pid=post["pid"],
            uid=current_user.uid,
            reason=form.reason.data,
            send_to_admin=form.send_to_admin.data,
        )
        misc.notify_mods(post["sid"])
        if callbacks_enabled:
            # callbacks!
            cb = getattr(callbacks, "ON_POST_REPORT", False)
            if cb:
                cb(post, current_user, form.reason.data, form.send_to_admin.data)
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/report/comment", methods=["POST"])
@login_required
@ratelimit(POSTING_LIMIT)
def report_comment():
    form = DeletePost()
    if form.validate():
        try:
            comm = SubPostComment.select(
                SubPostComment.cid,
                SubPostComment.content,
                SubPostComment.lastedit,
                SubPostComment.score,
                SubPostComment.status,
                SubPostComment.time,
                SubPostComment.pid,
                SubPost.sid,
                User.name.alias("username"),
                SubPostComment.uid,
                User.status.alias("userstatus"),
                SubPostComment.upvotes,
                SubPostComment.downvotes,
            )
            comm = comm.join(User, on=(User.uid == SubPostComment.uid)).switch(
                SubPostComment
            )
            comm = comm.join(SubPost)
            comm = comm.where(SubPostComment.cid == form.post.data)
            comm = comm.get()
        except SubPostComment.DoesNotExist:
            return jsonify(status="error", error=_("Comment does not exist"))

        if comm.status:
            return jsonify(status="error", error=_("Comment was removed"))

        # check if user already reported the comment
        try:
            SubPostCommentReport.get(
                (SubPostCommentReport.cid == comm.cid)
                & (SubPostCommentReport.uid == current_user.uid)
            )
            return jsonify(
                status="error", error=_("You have already reported this comment")
            )
        except SubPostCommentReport.DoesNotExist:
            pass

        if len(form.reason.data) < 2:
            return jsonify(status="error", error=_("Report reason too short."))
        elif len(form.reason.data) > 128:
            return jsonify(status="error", error=_("Report reason too long."))

        if not form.send_to_admin.data and misc.is_sub_banned(
            comm.pid.sid, uid=current_user.uid
        ):
            return jsonify(status="error", error=_("You are banned from this sub."))

        # do the reporting.
        SubPostCommentReport.create(
            cid=comm.cid,
            uid=current_user.uid,
            reason=form.reason.data,
            send_to_admin=form.send_to_admin.data,
        )
        misc.notify_mods(comm.pid.sid.sid)
        # callbacks!
        if callbacks_enabled:
            cb = getattr(callbacks, "ON_COMMENT_REPORT", False)
            if cb:
                cb(comm, current_user, form.reason.data, form.send_to_admin.data)
        return jsonify(status="ok")
    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/do/report/close_post_report/<pid>/<action>", methods=["POST"])
@login_required
# id is the pid of the post, and action is STR either "close" or "reopen"
def close_post_report(pid, action):
    # ensure user is mod or admin and report, post, and sub exist
    try:
        report = SubPostReport.get(SubPostReport.id == pid)
    except SubPostReport.DoesNotExist:
        return jsonify(status="error", error=_("Report does not exist"))

    try:
        post = SubPost.get(SubPost.pid == report.pid)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=_("Post does not exist"))

    try:
        sub = Sub.get(Sub.sid == post.sid)
    except Sub.DoesNotExist:
        return jsonify(status="error", error=_("Sub does not exist"))

    if action != "close" and action != "reopen":
        return jsonify(status="error", error=[_("Invalid action")])

    if not current_user.is_mod(sub.sid) and not current_user.is_admin():
        return jsonify(status="error", error=[_("Not authorized")])

    if action == "close" and not report.open:
        return jsonify(status="error", error=_("This report has already been closed"))

    elif action == "reopen" and report.open:
        return jsonify(status="error", error=_("This report is already open"))

    # change the report status
    if action == "close":
        SubPostReport.update(open=False).where(SubPostReport.id == pid).execute()
    elif action == "reopen":
        SubPostReport.update(open=True).where(SubPostReport.id == pid).execute()

    misc.notify_mods(sub.sid)

    # check if it changed and return status
    updated_report = SubPostReport.select().where(SubPostReport.id == pid).get()
    if action == "close" and not updated_report.open:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_CLOSE, current_user.uid, pid, log_type="post"
        )
        return jsonify(status="ok")
    elif action == "reopen" and updated_report.open:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_REOPEN, current_user.uid, pid, log_type="post"
        )
        return jsonify(status="ok")
    else:
        return jsonify(status="error", error=_("Failed to update report"))


@do.route("/do/report/close_comment_report/<cid>/<action>", methods=["POST"])
@login_required
# id is the cid of the comment, and action is STR either "close" or "reopen"
def close_comment_report(cid, action):
    # ensure user is mod or admin and report, post, and sub exist
    try:
        report = SubPostCommentReport.get(SubPostCommentReport.id == cid)
    except SubPostCommentReport.DoesNotExist:
        return jsonify(status="error", error=_("Report does not exist"))

    try:
        comment = SubPostComment.get(SubPostComment.cid == report.cid)
    except SubPostCommentReport.DoesNotExist:
        return jsonify(status="error", error=_("Comment does not exist"))

    try:
        post = SubPost.get(SubPost.pid == comment.pid)
    except SubPost.DoesNotExist:
        return jsonify(status="error", error=_("Post does not exist"))

    try:
        sub = Sub.get(Sub.sid == post.sid)
    except Sub.DoesNotExist:
        return jsonify(status="error", error=_("Sub does not exist"))

    if action != "close" and action != "reopen":
        return jsonify(status="error", error=[_("Invalid action")])

    if not current_user.is_mod(sub.sid) and not current_user.is_admin():
        return jsonify(status="error", error=[_("Not authorized")])

    if action == "close" and not report.open:
        return jsonify(status="error", error=_("This report has already been closed"))

    elif action == "reopen" and report.open:
        return jsonify(status="error", error=_("This report is already open"))

    # change the report status
    if action == "close":
        SubPostCommentReport.update(open=False).where(
            SubPostCommentReport.id == cid
        ).execute()
    elif action == "reopen":
        SubPostCommentReport.update(open=True).where(
            SubPostCommentReport.id == cid
        ).execute()
    misc.notify_mods(sub.sid)

    # check if it changed and return status
    updated_report = (
        SubPostCommentReport.select().where(SubPostCommentReport.id == cid).get()
    )
    if action == "close" and not updated_report.open:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_CLOSE, current_user.uid, cid, log_type="comment"
        )
        return jsonify(status="ok")
    elif action == "reopen" and updated_report.open:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_REOPEN, current_user.uid, cid, log_type="comment"
        )
        return jsonify(status="ok")
    else:
        return jsonify(status="error", error=_("Failed to update report"))


@do.route(
    "/do/report/close_post_related_reports/<related_reports>/<original_report>",
    methods=["POST"],
)
@login_required
def close_post_related_reports(related_reports, original_report):
    if not (current_user.is_a_mod or current_user.is_admin()):
        abort(403)

    related_report_ids = json.loads(related_reports)
    OriginalSubPostReport = SubPostReport.alias()

    # Select only the reports that are related and still need closing.
    related_reports = (
        SubPostReport.select(SubPostReport.id, SubPost.sid)
        .join(SubPost)
        .join(
            OriginalSubPostReport,
            JOIN.LEFT_OUTER,
            on=(OriginalSubPostReport.pid == SubPostReport.pid),
        )
        .where(
            SubPostReport.open
            & (SubPostReport.id << related_report_ids)
            & (OriginalSubPostReport.id == original_report)
        )
    )
    # Ensure user is mod or admin.
    if not current_user.is_admin():
        related_reports = related_reports.join(
            SubMod, on=((SubMod.sid == SubPost.sid) & (~SubMod.invite))
        ).where(SubMod.uid == current_user.uid)
    related_reports = list(related_reports.dicts())

    if len(related_reports) == 0:
        return jsonify(status="error", error=_("No related reports to close"))

    SubPostReport.update(open=False).where(
        SubPostReport.id << [rep["id"] for rep in related_reports]
    ).execute()
    misc.notify_mods(related_reports[0]["sid"])

    for rep in related_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_CLOSE_RELATED,
            current_user.uid,
            rep["id"],
            log_type="post",
            related=True,
            original_report=original_report,
        )

    return jsonify(status="ok")


@do.route(
    "/do/report/close_comment_related_reports/<related_reports>/<original_report>",
    methods=["POST"],
)
@login_required
def close_comment_related_reports(related_reports, original_report):
    if not (current_user.is_a_mod or current_user.is_admin()):
        abort(403)

    related_report_ids = json.loads(related_reports)
    OriginalSubPostCommentReport = SubPostCommentReport.alias()

    # Select only the reports that are related and still need closing.
    related_reports = (
        SubPostCommentReport.select(SubPostCommentReport.id, SubPost.sid)
        .join(SubPostComment)
        .join(SubPost)
        .join(
            OriginalSubPostCommentReport,
            JOIN.LEFT_OUTER,
            on=(OriginalSubPostCommentReport.cid == SubPostCommentReport.cid),
        )
        .where(
            SubPostCommentReport.open
            & (SubPostCommentReport.id << related_report_ids)
            & (OriginalSubPostCommentReport.id == original_report)
        )
    )
    # Ensure user is mod or admin.
    if not current_user.is_admin():
        related_reports = related_reports.join(
            SubMod, on=((SubMod.sid == SubPost.sid) & (~SubMod.invite))
        ).where(SubMod.uid == current_user.uid)
    related_reports = list(related_reports.dicts())

    if len(related_reports) == 0:
        return jsonify(status="error", error=_("No related reports to close"))

    SubPostCommentReport.update(open=False).where(
        SubPostCommentReport.id << [rep["id"] for rep in related_reports]
    ).execute()
    misc.notify_mods(related_reports[0]["sid"])

    for rep in related_reports:
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_CLOSE_RELATED,
            current_user.uid,
            rep["id"],
            log_type="comment",
            related=True,
            original_report=original_report,
        )
    return jsonify(status="ok")


@do.route("/do/create_report_note/<report_type>/<report_id>", methods=["POST"])
@login_required
def create_report_note(report_type, report_id):
    """Creates a new note on a report"""
    if report_type == "post":
        try:
            report = SubPostReport.get(SubPostReport.id == report_id)
            sub = (
                Sub.select()
                .join(SubPost)
                .join(SubPostReport)
                .where(SubPostReport.id == report_id)
                .get()
            )
        except SubPostReport.DoesNotExist:
            return abort(404)
    else:
        try:
            report = SubPostCommentReport.get(SubPostCommentReport.id == report_id)
            sub = (
                Sub.select()
                .join(SubPost)
                .join(SubPostComment)
                .join(SubPostCommentReport)
                .where(SubPostCommentReport.id == report_id)
                .get()
            )
        except SubPostCommentReport.DoesNotExist:
            return abort(404)

    if not current_user.is_mod(sub.sid, 1) and not current_user.is_admin():
        return abort(403)

    form = CreateReportNote()
    if form.validate():
        misc.create_reportlog(
            misc.LOG_TYPE_REPORT_NOTE,
            current_user.uid,
            report.id,
            log_type=report_type,
            desc=form.text.data,
        )
        return jsonify(status="ok")

    return json.dumps({"status": "error", "error": get_errors(form)})


@do.route("/suboftheday/delete", methods=["POST"])
@login_required
def delete_suboftheday():
    if not current_user.is_admin():
        return abort(403)
    rconn.delete("daysub")
    misc.cache.delete_memoized(misc.getSubOfTheDay)
    return jsonify(status="ok")


@do.route("/suboftheday/set", methods=["POST"])
@login_required
def set_suboftheday():
    if not current_user.is_admin():
        return abort(403)
    form = SetSubOfTheDayForm()
    if not form.validate():
        return json.dumps({"status": "error", "error": get_errors(form)})

    try:
        sub = Sub.get(Sub.name == form.sub.data)
        if sub.status in (1, 2):  # Check if status is 1 or 2
            return jsonify(status="error", error=[_("Sub is banned or quarantined")])
    except Sub.DoesNotExist:
        return jsonify(status="error", error=_("Sub does not exist"))
    rconn.delete("daysub")
    misc.set_sub_of_the_day(sub.sid)
    misc.cache.delete_memoized(misc.getSubOfTheDay)
    return jsonify(status="ok")


@do.route("/do/admin/config/set", methods=["POST"])
@login_required
def admin_modify_config_setting():
    if not current_user.is_admin():
        return abort(403)

    form = ChangeConfigSettingForm()
    if not form.validate():
        return json.dumps({"status": "error", "error": get_errors(form)})
    setting_info = next(
        (c for c in config.get_mutable_items() if c["name"] == form.setting.data)
    )

    if setting_info["type"] == "bool":
        value = form.value.data == "True"
    else:
        value = form.value.data

    try:
        config.update_value(form.setting.data, value)
    except ValueError:
        return json.dumps({"status": "error", "error": [_("Error: not a number")]})

    misc.create_sitelog(
        misc.LOG_TYPE_ADMIN_CONFIG_CHANGE,
        current_user.uid,
        comment=f"{form.setting.data}/{config.get_value(form.setting.data)}",
    )

    return jsonify(status="ok")


@do.route("/do/add_default/<sub>", methods=["POST"])
@login_required
def add_default(sub):
    if not current_user.is_admin():
        abort(403)

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    try:
        SiteMetadata.get(
            (SiteMetadata.key == "default") & (SiteMetadata.value == sub.sid)
        )
        return "Error: Sub is already a default!"
    except SiteMetadata.DoesNotExist:
        SiteMetadata.create(key="default", value=sub.sid)
        misc.create_sublog(
            misc.LOG_TYPE_DEFAULT_SUB, uid=current_user.uid, sid=sub.sid, admin=True
        )
        return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))


@do.route("/do/remove_default/<sub>", methods=["POST"])
@login_required
def remove_default(sub):
    if not current_user.is_admin():
        abort(403)

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    try:
        metadata = SiteMetadata.get(
            (SiteMetadata.key == "default") & (SiteMetadata.value == sub.sid)
        )
        metadata.delete_instance()
        misc.create_sublog(
            misc.LOG_TYPE_UNDEFAULT_SUB, uid=current_user.uid, sid=sub.sid, admin=True
        )
        return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))
    except SiteMetadata.DoesNotExist:
        return "Error: Sub is not a default"


@do.route("/do/ban_sub/<sub>", methods=["POST"])
@login_required
def ban_sub(sub):
    if not current_user.is_admin():
        abort(403)

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    if sub.status == 1:
        return "Error: Sub is already banned!"

    sub.status = 1
    sub.save()
    misc.create_sublog(
        misc.LOG_TYPE_BAN_SUB, uid=current_user.uid, sid=sub.sid, admin=True
    )
    return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))


@do.route("/do/unban_sub/<sub>", methods=["POST"])
@login_required
def unban_sub(sub):
    if not current_user.is_admin():
        abort(403)

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    if sub.status != 1:
        return "Error: Sub is not banned!"

    sub.status = 0
    sub.save()
    misc.create_sublog(
        misc.LOG_TYPE_UNBAN_SUB, uid=current_user.uid, sid=sub.sid, admin=True
    )
    return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))


@do.route("/do/quarantine_sub/<sub>", methods=["POST"])
@login_required
def quarantine_sub(sub):
    if not current_user.is_admin():
        abort(403)

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    if sub.status == 2:
        return "Error: Sub is already quarantined!"

    sub.status = 2
    sub.save()
    misc.create_sublog(
        misc.LOG_TYPE_QUARANTINE_SUB, uid=current_user.uid, sid=sub.sid, admin=True
    )
    return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))


@do.route("/do/unquarantine_sub/<sub>", methods=["POST"])
@login_required
def unquarantine_sub(sub):
    if not current_user.is_admin():
        abort(403)

    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    if sub.status != 2:
        return "Error: Sub is not quarantined!"

    sub.status = 0
    sub.save()
    misc.create_sublog(
        misc.LOG_TYPE_UNQUARANTINE_SUB, uid=current_user.uid, sid=sub.sid, admin=True
    )
    return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))


@do.route("/do/private_sub/<sub>", methods=["POST"])
@login_required
def private_sub(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    if not current_user.is_admin() and not current_user.is_mod(sub.sid, 0):
        abort(403)
    # Ensure the sub is not a default sub
    try:
        SiteMetadata.get(
            (SiteMetadata.key == "default") & (SiteMetadata.value == sub.sid)
        )
        response = jsonify({"error": "Default subs cannot be made private!"})
        print(f"{response.json}")  # Debug log
        return response, 400
    except SiteMetadata.DoesNotExist:
        pass

    if sub.private == 1:
        return "Error: Sub is already private!"

    sub.private = 1
    #  we are automatically setting sub logs to private too, just in case
    sub.update_metadata("sublog_private", True)
    sub.update_metadata("sub_banned_users_private", True)
    sub.save()

    misc.create_sublog(
        misc.LOG_TYPE_PRIVATE_SUB, uid=current_user.uid, sid=sub.sid, admin=False
    )
    return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))


@do.route("/do/unprivate_sub/<sub>", methods=["POST"])
@login_required
def unprivate_sub(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return "Error: Sub does not exist"

    if not current_user.is_admin() and not current_user.is_mod(sub.sid, 0):
        abort(403)

    if sub.private != 1:
        return "Error: Sub is not private!"

    sub.private = 0
    sub.save()
    misc.create_sublog(
        misc.LOG_TYPE_UNPRIVATE_SUB, uid=current_user.uid, sid=sub.sid, admin=False
    )
    return jsonify(status="ok", addr=url_for("sub.view_sub", sub=sub.name))
