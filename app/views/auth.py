""" Authentication endpoints and functions """
from urllib.parse import urlparse
from datetime import datetime, timedelta
import uuid
import re
import requests
from peewee import fn
from flask import (
    Blueprint,
    request,
    redirect,
    abort,
    url_for,
    session,
    current_app,
    flash,
    jsonify,
)
from flask_login import current_user, login_user, login_required
from flask_babel import _
from itsdangerous import URLSafeTimedSerializer
from itsdangerous.exc import SignatureExpired, BadSignature
from .. import misc
from ..config import config
from ..auth import auth_provider, email_validation_is_required
from ..auth import normalize_email, create_user
from ..forms import LoginForm, RegistrationForm, ResendConfirmationForm
from ..misc import engine, send_email, is_domain_banned, gevent_required
from ..misc import ratelimit, AUTH_LIMIT, SIGNUP_LIMIT
from ..models import User, UserStatus, InviteCode, rconn

bp = Blueprint("auth", __name__)


def sanitize_serv(serv):
    serv = serv.replace("%253A", "%3A")
    return serv.replace("%252F", "%2F")


def generate_cas_token(uid):
    # Create Session Ticket and store it in Redis
    token = str(uuid.uuid4())
    rconn.setex(name="cas-" + token, value=uid, time=30)
    return token


def handle_cas_ok(uid):
    # 2 - Send the ticket over to `service` with the ticket parameter
    return redirect(
        sanitize_serv(request.args.get("service"))
        + "&ticket="
        + generate_cas_token(uid)
    )


@bp.route("/proxyValidate", methods=["GET"])
@ratelimit(AUTH_LIMIT)
def sso_proxy_validate():
    if not request.args.get("ticket") or not request.args.get("service"):
        abort(400)

    pipe = rconn.pipeline()
    pipe.get("cas-" + request.args.get("ticket"))
    pipe.delete("cas-" + request.args.get("ticket"))
    red_c = pipe.execute()

    if red_c:
        try:
            user = User.get((User.uid == red_c[0].decode()) & (User.status << (0, 100)))
        except User.DoesNotExist:
            return (
                "<cas:serviceResponse xmlns:cas='http://www.yale.edu/tp/cas'>"
                '<cas:authenticationFailure code="INVALID_TICKET">'
                + _("User not found or invalid ticket")
                + "</cas:authenticationFailure></cas:serviceResponse>",
                401,
            )

        return (
            "<cas:serviceResponse xmlns:cas='http://www.yale.edu/tp/cas'>"
            f"<cas:authenticationSuccess><cas:user>{user.name.lower()}</cas:user>"
            "</cas:authenticationSuccess></cas:serviceResponse>",
            200,
        )
    else:
        return (
            "<cas:serviceResponse xmlns:cas='http://www.yale.edu/tp/cas'>"
            '<cas:authenticationFailure code="INVALID_TICKET">'
            + _("User not found or invalid ticket")
            + "</cas:authenticationFailure></cas:serviceResponse>",
            401,
        )


