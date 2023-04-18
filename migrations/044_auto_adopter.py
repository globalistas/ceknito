"""Peewee migrations -- 044_auto_adopter

Add an admin site configuration option to control whether new users get
the Early Adopter badge automatically.

"""

import peewee as pw

SQL = pw.SQL


def migrate(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.create(key="site.auto_adopter", value="0")


def rollback(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.delete().where(SiteMetadata.key == "site.auto_adopter").execute()
