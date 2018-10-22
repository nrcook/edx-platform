"""
Utility functions for setting "logged in" cookies used by subdomains.
"""
from __future__ import unicode_literals

import json
import logging
import time

import six
from django.conf import settings
from django.contrib.auth.models import User
from django.dispatch import Signal
from django.urls import NoReverseMatch, reverse
from django.utils.http import cookie_date

from edx_rest_framework_extensions.auth.jwt import cookies as jwt_cookies
from edx_rest_framework_extensions.auth.jwt.constants import JWT_DELIMITER
from oauth2_provider.models import Application
from openedx.core.djangoapps.oauth_dispatch.adapters import DOTAdapter
from openedx.core.djangoapps.oauth_dispatch.api import create_dot_access_token, refresh_dot_access_token
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_from_token
from openedx.core.djangoapps.user_api.accounts.utils import retrieve_last_sitewide_block_completed
from openedx.core.djangoapps.user_authn.exceptions import AuthFailedError
from student.models import CourseEnrollment


log = logging.getLogger(__name__)


CREATE_LOGON_COOKIE = Signal(providing_args=['user', 'response'])


JWT_COOKIE_NAMES = (
    # Header and payload sections of a JSON Web Token containing user
    # information and used as an access token.
    jwt_cookies.jwt_cookie_header_payload_name(),

    # Signature section of a JSON Web Token.
    jwt_cookies.jwt_cookie_signature_name(),

    # Refresh token, which can be used to get a new JSON Web Token.
    jwt_cookies.jwt_refresh_cookie_name(),
)

# TODO (ARCH-245): Remove the following deprecated cookies.
DEPRECATED_LOGGED_IN_COOKIE_NAMES = (
    # Set to 'true' if the user is logged in.
    settings.EDXMKTG_LOGGED_IN_COOKIE_NAME,

    # JSON-encoded dictionary with user information.
    settings.EDXMKTG_USER_INFO_COOKIE_NAME,
)

ALL_LOGGED_IN_COOKIE_NAMES = JWT_COOKIE_NAMES + DEPRECATED_LOGGED_IN_COOKIE_NAMES


def is_logged_in_cookie_set(request):
    """ Check whether the request has logged in cookies set. """
    return (
        settings.EDXMKTG_LOGGED_IN_COOKIE_NAME in request.COOKIES and
        request.COOKIES[settings.EDXMKTG_LOGGED_IN_COOKIE_NAME]
    )


def delete_logged_in_cookies(response):
    """
    Delete cookies indicating that the user is logged in.
    Arguments:
        response (HttpResponse): The response sent to the client.
    Returns:
        HttpResponse
    """
    for cookie_name in ALL_LOGGED_IN_COOKIE_NAMES:
        response.delete_cookie(
            cookie_name.encode('utf-8'),
            path='/',
            domain=settings.SESSION_COOKIE_DOMAIN
        )

    return response


def standard_cookie_settings(request):
    """ Returns the common cookie settings (e.g. expiration time). """

    if request.session.get_expire_at_browser_close():
        max_age = None
        expires = None
    else:
        max_age = request.session.get_expiry_age()
        _expires_time = time.time() + max_age
        expires = cookie_date(_expires_time)

    cookie_settings = {
        'max_age': max_age,
        'expires': expires,
        'domain': settings.SESSION_COOKIE_DOMAIN,
        'path': '/',
        'httponly': None,
    }

    # In production, TLS should be enabled so that this cookie is encrypted
    # when we send it.  We also need to set "secure" to True so that the browser
    # will transmit it only over secure connections.
    #
    # In non-production environments (acceptance tests, devstack, and sandboxes),
    # we still want to set this cookie.  However, we do NOT want to set it to "secure"
    # because the browser won't send it back to us.  This can cause an infinite redirect
    # loop in the third-party auth flow, which calls `is_logged_in_cookie_set` to determine
    # whether it needs to set the cookie or continue to the next pipeline stage.
    cookie_settings['secure'] = request.is_secure()

    return cookie_settings


def set_logged_in_cookies(request, response, user):
    """
    Set cookies at the time of user login. See ALL_LOGGED_IN_COOKIE_NAMES to see
    which cookies are set.

    Arguments:
        request (HttpRequest): The request to the view, used to calculate
            the cookie's expiration date based on the session expiration date.
        response (HttpResponse): The response on which the cookie will be set.
        user (User): The currently logged in user.

    Returns:
        HttpResponse

    """
    # Note: The user may not yet be set on the request object by this time,
    # especially during third party authentication.  So use the user object
    # that is passed in when needed.
    if user.is_authenticated and not user.is_anonymous:

        # JWT cookies expire at the same time as other login-related cookies
        # so that cookie-based login determination remains consistent.
        cookie_settings = standard_cookie_settings(request)

        _set_deprecated_logged_in_cookie(response, cookie_settings)
        set_deprecated_user_info_cookie(response, request, user, cookie_settings)
        _create_and_set_jwt_cookies(response, request, cookie_settings, user=user)
        CREATE_LOGON_COOKIE.send(sender=None, user=user, response=response)

    return response


def refresh_jwt_cookies(request, response):
    """
    Resets the JWT related cookies in the response, while expecting a refresh
    cookie in the request.
    """
    try:
        refresh_token = request.COOKIES[jwt_cookies.jwt_refresh_cookie_name()]
    except KeyError:
        raise AuthFailedError(u"JWT Refresh Cookie not found in request.")

    # TODO don't extend the cookie expiration - reuse value from existing cookie
    cookie_settings = standard_cookie_settings(request)
    _create_and_set_jwt_cookies(response, request, cookie_settings, refresh_token=refresh_token)
    return response


