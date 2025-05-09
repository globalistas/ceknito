"""User-related forms"""

from flask import request, redirect, url_for
from urllib.parse import urlparse, urljoin
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField
from wtforms import BooleanField, HiddenField, SelectField
from wtforms.validators import DataRequired, Length, Email, EqualTo
from wtforms.validators import Optional, Regexp, ValidationError
from wtforms.fields import EmailField
from flask_babel import lazy_gettext as _l
from app.config import config


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def get_redirect_target():
    for target in request.args.get("next"), request.referrer:
        if not target:
            continue
        if is_safe_url(target):
            return target


class RedirectForm(FlaskForm):
    next = HiddenField()

    def __init__(self, *args, **kwargs):
        FlaskForm.__init__(self, *args, **kwargs)
        if not self.next.data:
            self.next.data = get_redirect_target() or ""

    def redirect(self, endpoint="index", **values):
        if is_safe_url(self.next.data):
            return redirect(self.next.data)
        target = get_redirect_target()
        return redirect(target or url_for(endpoint, **values))


class OptionalIfFieldIsEmpty(Optional):
    """A custom field validator."""

    def __init__(self, field_name, *args, **kwargs):
        self.field_name = field_name
        super(OptionalIfFieldIsEmpty, self).__init__(*args, **kwargs)

    def __call__(self, form, field):
        other_field = form._fields.get(self.field_name)
        if other_field is None:
            raise Exception('no field named "{0}" in form'.format(self.field_name))
        if other_field.data == "":
            super(OptionalIfFieldIsEmpty, self).__call__(form, field)


class UsernameLength:
    def __call__(self, form, field):
        min = 2
        max = config.site.username_max_length
        length = field.data and len(field.data) or 0
        if length < min or length > max:
            if min == max:
                message = field.ngettext(
                    "Field must be exactly %(max)d character long.",
                    "Field must be exactly %(max)d characters long.",
                    max,
                )
            else:
                message = field.gettext(
                    "Field must be between %(min)d and %(max)d characters long."
                )

            raise ValidationError(message % dict(min=min, max=max, length=length))


class LoginForm(RedirectForm):
    """Login form."""

    username = StringField(_l("Username"), validators=[Length(max=256)])
    password = PasswordField(
        _l("Password"), validators=[DataRequired(), Length(min=7, max=256)]
    )
    remember = BooleanField(_l("Remember me"))


class RegistrationForm(FlaskForm):
    """Registration form."""

    username = StringField(
        _l("Username"), [UsernameLength(), Regexp(r"^[a-zA-ZÁ-ž0-9_-]+$")]
    )
    username_placeholder = StringField(_l("No spaces or diacritics"))
    email_optional_placeholder = StringField(
        _l("Used for password reset and notifications")
    )
    email_optional = EmailField(
        _l("Email Address (optional)"),
        validators=[
            OptionalIfFieldIsEmpty("email_optional"),
            Email(_l("Invalid email address.")),
        ],
    )
    email_required = EmailField(
        _l("Email Address (required)"), validators=[Email(_l("Invalid email address."))]
    )
    password_placeholder = StringField(_l("At least 7 characters"))
    password = PasswordField(
        _l("Password"),
        [
            DataRequired(),
            EqualTo("confirm", message=_l("Passwords must match")),
            Length(min=7, max=256),
        ],
    )
    confirm = PasswordField(_l("Repeat Password"))
    invitecode = StringField(_l("Invite Code"))
    accept_tos = BooleanField(_l("I accept the TOS"), [DataRequired()])
    captcha = StringField(_l("Captcha"))
    ctok = HiddenField()
    securityanswer = StringField(_l("Security question"))


class ResendConfirmationForm(FlaskForm):
    """For resending emails"""

    email = EmailField(
        _l("Email Address"), validators=[Email(_l("Invalid email address."))]
    )


