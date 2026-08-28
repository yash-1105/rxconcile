"""Demo identity. **This is not authentication.**

Credentials are hardcoded below and the signing secret is committed alongside
them, so anyone with the repository can mint a token. Nothing here protects
anything, and no part of it should ever be described as security.

What it *does* buy, and the only reason it exists: the server derives who is
calling from a token it issued, rather than from a role the caller asserts.
Filtering by a caller-supplied role would be filtering by nothing — the caller
would simply claim ``admin``. A token carries the email only; the role is looked
up here, server-side, from the same table the sign-in screen uses.

For a real deployment this whole module is replaced by an identity provider.
"""

from __future__ import annotations

import hmac
from hashlib import sha256
from typing import Final, Literal

Role = Literal["employee", "admin"]


class DemoUser:
    """One hardcoded demo account."""

    __slots__ = ("email", "password", "name", "employee_number", "role")

    def __init__(
        self, email: str, password: str, name: str, employee_number: str, role: Role
    ) -> None:
        self.email = email
        self.password = password
        self.name = name
        self.employee_number = employee_number
        self.role = role


#: The same two accounts the sign-in screen offers. Passwords are stored in
#: plain text on purpose: hashing a published demo credential would imply a
#: protection that does not exist.
DEMO_USERS: Final[dict[str, DemoUser]] = {
    user.email: user
    for user in (
        DemoUser("employee@gmail.com", "employee123", "Yash", "EMP-4417", "employee"),
        DemoUser("admin@gmail.com", "admin123", "Ishan", "ADM-0001", "admin"),
    )
}

#: Committed on purpose. It makes tokens survive a reload during a demo; it does
#: not make them unforgeable, and it is not a secret in any meaningful sense.
_DEMO_TOKEN_SECRET: Final[bytes] = b"rxconcile-demo-not-a-secret"


def verify_credentials(email: str, password: str) -> DemoUser | None:
    user = DEMO_USERS.get(email.strip().lower())
    if user is None or not hmac.compare_digest(user.password, password):
        return None
    return user


def issue_token(email: str) -> str:
    """Bind a token to an email. The role is deliberately NOT in the payload."""
    signature = hmac.new(_DEMO_TOKEN_SECRET, email.encode(), sha256).hexdigest()[:32]
    return f"{email}.{signature}"


def email_from_token(token: str) -> str | None:
    """Recover the email a token was issued for, or None if it does not verify."""
    email, _, signature = token.rpartition(".")
    if not email or not signature:
        return None
    expected = hmac.new(_DEMO_TOKEN_SECRET, email.encode(), sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, signature):
        return None
    return email if email in DEMO_USERS else None


def user_from_token(token: str) -> DemoUser | None:
    email = email_from_token(token)
    return DEMO_USERS.get(email) if email else None