def set_deprecated_user_info_cookie(response, request, user, cookie_settings=None):
    """
    Sets the user info cookie on the response.

    The user info cookie has the following format:
    {
        "version": 1,
        "username": "test-user",
        "header_urls": {
            "account_settings": "https://example.com/account/settings",
            "resume_block":
                "https://example.com//courses/org.0/course_0/Run_0/jump_to/i4x://org.0/course_0/vertical/vertical_4"
            "learner_profile": "https://example.com/u/test-user",
            "logout": "https://example.com/logout"
        }
    }
    """
    cookie_settings = cookie_settings or standard_cookie_settings(request)
    user_info = _get_user_info_cookie_data(request, user)
    response.set_cookie(
        settings.EDXMKTG_USER_INFO_COOKIE_NAME.encode('utf-8'),
        json.dumps(user_info),
        **cookie_settings
    )


def _set_deprecated_logged_in_cookie(response, cookie_settings):
    """ Sets the logged in cookie on the response. """

    # Backwards compatibility: set the cookie indicating that the user
    # is logged in.  This is just a boolean value, so it's not very useful.
    # In the future, we should be able to replace this with the "user info"
    # cookie set below.
    response.set_cookie(
        settings.EDXMKTG_LOGGED_IN_COOKIE_NAME.encode('utf-8'),
        'true',
        **cookie_settings
    )

    return response


def _get_user_info_cookie_data(request, user):
    """ Returns information that will populate the user info cookie. """

    # Set a cookie with user info.  This can be used by external sites
    # to customize content based on user information.  Currently,
    # we include information that's used to customize the "account"
    # links in the header of subdomain sites (such as the marketing site).
    header_urls = {'logout': reverse('logout')}

    # Unfortunately, this app is currently used by both the LMS and Studio login pages.
    # If we're in Studio, we won't be able to reverse the account/profile URLs.
    # To handle this, we don't add the URLs if we can't reverse them.
    # External sites will need to have fallback mechanisms to handle this case
    # (most likely just hiding the links).
    try:
        header_urls['account_settings'] = reverse('account_settings')
        header_urls['learner_profile'] = reverse('learner_profile', kwargs={'username': user.username})
    except NoReverseMatch:
        pass

    # Add 'resume course' last completed block
    try:
        header_urls['resume_block'] = retrieve_last_sitewide_block_completed(user)
    except User.DoesNotExist:
        pass

    # Convert relative URL paths to absolute URIs
    for url_name, url_path in six.iteritems(header_urls):
        header_urls[url_name] = request.build_absolute_uri(url_path)

    user_info = {
        'version': settings.EDXMKTG_USER_INFO_COOKIE_VERSION,
        'username': user.username,
        'header_urls': header_urls,
        'enrollmentStatusHash': CourseEnrollment.generate_enrollment_status_hash(user)
    }

    return user_info


def _create_and_set_jwt_cookies(response, request, cookie_settings, user=None, refresh_token=None):
    """ Sets a cookie containing a JWT on the response. """
    # For security reasons, the JWT that is embedded inside the cookie expires
    # much sooner than the cookie itself, per the following setting.
    expires_in = settings.JWT_AUTH['JWT_IN_COOKIE_EXPIRATION']

    oauth_application = _get_login_oauth_client()
    if refresh_token:
        access_token = refresh_dot_access_token(
            request, oauth_application.client_id, refresh_token, expires_in=expires_in,
        )
    else:
        access_token = create_dot_access_token(
            request, user, oauth_application, expires_in=expires_in, scopes=['email', 'profile'],
        )
    jwt = create_jwt_from_token(access_token, DOTAdapter(), use_asymmetric_key=True)
    jwt_header_and_payload, jwt_signature = _parse_jwt(jwt)
    _set_jwt_cookies(
        response,
        cookie_settings,
        jwt_header_and_payload,
        jwt_signature,
        access_token['refresh_token'],
    )


def _parse_jwt(jwt):
    """
    Parses and returns the following parts of the jwt: header_and_payload, signature
    """
    jwt_parts = jwt.split(JWT_DELIMITER)
    header_and_payload = JWT_DELIMITER.join(jwt_parts[0:2])
    signature = jwt_parts[2]
    return header_and_payload, signature


def _set_jwt_cookies(response, cookie_settings, jwt_header_and_payload, jwt_signature, refresh_token):
    """
    Sets the given jwt_header_and_payload, jwt_signature, and refresh token in 3 different cookies.
    The latter 2 cookies are set as httponly.
    """
    cookie_settings['httponly'] = None
    response.set_cookie(
        jwt_cookies.jwt_cookie_header_payload_name(),
        jwt_header_and_payload,
        **cookie_settings
    )

    cookie_settings['httponly'] = True
    response.set_cookie(
        jwt_cookies.jwt_cookie_signature_name(),
        jwt_signature,
        **cookie_settings
    )
    response.set_cookie(
        jwt_cookies.jwt_refresh_cookie_name(),
        refresh_token,
        **cookie_settings
    )


def _get_login_oauth_client():
    """
    Returns the configured OAuth Client/Application used for Login.
    """
    login_client_id = settings.JWT_AUTH['JWT_LOGIN_CLIENT_ID']
    try:
        return Application.objects.get(client_id=login_client_id)
    except Application.DoesNotExist:
        raise AuthFailedError(
            u"OAuth Client for the Login service, '{}', is not configured.".format(login_client_id)
        )
