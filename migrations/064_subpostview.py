"""Peewee migrations -- 049_subpostview.py

Add a table to keep track of when each user last looked at each post.
"""

import datetime as dt
import peewee as pw

SQL = pw.SQL


def migrate(migrator, database, fake=False, **kwargs):
    @migrator.create_model
    class SubPostView(pw.Model):
        uid = pw.ForeignKeyField(
            db_column="uid", model=migrator.orm["user"], field="uid"
        )
        pid = pw.ForeignKeyField(
            db_column="pid", model=migrator.orm["sub_post"], field="pid"
        )
        datetime = pw.DateTimeField(null=True, default=dt.datetime.utcnow)

        def __repr__(self):
            return f"<SubPostView{self.cid}>"

        class Meta:
            table_name = "sub_post_view"

    # peewee_migrate's add_index does not work if you set db_column when creating
    # a ForeignKeyField.
    ctx = database.get_sql_context()
    idx = pw.Index(
        "subpostview_pid_uid",
        "sub_post_view",
        [SubPostView.pid, SubPostView.uid],
        unique=True,
    )
    migrator.sql("".join(ctx.sql(idx)._sql))


def rollback(migrator, database, fake=False, **kwargs):
    migrator.remove_model("sub_post_view")
