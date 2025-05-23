"""Manages notifications"""

from datetime import datetime, timedelta

from flask_login import current_user
from peewee import JOIN
from flask_babel import _, force_locale
from pyfcm import FCMNotification
from .config import config
from .models import (
    Notification,
    User,
    UserMetadata,
    UserContentBlock,
    Sub,
    SubMod,
    SubPost,
    SubPostComment,
    SubPostCommentVote,
    SubPostCommentView,
    SubPostVote,
    UserSaved,
)
from .socketio import socketio
from .misc import get_notification_count, send_email
from flask import url_for


class Notifications(object):
    def __init__(self):
        self.push_service = None

    def init_app(self, app):
        with app.app_context():
            if config.notifications.fcm_api_key:
                self.push_service = FCMNotification(
                    api_key=config.notifications.fcm_api_key
                )

    @staticmethod
    def get_notifications(uid, page):
        ParentComment = SubPostComment.alias()
        SubModCurrentUser = SubMod.alias()
        notifications = (
            Notification.select(
                Notification.id,
                Notification.type,
                Notification.read,
                Notification.created,
                Sub.sid,
                Sub.name.alias("sub_name"),
                Sub.nsfw.alias("sub_nsfw"),
                Notification.post.alias("pid"),
                Notification.comment.alias("cid"),
                User.name.alias("sender"),
                Notification.sender.alias("senderuid"),
                Notification.content,
                SubPost.title.alias("post_title"),
                SubPost.posted,
                SubPost.nsfw,
                SubPostComment.content.alias("comment_content"),
                SubPostComment.score.alias("comment_score"),
                Sub.commentscore_delay.alias("commentscore_delay"),
                SubPostComment.content.alias("post_comment"),
                SubPostCommentView.id.alias("already_viewed"),
                SubPost.score.alias("post_score"),
                SubPost.link.alias("post_link"),
                ParentComment.content.alias("comment_context"),
                ParentComment.time.alias("comment_context_posted"),
                ParentComment.score.alias("comment_context_score"),
                ParentComment.cid.alias("comment_context_cid"),
                SubPost.content.alias("post_content"),
                UserSaved.cid.alias("comment_is_saved"),
            )
            .join(Sub, JOIN.LEFT_OUTER)
            .switch(Notification)
            .join(SubPost, JOIN.LEFT_OUTER)
            .switch(Notification)
            .join(SubPostComment, JOIN.LEFT_OUTER)
            .join(
                SubPostCommentView,
                JOIN.LEFT_OUTER,
                on=(
                    (SubPostCommentView.cid == SubPostComment.cid)
                    & (SubPostCommentView.uid == uid)
                ),
            )
            .switch(Notification)
            .join(User, JOIN.LEFT_OUTER, on=Notification.sender == User.uid)
            .join(
                UserContentBlock,
                JOIN.LEFT_OUTER,
                on=(
                    (UserContentBlock.uid == uid)
                    & (UserContentBlock.target == User.uid)
                ),
            )
            .join(
                SubMod,
                JOIN.LEFT_OUTER,
                on=(
                    (SubMod.user == User.uid)
                    & (SubMod.sub == Notification.sub)
                    & ~SubMod.invite
                ),
            )
            .join(
                SubModCurrentUser,
                JOIN.LEFT_OUTER,
                on=(
                    (SubModCurrentUser.user == uid)
                    & (SubModCurrentUser.sub == Notification.sub)
                    & ~SubModCurrentUser.invite
                ),
            )
            .join(
                ParentComment,
                JOIN.LEFT_OUTER,
                on=(SubPostComment.parentcid == ParentComment.cid),
            )
            .join(
                UserSaved,
                JOIN.LEFT_OUTER,
                on=(
                    (UserSaved.cid == SubPostComment.cid)
                    & (UserSaved.uid == current_user.uid)
                ),
            )
            .where(
                (Notification.target == uid)
                & (SubPostComment.status.is_null(True))
                & (
                    UserContentBlock.id.is_null(True)
                    | ~(
                        Notification.type
                        << [
                            "POST_REPLY",
                            "COMMENT_REPLY",
                            "POST_MENTION",
                            "COMMENT_MENTION",
                        ]
                    )
                    | SubMod.sid.is_null(False)
                    | SubModCurrentUser.sid.is_null(False)
                )
            )
            .order_by(Notification.created.desc())
            .paginate(page, 50)
            .dicts()
        )
        notifications = list(notifications)
        # Fetch the votes for only the 50 notifications on the page.
        # Joining the vote tables in the query above was causing Postgres
        # to do a lot of extra work for users with many notifications and
        # votes.
        votes = (
            Notification.select(
                Notification.id,
                SubPostCommentVote.positive.alias("comment_positive"),
                SubPostVote.positive.alias("post_positive"),
            )
            .join(SubPost, JOIN.LEFT_OUTER)
            .join(
                SubPostVote,
                JOIN.LEFT_OUTER,
                on=(SubPostVote.uid == uid) & (SubPostVote.pid == SubPost.pid),
            )
            .switch(Notification)
            .join(SubPostComment, JOIN.LEFT_OUTER)
            .join(
                SubPostCommentVote,
                JOIN.LEFT_OUTER,
                on=(
                    (SubPostCommentVote.uid == uid)
                    & (SubPostCommentVote.cid == SubPostComment.cid)
                ),
            )
            .where(Notification.id << [n["id"] for n in notifications])
        ).dicts()
        votes_by_id = {v["id"]: v for v in votes}
        for n in notifications:
            n["comment_positive"] = votes_by_id[n["id"]]["comment_positive"]
            n["post_positive"] = votes_by_id[n["id"]]["post_positive"]
        return notifications

    @staticmethod
    def mark_read(uid, notifs=None):
        if notifs:
            # Help the users who can't be bothered to delete their
            # notifications by removing anything over a month old
            # unless it appears on the first page of notifications.
            Notification.delete().where(
                (Notification.target == uid)
                & (Notification.created < datetime.utcnow() - timedelta(days=30))
                & ~(Notification.id << [n["id"] for n in notifs])
            ).execute()
        Notification.update(read=datetime.utcnow()).where(
            (Notification.read.is_null(True)) & (Notification.target == uid)
        ).execute()

    @staticmethod
    def email_template(notification_type, user, post, sub):
        if notification_type == "POST_REPLY":
            return _(
                'User %(user_name)s <a href="%(url)s">replied</a> to your post '
                "<br><br><i>%(post_title)s</i><br><br>in <i>%(sub_name)s</i>",
                user_name=user.name,
                post_title=post.title,
                sub_name=sub.name,
                url=url_for("messages.view_notifications", _external=True),
            )
        elif notification_type == "COMMENT_REPLY":
            return _(
                'User %(user_name)s <a href="%(url)s">replied</a> to your comment '
                "in the post titled<br><br><i>%(post_title)s</i><br><br>in <i>%(sub_name)s</i>",
                user_name=user.name,
                post_title=post.title,
                sub_name=sub.name,
                url=url_for("messages.view_notifications", _external=True),
            )
        else:
            return None

    def send(
        self,
        notification_type,
        target,
        sender,
        sub=None,
        comment=None,
        post=None,
        content=None,
    ):
        """
        Sends a notification to a user
        @param notification_type: Type of notification. May be one of:
         - POST_REPLY
         - COMMENT_REPLY
         - POST_MENTION
         - COMMENT_MENTION
        @param target: UID of the user receiving the message
        @param sender: UID of the user sending the message or None if it was sent by the system
        @param sub: SID of the sub related to this message
        @param comment: CID of the comment related to this message
        @param post: PID of the post related to this message
        @param content: Text content of the message
        """
        Notification.create(
            type=notification_type,
            target=target,
            sender=sender,
            sub=sub,
            comment=comment,
            post=post,
            content=content,
        )
        ignore = None

        target_email_notify = (
            UserMetadata.select(UserMetadata.value)
            .where((UserMetadata.uid == target) & (UserMetadata.key == "email_notify"))
            .scalar()  # Use scalar() to get the actual value
        ) == "1"

        if target_email_notify and notification_type in ["POST_REPLY", "COMMENT_REPLY"]:
            target_language = User.get_by_id(pk=target).language
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
                    "sk_SK"  # Default language if no target language found
                )

            with force_locale(locale_language):
                email = self.email_template(
                    notification_type,
                    User.get_by_id(pk=sender),
                    SubPost.get_by_id(pk=post),
                    Sub.get_by_id(pk=sub),
                )
                send_email(
                    User.get_by_id(pk=target).email,
                    subject=_("New notification"),
                    text_content="",
                    html_content=email,
                )
        try:
            TargetSubMod = SubMod.alias()
            ignore = (
                UserContentBlock.select()
                .join(
                    SubMod,
                    JOIN.LEFT_OUTER,
                    on=(
                        (SubMod.uid == UserContentBlock.uid)
                        & (SubMod.sub == sub)
                        & ~SubMod.invite
                    ),
                )
                .join(
                    TargetSubMod,
                    JOIN.LEFT_OUTER,
                    on=(
                        (TargetSubMod.uid == UserContentBlock.target)
                        & (TargetSubMod.sub == sub)
                        & ~TargetSubMod.invite
                    ),
                )
                .where(
                    (UserContentBlock.target == sender)
                    & (UserContentBlock.uid == target)
                    & SubMod.uid.is_null()
                    & TargetSubMod.uid.is_null()
                )
            ).get()
        except UserContentBlock.DoesNotExist:
            pass
        if ignore is not None:
            return
        notification_count = get_notification_count(target)
        socketio.emit(
            "notification",
            {"count": notification_count},
            namespace="/snt",
            room="user" + target,
        )
        if self.push_service:
            if sender:
                sender = User.get(User.uid == sender)
            if sub:
                sub = Sub.get(Sub.sid == sub)
            if post:
                post = SubPost.get(SubPost.pid == post)
            # TODO: Set current language to target's lang
            message_body = _(
                "Looks like nobody bothered to code the message for this notification :("
            )
            message_title = _("New notification.")
            if notification_type == "POST_REPLY":
                message_title = _(
                    "Post reply in /%(prefix)s/%(sub)s",
                    prefix=config.site.sub_prefix,
                    sub=sub.name,
                )
                message_body = _(
                    "%(name)s replied to your post titled %(title)s",
                    name=sender.name,
                    title=post.title,
                )
            elif notification_type == "COMMENT_REPLY":
                message_title = _(
                    "Comment reply in /%(prefix)s/%(sub)s",
                    prefix=config.site.sub_prefix,
                    sub=sub.name,
                )
                message_body = _(
                    "%(name)s replied to your comment in the post titled %(title)s",
                    name=sender.name,
                    title=post.title,
                )
            elif notification_type in ("POST_MENTION", "COMMENT_MENTION"):
                message_title = _(
                    "You were mentioned in /%(prefix)s/%(sub)s",
                    prefix=config.site.sub_prefix,
                    sub=sub.name,
                )
                message_body = _(
                    "%(name)s mentioned you in the post titled %(title)s",
                    name=sender.name,
                    title=post.title,
                )
            # TODO: click_action (URL the notification sends you to)
            # - Blocker: Implementing messaging in PWA
            # TODO: actions (mark as read?)
            notification_data = {
                "type": "notification",
                "title": message_title,
                "notificationPayload": {
                    "badge": config.site.icon_url,
                    "body": message_body,
                },
                "notificationCount": notification_count,
            }
            self.push_service.topic_subscribers_data_message(
                topic_name=target, data_message=notification_data
            )


notifications = Notifications()