@bp.route("/register", methods=["GET", "POST"])
@gevent_required  # Contacts Keycloak if configured, does async task (email).
@ratelimit(SIGNUP_LIMIT, methods=["POST"])
def register():
    """ Endpoint for the registration form """
    if current_user.is_authenticated:
        return redirect(url_for("home.index"))
    form = RegistrationForm()
    if email_validation_is_required():
        del form.email_optional
    else:
        del form.email_required
    captcha = misc.create_captcha()

    if not config.site.enable_registration:
        return engine.get_template("user/registration_disabled.html").render({})

    if not form.validate():
        return engine.get_template("user/register.html").render(
            {"error": misc.get_errors(form, True), "regform": form, "captcha": captcha}
        )

    if not misc.validate_captcha(form.ctok.data, form.captcha.data):
        return engine.get_template("user/register.html").render(
            {"error": _("Invalid captcha."), "regform": form, "captcha": captcha}
        )

    if not misc.allowedNames.match(form.username.data):
        return engine.get_template("user/register.html").render(
            {
                "error": _("Username has invalid characters."),
                "regform": form,
                "captcha": captcha,
            }
        )

    # check if user or email are in use
    existing_user = None
    try:
        existing_user = User.get(fn.Lower(User.name) == form.username.data.lower())
        # Allow reregistering an existing user account which has never
        # fetched the verification link and is more than two days old.
        if (
            existing_user.status != UserStatus.PROBATION
            or (datetime.utcnow() - existing_user.joindate).days < 2
        ):
            return engine.get_template("user/register.html").render(
                {
                    "error": _("Username is not available."),
                    "regform": form,
                    "captcha": captcha,
                }
            )
    except User.DoesNotExist:
        pass

    if email_validation_is_required():
        email = form.email_required.data
    else:
        email = form.email_optional.data
    if email:
        email = normalize_email(email)

    if email:
        if is_domain_banned(email, domain_type="email"):
            return engine.get_template("user/register.html").render(
                {
                    "error": _("We do not accept emails from your email provider."),
                    "regform": form,
                    "captcha": captcha,
                }
            )
        user_by_email = auth_provider.get_user_by_email(email)
        if user_by_email is not None and user_by_email != existing_user:
            return engine.get_template("user/register.html").render(
                {
                    "error": _("E-mail address is already in use."),
                    "regform": form,
                    "captcha": captcha,
                }
            )

    if config.site.enable_security_question:
        if form.securityanswer.data.lower() != session["sa"].lower():
            return engine.get_template("user/register.html").render(
                {
                    "error": _("Incorrect answer for security question."),
                    "regform": form,
                    "captcha": captcha,
                }
            )

    if config.site.require_invite_code:
        if not form.invitecode.data:
            return engine.get_template("user/register.html").render(
                {
                    "error": _("Invalid invite code."),
                    "regform": form,
                    "captcha": captcha,
                }
            )
        # Check if there's a valid invite code in the database
        try:
            InviteCode.get_valid(form.invitecode.data)
        except InviteCode.DoesNotExist:
            return engine.get_template("user/register.html").render(
                {
                    "error": _("Invalid invite code."),
                    "regform": form,
                    "captcha": captcha,
                }
            )

        InviteCode.update(uses=InviteCode.uses + 1).where(
            InviteCode.code == form.invitecode.data
        ).execute()

    user = create_user(
        form.username.data,
        form.password.data,
        email,
        form.invitecode.data,
        existing_user,
    )

    # Custom sendmail info for regs
    regdate = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    useragent = request.headers.get("User-Agent")
    ip = request.environ.get("HTTP_X_REAL_IP", request.remote_addr)
    text_content = user.name + "\n" + ip + "\n" + regdate + "\n" + useragent
    send_email(config.mail.default_to, "New registration", text_content, "")

    if email_validation_is_required():
        send_login_link_email(user)
        session["reg"] = {
            "uid": user.uid,
            "email": user.email,
            "now": datetime.utcnow(),
        }
        return redirect(url_for("auth.confirm_registration"))
    else:
        theuser = misc.load_user(user.uid)
        login_user(theuser)
        theuser.update_prefs("nsfw", "1")
        theuser.update_prefs("email_notify", "1")
        session["remember_me"] = False
        return redirect(url_for("wiki.welcome"))


def send_login_link_email(user):
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="login")
    token = s.dumps({"uid": user.uid, "resets": user.resets})
    send_email(
        user.email,
        _("Confirm your new account on %(site)s", site=config.site.name),
        text_content=engine.get_template("user/email/login-link.txt").render(
            dict(user=user, token=token)
        ),
        html_content=engine.get_template("user/email/login-link.html").render(
            dict(user=user, token=token)
        ),
    )


def user_from_login_token(token):
    try:
        s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="login")
        info = s.loads(token, max_age=8 * 60 * 60)  # TODO in config?
        return User.get(
            (User.uid == info["uid"]) & (User.resets == info.get("resets", 0))
        )
    except (SignatureExpired, BadSignature, User.DoesNotExist):
        return None


@bp.route("/register/confirm")
def confirm_registration():
    if current_user.is_authenticated:
        return redirect(url_for("home.index"))

    email = session.get("reg", {}).get("email", "")
    return engine.get_template("user/check-your-email.html").render(
        {"reason": "registration", "email": email, "show_resend_link": email != ""}
    )


@bp.route("/login/with-token/<token>")
@gevent_required  # Contacts Keycloak if configured.
@ratelimit(SIGNUP_LIMIT)
def login_with_token(token):
    if current_user.is_authenticated:
        return redirect(url_for("home.index"))
    user = user_from_login_token(token)
    if (
        user is None
        or user.status == UserStatus.BANNED
        or user.status == UserStatus.DELETED
    ):
        flash(_("The link you used is invalid or has expired."), "error")
        return redirect(url_for("auth.resend_confirmation_email"))
    elif user.status == UserStatus.PROBATION:
        user.status = UserStatus.OK
        user.save()
        auth_provider.set_email_verified(user)
        theuser = misc.load_user(user.uid)
        login_user(theuser)
        session["remember_me"] = False
        session.pop("reg", None)
    return redirect(url_for("wiki.welcome"))


