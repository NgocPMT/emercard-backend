"""Safe domain errors for card persistence and lifecycle operations."""


class CardError(Exception):
    """Base class for card failures without sensitive detail."""


class CardNotFoundError(CardError):
    """The requested card does not exist."""


class CardUserNotFoundError(CardError):
    """The target card owner does not exist."""


class CardSerialConflictError(CardError):
    """A generated or supplied serial already exists."""


class CardTokenHashConflictError(CardError):
    """A generated token hash already exists."""


class CardIdentityConflictError(CardError):
    """MongoDB reported an identity conflict that could not be classified."""


class CardAlreadyAssignedError(CardError):
    """The card is already assigned or otherwise owned."""


class CardLinkAlreadyProvisionedError(CardError):
    """The card already has a link or cannot be provisioned in its state."""


class CardEncodingNotVerifiedError(CardError):
    """The card has not passed physical encoding verification."""


class CardEncodingMismatchError(CardError):
    """The read-back public link does not match the current card token."""


class CardAssignmentTargetInvalidError(CardError):
    """The requested account cannot receive an administrative card assignment."""


class CardReassignmentNotAllowedError(CardError):
    """The card cannot be corrected after issuance or activation."""


class CardAlreadyIssuedError(CardError):
    """The card has already left EmerCard custody."""


class CardOwnershipMismatchError(CardError):
    """The requested card is not owned by the expected user."""


class CardInvalidTransitionError(CardError):
    """The requested lifecycle transition is not allowed."""


class CardTerminalStateError(CardInvalidTransitionError):
    """The card is permanently lost or replaced."""


class CardInvariantError(CardError):
    """A card document or requested mutation violates a domain invariant."""


class CardProvisioningError(CardError):
    """Card identity provisioning did not complete successfully."""


class CardReplacementError(CardError):
    """Card replacement did not complete successfully."""
