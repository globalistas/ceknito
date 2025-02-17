""" Here we store badges. """
from .storage import FILE_NAMESPACE, mtype_from_file, calculate_file_hash, store_file
from peewee import JOIN
from .models import Badge, UserMetadata, SubMod
from flask_babel import lazy_gettext as _l
import uuid


class Badges:
    """
    Badge exposes a stable API for dealing with user badges.

    We need to be able to look up a badge by id and name, along with the ability to
    iterate through all of the badges.

    We also want to be able to create badges.

    For backwards compatibility we will allow "fetching" of old_badges but only by ID.

    This will also create an interface for Triggers, as Badges and Triggers are interlinked.
    """

    def __iter__(self):
        """
        Returns a list of all badges in the database.
        """
        badge_query = Badge.select(
            Badge.bid,
            Badge.name,
            Badge.alt,
            Badge.icon,
            Badge.score,
            Badge.trigger,
            Badge.rank,
        ).order_by(Badge.rank, Badge.name)
        return (x for x in badge_query)

    def __getitem__(self, bid):
        """
        Returns a badge from the database.
        """
        try:
            return Badge.get(Badge.bid == bid)
        except Badge.DoesNotExist:
            return None

    def update_badge(self, bid, name, alt, icon, score, rank, trigger):
        """
        Updates the information related to a badge, updates icon if provided.
        """
        if icon:
            icon = gen_icon(icon)
        else:
            icon = self[bid].icon

        Badge.update(
            name=name, alt=alt, icon=icon, score=score, rank=rank, trigger=trigger
        ).where(Badge.bid == bid).execute()

    @staticmethod
    def new_badge(name, alt, icon, score, rank, trigger=None):
        """
        Creates a new badge with an optional trigger.
        """
        icon = gen_icon(icon)
        Badge.create(
            name=name, alt=alt, icon=icon, score=score, rank=rank, trigger=trigger
        )

    @staticmethod
    def delete_badge(bid):
        """
        Deletes a badge by ID
        """
        Badge.delete().where(Badge.bid == bid).execute()
        UserMetadata.delete().where(
            (UserMetadata.key == "badge") & (UserMetadata.value == bid)
        ).execute()

    @staticmethod
    def assign_userbadge(uid, bid):
        """
        Gives a badge to a user
        """
        UserMetadata.get_or_create(key="badge", uid=uid, value=bid)

    @staticmethod
    def unassign_userbadge(uid, bid):
        """
        Removes a badge from a user
        """
        UserMetadata.delete().where(
            (UserMetadata.key == "badge")
            & (UserMetadata.uid == uid)
            & (UserMetadata.value == str(bid))
        ).execute()

    @staticmethod
    def triggers():
        """
        Lists available triggers that can be attached to a badge.
        """
        return triggers.keys()

    @staticmethod
    def badges_for_user(uid):
        """
        Returns a list of badges associated with a user.
        """
        return (
            Badge.select(
                Badge.bid, Badge.name, Badge.icon, Badge.score, Badge.alt, Badge.rank
            )
            .join(
                UserMetadata,
                JOIN.LEFT_OUTER,
                on=(UserMetadata.value.cast("int") == Badge.bid),
            )
            .where((UserMetadata.uid == uid) & (UserMetadata.key == "badge"))
            .order_by(Badge.rank, Badge.name)
        )


def gen_icon(icon):
    mtype = mtype_from_file(icon, allow_video_formats=False)
    if mtype is None:
        raise Exception(_l("Invalid file type. Only jpg, png and gif allowed."))

    fhash = calculate_file_hash(icon)
    basename = str(uuid.uuid5(FILE_NAMESPACE, fhash))
    f_name = store_file(icon, basename, mtype, remove_metadata=True)
    return f_name


badges = Badges()


def admin(bid):
    """
    Auto assigns badges to admins.
    """
    for user in UserMetadata.select().where(
        (UserMetadata.key == "admin") & (UserMetadata.value == "1")
    ):
        print("Giving ", bid, " to:", user.uid)
        badges.assign_userbadge(user.uid, bid)


def mod(bid):
    """
    Auto assigns badges to mods.
    """
    for user in SubMod.select().where((~SubMod.invite)):
        print("Giving ", bid, " to:", user.uid)
        badges.assign_userbadge(user.uid, bid)


def user_registers(uid):
    """
    Assigns the 'Early Adopter' badge to a newly registered user.
    """
    early_adopter_badge = next(
        (b for b in badges if b.trigger == "user registers"), None
    )
    if early_adopter_badge:
        badges.assign_userbadge(uid, early_adopter_badge.bid)


def first_post(uid):
    """
    Assigns the 'First Post' badge to a user when they make their first post.
    """
    first_post_badge = next((b for b in badges if b.trigger == "first post"), None)
    if first_post_badge:
        user_badges = Badges.badges_for_user(uid)
        badge_ids = {badge.bid for badge in user_badges}  # Use a set for efficiency

        if first_post_badge.bid not in badge_ids:
            badges.assign_userbadge(uid, first_post_badge.bid)


triggers = {
    "admin": admin,
    "mod": mod,
    "user registers": user_registers,
    "first post": first_post,
}
