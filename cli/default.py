import click
import datetime
from flask.cli import AppGroup
from peewee import fn
from app.models import Sub, SiteMetadata, SubSubscriber, User

default = AppGroup(
    "default",
    help="""Manages default subs

Default subs are shown in the home page to logged out users, and newly registered users will be automatically
subscribed to these subs.

Adding or removing default subs has no impact on existing users, unless you use the --subscribe-all option
to `add`.
""",
)


@default.command(help="Marks a sub as default")
@click.argument("sub")
@click.option(
    "--subscribe-all",
    default=False,
    is_flag=True,
    help="Subscribe all users who don't have the sub blocked",
)
def add(sub, subscribe_all):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return print("Error: Sub does not exist")

    try:
        SiteMetadata.get(
            (SiteMetadata.key == "default") & (SiteMetadata.value == sub.sid)
        )
        print("Sub is already a default!")
    except SiteMetadata.DoesNotExist:
        SiteMetadata.create(key="default", value=sub.sid)
        print("Sub is now default")

    if subscribe_all:
        subq = SubSubscriber.select().where(
            (SubSubscriber.uid == User.uid) & (SubSubscriber.sid == sub.sid)
        )
        users = User.select(User.uid).where(~fn.EXISTS(subq))
        now = datetime.datetime.utcnow()
        subscribes = [
            SubSubscriber(time=now, uid=u.uid, sid=sub.sid, status=1) for u in users
        ]
        SubSubscriber.bulk_create(subscribes, batch_size=100)
        Sub.update(subscribers=Sub.subscribers + len(users)).where(
            Sub.sid == sub.sid
        ).execute()
        print(f"Subscribed {len(users)} users to {sub.name}")


@default.command(help="Removes a default sub")
@click.argument("sub")
def remove(sub):
    try:
        sub = Sub.get(fn.Lower(Sub.name) == sub.lower())
    except Sub.DoesNotExist:
        return print("Error: Sub does not exist")

    try:
        metadata = SiteMetadata.get(
            (SiteMetadata.key == "default") & (SiteMetadata.value == sub.sid)
        )
        metadata.delete_instance()
        print("Done.")
    except SiteMetadata.DoesNotExist:
        print("Error: Sub is not a default")


@default.command(name="list", help="Lists all default subs")
def list_defaults():
    subs = (
        SiteMetadata.select(Sub.name)
        .join(Sub, on=Sub.sid == SiteMetadata.value)
        .where((SiteMetadata.key == "default"))
        .dicts()
    )
    print("Default subs: ")
    for i in subs:
        print("  ", i["name"])
