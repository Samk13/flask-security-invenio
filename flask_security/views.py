# -*- coding: utf-8 -*-
"""
flask_security.views
~~~~~~~~~~~~~~~~~~~~

Flask-Security views module

:copyright: (c) 2012 by Matt Wright.
:license: MIT, see LICENSE for more details.
"""

from flask import Blueprint, after_this_request, current_app, redirect, request
from flask_login import current_user, login_required
from werkzeug.local import LocalProxy

from .changeable import change_user_password
from .confirmable import (
    confirm_email_token_status,
    confirm_user,
    send_confirmation_instructions,
)
from .decorators import anonymous_user_required
from .recoverable import (
    reset_password_token_status,
    send_reset_password_instructions,
    update_password,
)
from .registerable import register_user
from .utils import (
    config_value,
    do_flash,
    get_message,
    get_post_login_redirect,
    get_post_logout_redirect,
    get_post_register_redirect,
    get_url,
    login_user,
    logout_user,
    slash_url_suffix,
)
from .utils import url_for_security as url_for

# Convenient references
_security = LocalProxy(lambda: current_app.extensions["security"])

_datastore = LocalProxy(lambda: _security.datastore)


def _commit(response=None):
    _datastore.commit()
    return response


def _ctx(endpoint):
    return _security._run_ctx_processor(endpoint)


@anonymous_user_required
def login():
    """View function for login view"""

    form_class = _security.login_form

    form = form_class(request.form)

    if form.validate_on_submit():
        login_user(form.user)
        after_this_request(_commit)

        return redirect(get_post_login_redirect(form.next.data))

    return _security.render_template(
        config_value("LOGIN_USER_TEMPLATE"), login_user_form=form, **_ctx("login")
    )


def logout():
    """View function which handles a logout request."""

    if current_user.is_authenticated:
        logout_user()

    return redirect(get_post_logout_redirect())


@anonymous_user_required
def register():
    """View function which handles a registration request."""

    if _security.confirmable:
        form_class = _security.confirm_register_form
    else:
        form_class = _security.register_form

    form = form_class(request.form)

    if form.validate_on_submit():
        user = register_user(**form.to_dict())
        form.user = user

        if not _security.confirmable or _security.login_without_confirmation:
            after_this_request(_commit)
            login_user(user)

        if not request.is_json:
            if "next" in form:
                redirect_url = get_post_register_redirect(form.next.data)
            else:
                redirect_url = get_post_register_redirect()

            return redirect(redirect_url)

    return _security.render_template(
        config_value("REGISTER_USER_TEMPLATE"),
        register_user_form=form,
        **_ctx("register")
    )


def send_confirmation():
    """View function which sends confirmation instructions."""

    form_class = _security.send_confirmation_form

    form = form_class()

    if form.validate_on_submit():
        send_confirmation_instructions(form.user)
        do_flash(*get_message("CONFIRMATION_REQUEST", email=form.user.email))

    return _security.render_template(
        config_value("SEND_CONFIRMATION_TEMPLATE"),
        send_confirmation_form=form,
        **_ctx("send_confirmation")
    )


def confirm_email(token):
    """View function which handles a email confirmation request."""

    expired, invalid, user = confirm_email_token_status(token)

    if not user or invalid:
        invalid = True
        do_flash(*get_message("INVALID_CONFIRMATION_TOKEN"))

    already_confirmed = user is not None and user.confirmed_at is not None

    if expired and not already_confirmed:
        send_confirmation_instructions(user)
        do_flash(
            *get_message(
                "CONFIRMATION_EXPIRED",
                email=user.email,
                within=_security.confirm_email_within,
            )
        )
    if invalid or (expired and not already_confirmed):
        return redirect(
            get_url(_security.confirm_error_view) or url_for("send_confirmation")
        )

    if user != current_user:
        logout_user()
        login_user(user)

    if confirm_user(user):
        after_this_request(_commit)
        msg = "EMAIL_CONFIRMED"
    else:
        msg = "ALREADY_CONFIRMED"

    do_flash(*get_message(msg))

    return redirect(
        get_url(_security.post_confirm_view) or get_url(_security.post_login_view)
    )


@anonymous_user_required
def forgot_password():
    """View function that handles a forgotten password request."""

    form_class = _security.forgot_password_form

    form = form_class()

    if form.validate_on_submit():
        send_reset_password_instructions(form.user)

        do_flash(*get_message("PASSWORD_RESET_REQUEST", email=form.user.email))

    return _security.render_template(
        config_value("FORGOT_PASSWORD_TEMPLATE"),
        forgot_password_form=form,
        **_ctx("forgot_password")
    )


@anonymous_user_required
def reset_password(token):
    """View function that handles a reset password request."""

    expired, invalid, user = reset_password_token_status(token)

    if not user or invalid:
        invalid = True
        do_flash(*get_message("INVALID_RESET_PASSWORD_TOKEN"))

    if expired:
        send_reset_password_instructions(user)
        do_flash(
            *get_message(
                "PASSWORD_RESET_EXPIRED",
                email=user.email,
                within=_security.reset_password_within,
            )
        )
    if invalid or expired:
        return redirect(url_for("forgot_password"))

    form = _security.reset_password_form()
    form.user = user

    if form.validate_on_submit():
        after_this_request(_commit)
        update_password(user, form.password.data)
        do_flash(*get_message("PASSWORD_RESET"))
        login_user(user)
        return redirect(
            get_url(_security.post_reset_view) or get_url(_security.post_login_view)
        )

    return _security.render_template(
        config_value("RESET_PASSWORD_TEMPLATE"),
        reset_password_form=form,
        reset_password_token=token,
        **_ctx("reset_password")
    )


@login_required
def change_password():
    """View function which handles a change password request."""

    form_class = _security.change_password_form

    form = form_class()

    if form.validate_on_submit():
        after_this_request(_commit)
        change_user_password(current_user._get_current_object(), form.new_password.data)
        do_flash(*get_message("PASSWORD_CHANGE"))
        return redirect(
            get_url(_security.post_change_view) or get_url(_security.post_login_view)
        )

    return _security.render_template(
        config_value("CHANGE_PASSWORD_TEMPLATE"),
        change_password_form=form,
        **_ctx("change_password")
    )


def create_blueprint(state, import_name):
    """Creates the security extension blueprint"""

    bp = Blueprint(
        state.blueprint_name,
        import_name,
        url_prefix=state.url_prefix,
        subdomain=state.subdomain,
        template_folder="templates",
    )

    bp.route(state.logout_url, endpoint="logout")(logout)

    bp.route(state.login_url, methods=["GET", "POST"], endpoint="login")(login)

    if state.registerable:
        bp.route(state.register_url, methods=["GET", "POST"], endpoint="register")(
            register
        )

    if state.recoverable:
        bp.route(state.reset_url, methods=["GET", "POST"], endpoint="forgot_password")(
            forgot_password
        )
        bp.route(
            state.reset_url + slash_url_suffix(state.reset_url, "<token>"),
            methods=["GET", "POST"],
            endpoint="reset_password",
        )(reset_password)

    if state.changeable:
        bp.route(state.change_url, methods=["GET", "POST"], endpoint="change_password")(
            change_password
        )

    if state.confirmable:
        bp.route(
            state.confirm_url, methods=["GET", "POST"], endpoint="send_confirmation"
        )(send_confirmation)
        bp.route(
            state.confirm_url + slash_url_suffix(state.confirm_url, "<token>"),
            methods=["GET", "POST"],
            endpoint="confirm_email",
        )(confirm_email)

    return bp
