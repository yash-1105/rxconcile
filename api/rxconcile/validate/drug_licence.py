"""Drug licence number checks for an Indian pharmacy bill.

.. warning::

   **There is no national format and no checksum.** Drug licences are issued
   under the Drugs and Cosmetics Rules by 36 separate state and union-territory
   authorities, each with its own convention. ``TN/2019/337821``,
   ``KA-B-21/1234``, ``20B/MH/1998/554`` and ``DL-20B-441`` are all plausible
   and none can be told apart from a typo by pattern alone.

   So no pattern validation is attempted here, deliberately. **Rejecting a
   valid licence is worse than not checking one**: it would put a real
   compliance accusation against a pharmacy on the basis of a format this
   system invented.

The only thing that can be said with certainty is whether a number is printed
at all. Rule 65 of the Drugs and Cosmetics Rules requires a retail sale to be
made under a licence, and the number appears on a compliant invoice.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LicenceCheck(BaseModel):
    """The outcome of checking a printed drug licence number.

    There is deliberately no ``valid`` field. Presence is the only property
    that can be established without a state registry, and offering a validity
    verdict would imply one was consulted.
    """

    model_config = ConfigDict(frozen=True)

    raw: str | None = Field(default=None, description="As printed on the bill.")
    present: bool = Field(
        default=False, description="Whether any licence number was printed at all."
    )
    note: str = Field(
        default="",
        description="Always states that no format check was performed, so a caller "
        "cannot read presence as validation.",
    )


NO_FORMAT_CHECK: str = (
    "Presence only. Indian drug licence numbers have no national format and no "
    "checksum, so no format validation was attempted and none should be inferred."
)


def check_licence(raw: str | None) -> LicenceCheck:
    """Report whether a drug licence number is printed. Nothing more."""
    present = bool(raw and raw.strip())
    return LicenceCheck(raw=raw, present=present, note=NO_FORMAT_CHECK)
