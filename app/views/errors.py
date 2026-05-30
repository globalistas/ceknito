"""Pages to be migrated to a wiki-like system"""

from datetime import datetime

from flask import Blueprint, request, redirect, url_for, jsonify
from flask_login import current_user

from .. import config
from ..misc import engine, logger, ensure_locale_loaded, get_locale, send_email
from ..caching import cache

bp = Blueprint("errors", __name__)


@bp.app_errorhandler(401)
def unauthorized(_):
    """401 Unauthorized"""
    return redirect(url_for("auth.login"))


@bp.app_errorhandler(403)
def forbidden_error(_):
    """403 Forbidden"""
    return render_error_template("errors/403.html"), 403


@bp.app_errorhandler(404)
def not_found(_):
    """404 Not found error"""
    if request.path.startswith("/api"):
        if request.path.startswith("/api/v3"):
            return jsonify(msg="Method not found or not implemented"), 404
        return jsonify(status="error", error="Method not found or not implemented"), 404
    return render_error_template("errors/404.html"), 404


@bp.app_errorhandler(417)
def teapot_error(_):
    """418 I'm a teapot"""
    return render_error_template("errors/417.html"), 418


@bp.app_errorhandler(429)
def too_many_requests_error(_):
    """429 Too many requests"""
    return render_error_template("errors/429.html"), 429


@bp.app_errorhandler(500)
def server_error(_):
    """500 Internal server error"""
    import traceback
    import sys

    typ, val, tb = sys.exc_info()
    logger.error('EXCEPTION: %s, "%s", %s', typ.__name__, val, traceback.format_tb(tb))

    try:
        username = current_user.name if current_user.is_authenticated else "Anonymous"
        error_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tb_str = "".join(traceback.format_tb(tb))
        html_content = f"""
        <p><strong>User:</strong> {username}</p>
        <p><strong>Time:</strong> {error_time} UTC</p>
        <p><strong>Path:</strong> {request.path}</p>
        <p><strong>Error:</strong> {typ.__name__}: {val}</p>
        <pre>{tb_str}</pre>
        """
        send_email(
            config.mail.default_to, "500 Internal Server Error", "", html_content
        )
    except Exception as e:
        logger.error("Failed to send error email: %s", e)

    if request.path.startswith("/api"):
        if request.path.startswith("/api/v3"):
            return jsonify(msg="Internal error"), 500
        return jsonify(status="error", error="Internal error"), 500

    return render_error_template("errors/500.html"), 500


def render_error_template(template):
    ensure_locale_loaded()
    lang = get_locale()
    daynight_cookie = request.cookies.get("dayNight")
    return _render_error_template(template, lang, daynight_cookie)


@cache.memoize(300)
def _render_error_template(template, *_):
    return engine.get_template(template).render({})
