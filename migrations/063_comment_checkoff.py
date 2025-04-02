"""Peewee migrations -- 045_comment_checkoff.py

Allow mods to check off comments.
"""
import datetime as dt
import peewee as pw

SQL = pw.SQL


def migrate(migrator, database, fake=False, **kwargs):
    SubPost = migrator.orm["sub_post"]
    SubPostComment = migrator.orm["sub_post_comment"]
    SubMod = migrator.orm["sub_mod"]
    SiteMetadata = migrator.orm["site_metadata"]
    SubMetadata = migrator.orm["sub_metadata"]
    User = migrator.orm["user"]
    UserMetadata = migrator.orm["user_metadata"]

    @migrator.create_model
    class SubPostCommentCheckoff(pw.Model):
        """Allow mods to check off comments."""

        cid = pw.ForeignKeyField(
            column_name="cid", model=SubPostComment, field="cid", unique=True
        )
        uid = pw.ForeignKeyField(column_name="uid", model=User, field="uid")
        datetime = pw.DateTimeField(default=dt.datetime.now)

        class Meta:
            table_name = "sub_post_comment_checkoff"

    if not fake:
        # Enter checkoff records for all comments made by mods on non-archived posts.
        SubPostCommentCheckoff.create_table(True)
        try:
            announcement_pid = int(
                SiteMetadata.get(SiteMetadata.key == "announcement").value
            )
        except SiteMetadata.DoesNotExist:
            announcement_pid = None

        archive_post_after = int(
            SiteMetadata.get(SiteMetadata.key == "site.archive_post_after").value
        )
        delta = dt.timedelta(days=archive_post_after)

        mod_comments = (
            SubPostComment.select(
                SubPostComment.cid,
                SubPostComment.uid,
                pw.fn.Coalesce(SubPostComment.lastedit, SubPostComment.time).alias(
                    "datetime"
                ),
            )
            .join(SubPost)
            .join(
                SubMod,
                on=(
                    (SubMod.sid == SubPost.sid)
                    & (SubMod.uid == SubPostComment.uid)
                    & (~SubMod.invite)
                ),
            )
            .join(
                SiteMetadata,
                pw.JOIN.LEFT_OUTER,
                on=(SiteMetadata.key == "site.archive_sticky_posts"),
            )
            .join(
                SubMetadata,
                pw.JOIN.LEFT_OUTER,
                on=(
                    (SubMetadata.sid == SubPost.sid)
                    & (SubMetadata.key == "sticky")
                    & (SubMetadata.value.cast("int") == SubPost.pid)
                ),
            )
            .where(
                (SubPost.posted > dt.datetime.utcnow() - delta)
                | (SubPost.pid == announcement_pid)
                | ((SiteMetadata.value == "0") & SubMetadata.value.is_null(False))
            )
        ).dicts()

        # Insert rows 100 at a time.
        for batch in pw.chunked(mod_comments, 100):
            SubPostCommentCheckoff.insert_many(batch).execute()


def rollback(migrator, database, fake=False, **kwargs):
    migrator.remove_model("sub_post_comment_checkoff")