class EditAccountForm(FlaskForm):
    email_optional = EmailField(
        _l("Email Address (optional)"),
        validators=[
            Optional(),
            Email(_l("Invalid email address.")),
        ],
    )
    email_required = EmailField(
        _l("Email Address (required)"), validators=[Email(_l("Invalid email address."))]
    )
    password = PasswordField(
        _l("New password"),
        [
            EqualTo("confirm", message=_l("Passwords must match")),
            Optional("password"),
            Length(min=7, max=256),
        ],
    )
    confirm = PasswordField(_l("Repeat Password"), [])

    oldpassword = PasswordField(
        _l("Your current password"), [DataRequired(), Length(min=7, max=256)]
    )


class DeleteAccountForm(FlaskForm):
    password = PasswordField(
        _l("Your password"),
        [
            DataRequired(),
            Length(min=7, max=256),
        ],
    )
    consent = StringField(_l("Type 'YES' here"), [DataRequired(), Length(max=10)])


class EditUserForm(FlaskForm):
    """Edit User info form."""

    # username = StringField('Username', [Length(min=2, max=32)])
    disable_sub_style = BooleanField(_l("Disable custom sub styles"))
    show_nsfw = SelectField(
        _l("NSFW content"),
        choices=[
            ("hide", _l("Hide from lists")),
            ("show", _l("Show without blur")),
            ("blur", _l("Blur until clicked")),
        ],
    )
    experimental = BooleanField(_l("Disable auto expandos"))
    noscroll = BooleanField(_l("Disable infinite scroll"))
    nochat = BooleanField(_l("Disable chat"))
    language = SelectField(_l("Language"), choices=[], validate_choice=False)
    highlight_unseen_comments = BooleanField(_l("Highlight new comments"))
    email_notify = BooleanField(_l("Email notifications"))
    comment_sort = SelectField(
        _l("Sort comments by"),
        choices=[
            ("new", _l("New")),
            ("top", _l("Top")),
            ("old", _l("Old")),
        ],
    )


class EditIgnoreForm(FlaskForm):
    """Edit User blocks form."""

    view_messages = SelectField(
        "", choices=[("hide", _l("Hide messages")), ("show", _l("Show messages"))]
    )
    view_content = SelectField(
        "",
        choices=[
            ("hide", _l("Hide posts and comments")),
            ("blur", _l("Blur posts and collapse comments")),
            ("show", _l("Show posts and comments")),
        ],
    )


class CreateUserMessageForm(FlaskForm):
    """CreateUserMessage form."""

    to = StringField(_l("to"), [Length(min=2, max=32), Regexp(r"[a-zA-Z0-9_-]+")])
    subject = StringField(
        _l("subject"), validators=[DataRequired(), Length(min=1, max=400)]
    )

    content = TextAreaField(
        _l("message"), validators=[DataRequired(), Length(min=1, max=16384)]
    )


class CreateUserMessageReplyForm(FlaskForm):
    """Form for replies to private messages."""

    mid = HiddenField()
    content = TextAreaField(
        _l("message"), validators=[DataRequired(), Length(min=1, max=16384)]
    )


class PasswordRecoveryForm(FlaskForm):
    """the 'forgot your password?' form"""

    email = EmailField(
        _l("Email Address"), validators=[Email(_l("Invalid email address."))]
    )
    captcha = StringField(_l("Captcha"))
    ctok = HiddenField()


class PasswordResetForm(FlaskForm):
    """the 'forgot your password?' form"""

    user = HiddenField()
    key = HiddenField()
    password = PasswordField(
        _l("Password"),
        [
            DataRequired(),
            EqualTo("confirm", message=_l("Passwords must match")),
            Length(min=7, max=256),
        ],
    )
    confirm = PasswordField(_l("Repeat Password"))


class LogOutForm(FlaskForm):
    """Logout form. This form has no fields.
    We only use it for the CSRF stuff"""

    pass
