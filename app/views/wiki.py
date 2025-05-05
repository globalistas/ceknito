"""Pages to be migrated to a wiki-like system"""

from flask import Blueprint, abort, redirect, url_for
from flask_babel import lazy_gettext as _l
from flask_login import login_required
from ..misc import engine
from ..models import Wiki

bp = Blueprint("wiki", __name__)


@bp.route("/welcome")
@login_required
def welcome():
    """Welcome page for new users"""
    try:
        Wiki.select().where(Wiki.slug == "welcome").where(Wiki.is_global).get()
        return redirect(url_for("wiki.view", slug="welcome"))
    except Wiki.DoesNotExist:
        return engine.get_template("welcome.html").render({})


try:
    th_license = open("LICENSE").read()
except FileNotFoundError:
    th_license = _l("License file was deleted :(")


@bp.route("/license")
def show_license():
    """View API help page"""
    return engine.get_template("site/license.html").render({"license": th_license})


@bp.route("/wiki/<slug>")
def view(slug):
    try:
        page = Wiki.select().where(Wiki.slug == slug).where(Wiki.is_global).get()
    except Wiki.DoesNotExist:
        return abort(404)

    return engine.get_template("site/wiki.html").render({"wiki": page})
