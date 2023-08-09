"""Peewee migrations -- 053_ann_sub_config_option

Add a admin site configuration option to control where user replies
to admin moderation notifications are sent.

"""

import peewee as pw

SQL = pw.SQL


def migrate(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.create(key="site.ann_sub", value="announcements")


def rollback(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.delete().where(SiteMetadata.key == "site.ann_sub").execute()
