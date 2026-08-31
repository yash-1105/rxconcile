"""GSTIN format and checksum validation.

.. warning::

   **This proves a number is well-formed. It does not prove the business
   exists, is registered, or is active.** Live verification requires the GST
   portal API and is out of scope. Every message this produces must read as a
   statement about the FORMAT of a printed number -- never as the result of a
   lookup that did not happen.

A GSTIN is 15 characters:

===========  ==============================================================
Position     Meaning
===========  ==============================================================
1-2          State code, 01-38
3-12         The holder's 10-character PAN: 5 letters, 4 digits, 1 letter
13           Entity number for that PAN within the state, 1-9 or A-Z
14           Literal ``Z``
15           Check digit, modulus 36 over the first 14 characters
===========  ==============================================================
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

#: Base-36 alphabet the checksum is computed over, in value order.
ALPHABET: Final[str] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GSTIN_LENGTH: Final[int] = 15

#: 5 letters, 4 digits, 1 letter -- the PAN embedded at positions 3-12.
GSTIN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
)

#: Statutory state and union-territory codes. 01-38 are the states; a GSTIN
#: printed with anything else is not well-formed.
STATE_CODES: Final[dict[str, str]] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli",
    "27": "Maharashtra", "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh",
}


class GstinCheck(BaseModel):
    """The outcome of checking one printed GSTIN."""

    model_config = ConfigDict(frozen=True)

    raw: str | None = Field(default=None, description="As printed on the bill.")
    normalised: str | None = Field(default=None, description="Upper-cased, spaces removed.")
    present: bool = False
    well_formed: bool = Field(
        default=False,
        description="Length, character pattern, state code and check digit all pass. "
        "**Says nothing about whether the business is registered.**",
    )
    reason: str | None = Field(
        default=None, description="Why it is not well-formed, in plain words."
    )
    state_code: str | None = None
    state_name: str | None = None
    expected_check_digit: str | None = Field(
        default=None, description="What the check digit should have been."
    )


def gstin_check_digit(first_fourteen: str) -> str:
    """The modulus-36 check digit for the first 14 characters.

    Each character's base-36 value is multiplied by an alternating factor of 1
    and 2. The quotient and remainder of that product against 36 are BOTH added
    to the running sum -- which is what makes the digit sensitive to a
    transposition, not just to a substitution.

    Raises:
        ValueError: a character outside the base-36 alphabet.
    """
    total = 0
    for index, char in enumerate(first_fourteen):
        value = ALPHABET.find(char)
        if value < 0:
            raise ValueError(f"{char!r} is not a base-36 character")
        product = value * (2 if index % 2 else 1)
        total += product // len(ALPHABET) + product % len(ALPHABET)
    return ALPHABET[(len(ALPHABET) - (total % len(ALPHABET))) % len(ALPHABET)]


def check_gstin(raw: str | None) -> GstinCheck:
    """Check a printed GSTIN's format and checksum.

    A null or blank value is reported as absent rather than invalid: nothing
    was printed, so nothing failed.
    """
    if raw is None or not raw.strip():
        return GstinCheck(raw=raw, present=False)

    normalised = re.sub(r"[\s-]", "", raw).upper()
    base = GstinCheck(raw=raw, normalised=normalised, present=True)

    if len(normalised) != GSTIN_LENGTH:
        return base.model_copy(update={
            "reason": f"{len(normalised)} characters, expected {GSTIN_LENGTH}",
        })
    if not GSTIN_PATTERN.match(normalised):
        return base.model_copy(update={
            "reason": "does not follow the state-code, PAN, entity, Z, check-digit pattern",
        })

    state_code = normalised[:2]
    state_name = STATE_CODES.get(state_code)
    if state_name is None:
        return base.model_copy(update={
            "state_code": state_code,
            "reason": f"state code {state_code} is not one of the 38 statutory codes",
        })

    expected = gstin_check_digit(normalised[:14])
    if expected != normalised[14]:
        return base.model_copy(update={
            "state_code": state_code,
            "state_name": state_name,
            "expected_check_digit": expected,
            "reason": (
                f"check digit is {normalised[14]!r} but the first 14 characters "
                f"compute to {expected!r}"
            ),
        })

    return base.model_copy(update={
        "well_formed": True,
        "state_code": state_code,
        "state_name": state_name,
        "expected_check_digit": expected,
    })


def state_in_address(address: str | None) -> str | None:
    """The statutory state named in an address, if exactly one is.

    Returns None when the address is absent, names no state, or names more than
    one -- an ambiguous address is not evidence of a mismatch.
    """
    if not address:
        return None
    haystack = address.upper()
    found = {name for name in STATE_CODES.values() if name.upper() in haystack}
    return next(iter(found)) if len(found) == 1 else None
