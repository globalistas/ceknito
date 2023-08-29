"""Peewee migrations -- 052_contactus_link

Add a admin site configuration option to control whether to enable optional text content
in link posts.

"""

import peewee as pw

SQL = pw.SQL


def migrate(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.create(key="site.contactus_link", value="")


def rollback(migrator, database, fake=False, **kwargs):
    SiteMetadata = migrator.orm["site_metadata"]
    if not fake:
        SiteMetadata.delete().where(SiteMetadata.key == "site.contactus_link").execute()
