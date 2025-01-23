"""Peewee migrations -- 059_recents_sidebar_live

Add a admin site configuration option to control whether the scores
are shown with the top posts of the last 24 hours.

"""

import peewee as pw

SQL = pw.SQL


def migrate(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.create(key="site.recent_activity.live", value="0")


def rollback(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.delete().where(
            SiteMetadata.key == "site.recent_activity.live"
        ).execute()
