"""Anonymous device identity for the AllergyMap API.

AllergyMap is a crowdsensing platform: participants contribute symptom
reports and, from an allergy profile, receive personalised risk alerts. That
needs a stable notion of "the same participant over time" -- it does **not**
need to know who they are. The platform therefore stores no account, no
e-mail address, no password and no other personal identifier.

Identity model
--------------
A participant is a *device*, identified by a random UUID version 4 generated
in the browser on first use and kept in ``localStorage``. The identifier is
opaque: 122 bits of entropy, derived from nothing about the person or the
machine, and never reused across participants.

Because the identifier is unguessable, it doubles as a bearer credential --
possession of the value is the only authorisation the API asks for. That is a
deliberate trade-off, appropriate here because the data behind it is
pseudonymous (symptom scores, allergen sensitivities, coordinates) and never
directly identifying. It also gives the participant a portable profile with
no login: typing the same identifier on a second device -- the UI presents it
as a "transfer code" -- adopts the same profile.

Transport
---------
The identifier travels in the ``X-Device-Id`` request header rather than in
the URL or the request body:

* a header works uniformly for GET, POST and DELETE, so one decorator covers
  every identity-bearing route;
* keeping it out of the query string keeps it out of server access logs,
  browser history and ``Referer`` headers, where a bearer credential does not
  belong.

Validation
----------
Every request is checked against the canonical UUID version 4 form before it
reaches a route. This is not cosmetic: it stops arbitrary client-chosen
strings (``"admin"``, ``"1"``, an e-mail address) from becoming identities,
which would both weaken the unguessability the model rests on and let
personal data leak into the database through the identity field itself.
"""

from __future__ import annotations

import uuid
from functools import wraps

from flask import g, jsonify, request

#: Request header carrying the anonymous device identifier.
DEVICE_ID_HEADER = "X-Device-Id"

#: UUID version the platform issues and accepts. Version 4 is the purely
#: random variant; versions 1 and 2 embed a MAC address and a timestamp,
#: which would make the identifier weakly linkable to a machine.
DEVICE_ID_UUID_VERSION = 4


def normalize_device_id(raw: str | None) -> str:
    """Validate a device identifier and return it in canonical form.

    Accepts the value as a client may plausibly send or a participant may
    plausibly type it -- surrounding whitespace, upper-case hex digits, or
    the 32-character unhyphenated form -- and returns the canonical
    lower-case hyphenated representation used everywhere else in the system.

    Args:
        raw: The identifier as received, or None.

    Returns:
        The canonical UUID string, e.g.
        ``'3f2a9c1e-7b4d-4a6f-9e21-0c8d5b1a2f30'``.

    Raises:
        ValueError: If the value is missing, malformed, or not a version-4
            UUID. Callers should surface this as HTTP 400.

    Examples:
        >>> normalize_device_id('  3F2A9C1E-7B4D-4A6F-9E21-0C8D5B1A2F30 ')
        '3f2a9c1e-7b4d-4a6f-9e21-0c8d5b1a2f30'
    """
    if raw is None:
        raise ValueError(f"Missing {DEVICE_ID_HEADER} header.")

    candidate = str(raw).strip()
    if not candidate:
        raise ValueError(f"Missing {DEVICE_ID_HEADER} header.")

    try:
        parsed = uuid.UUID(candidate)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"{DEVICE_ID_HEADER} must be a UUID version 4.")

    if parsed.version != DEVICE_ID_UUID_VERSION:
        raise ValueError(f"{DEVICE_ID_HEADER} must be a UUID version 4.")

    return str(parsed)


def is_valid_device_id(raw: str | None) -> bool:
    """Report whether a value is an acceptable device identifier.

    A boolean convenience wrapper around `normalize_device_id`, for the places
    that branch on validity instead of rejecting the request (for example the
    migration of a legacy identifier).

    Args:
        raw: The candidate identifier, or None.

    Returns:
        True if the value is a version-4 UUID, False otherwise.
    """
    try:
        normalize_device_id(raw)
    except ValueError:
        return False
    return True


def require_device_id(view):
    """Reject a request that carries no valid ``X-Device-Id`` header.

    On success the canonical identifier is placed on `flask.g` as
    ``g.device_id``, so the wrapped view never re-reads or re-validates the
    header. On failure the view is not called at all and the client receives
    HTTP 400 with an explanatory message.

    Args:
        view: The Flask view function to wrap.

    Returns:
        The wrapped view function.

    Examples:
        >>> @bp.get('/me')  # doctest: +SKIP
        ... @require_device_id
        ... def read_me():
        ...     return jsonify({'device_id': g.device_id})
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            g.device_id = normalize_device_id(request.headers.get(DEVICE_ID_HEADER))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return view(*args, **kwargs)

    return wrapper