@bp.route("/register/fix-registration-email", methods=["GET", "POST"])
@gevent_required  # Contacts Keycloak, launches an async task (email).
@ratelimit(SIGNUP_LIMIT)
def fix_registration_email():
    if current_user.is_authenticated:
        return redirect(url_for("home.index"))

    user = None
    reg = session.get("reg")
    if reg is not None and datetime.utcnow() - reg["now"] < timedelta(hours=8):
        try:
            user = User.get(
                (User.uid == reg["uid"])
                & (User.email == reg["email"])
                & (User.status == UserStatus.PROBATION)
            )
        except User.DoesNotExist:
            pass

    if user is None:
        flash(_("The link you used is invalid or has expired."), "error")
        return redirect(url_for("auth.register"))

    form = ResendConfirmationForm()
    if not form.validate_on_submit():
        form.email.data = reg["email"]
        return engine.get_template("user/fix_registration_email.html").render(
            {"form": form, "error": misc.get_errors(form, True)}
        )

    email = normalize_email(form.email.data)
    if is_domain_banned(email, domain_type="email"):
        return engine.get_template("user/fix_registration_email.html").render(
            {
                "error": _("We do not accept emails from your email provider."),
                "form": form,
            }
        )
    user_by_email = auth_provider.get_user_by_email(email)
    if user_by_email is not None and user_by_email != user:
        return engine.get_template("user/fix_registration_email.html").render(
            {"error": _("E-mail address is already in use."), "form": form}
        )
    elif user_by_email is None:
        auth_provider.change_unconfirmed_user_email(user, email)
        user = User.get(User.uid == user.uid)

    send_login_link_email(user)
    session["reg"] = {
        "uid": user.uid,
        "email": user.email,
        "now": datetime.utcnow(),
    }

    return redirect(url_for("auth.confirm_registration"))


@bp.route("/register/resend-confirmation", methods=["GET", "POST"])
@gevent_required  # Starts an async task (email).
@ratelimit(SIGNUP_LIMIT)
def resend_confirmation_email():
    if current_user.is_authenticated:
        return redirect(url_for("home.index"))

    form = ResendConfirmationForm()
    if not form.validate():
        return engine.get_template("user/resend_confirmation.html").render(
            {"form": form, "error": misc.get_errors(form, True)}
        )

    try:
        email = normalize_email(form.email.data)
        user = User.get(fn.Lower(User.email) == email.lower())
    except User.DoesNotExist:
        return redirect(url_for("user.recovery_email_sent"))

    if user.status == UserStatus.PROBATION:
        send_login_link_email(user)
        session.pop("reg", None)
        return redirect(url_for("auth.confirm_registration"))
    elif user.status == UserStatus.OK:
        flash(_("Your email is already confirmed."))
        return redirect(url_for("auth.login"))

    return engine.get_template("user/check-your-email.html").render(
        {"reason": "registration", "email": "", "show_resend_link": False}
    )


@bp.route("/login", methods=["GET", "POST"])
@gevent_required  # Contacts Keycloak if configured.
@ratelimit(AUTH_LIMIT)
def login():
    """ Endpoint for the login form """
    if request.args.get("service"):
        # CAS login. Verify that we trust the initiator.
        url = urlparse(request.args.get("service"))
        if url.netloc not in config.site.cas_authorized_hosts:
            abort(403)

        if current_user.is_authenticated:
            # User is auth'd. Return ticket.
            return handle_cas_ok(uid=current_user.uid)

    if current_user.is_authenticated:
        return redirect(url_for("home.index"))

    form = LoginForm()
    if not form.validate_on_submit():
        return engine.get_template("user/login.html").render(
            {"error": misc.get_errors(form, True), "loginform": form}
        )
    try:
        user = User.get(fn.Lower(User.name) == form.username.data.lower())
    except User.DoesNotExist:
        return engine.get_template("user/login.html").render(
            {"error": _("Invalid username or password."), "loginform": form}
        )

    if user.status not in (UserStatus.PROBATION, UserStatus.OK):
        return engine.get_template("user/login.html").render(
            {"error": _("Invalid username or password."), "loginform": form}
        )

    if not auth_provider.validate_password(user, form.password.data):
        return engine.get_template("user/login.html").render(
            {"error": _("Invalid username or password."), "loginform": form}
        )

    if user.status == UserStatus.PROBATION:
        return redirect(url_for("auth.resend_confirmation_email"))
    session["remember_me"] = form.remember.data
    session.pop("reg", None)
    theuser = misc.load_user(user.uid)
    login_user(theuser, remember=form.remember.data)
    if request.args.get("service"):
        return handle_cas_ok(uid=user.uid)

    return form.redirect("index")


@bp.route("/auth/matrix", methods=["POST"])
@gevent_required  # Makes request from Matrix server.
@login_required
def get_ticket():
    """ Returns a CAS ticket for the current user """
    token = generate_cas_token(current_user.uid)
    # Not using safe_requests since we're supposed to trust this server.
    uri = f"{config.matrix.homeserver}/_matrix/client/r0/login/cas/ticket?redirectUrl=throat%3A%2F%2F&ticket={token}"
    resp = requests.get(uri, allow_redirects=False)

    if resp.status_code == 200:
        matches = re.search(r"href=\".+\?loginToken=(.+)\" class=.+?>", resp.text)
        return jsonify(token=matches.groups()[0])

    return jsonify(error="absolutely"), 400
