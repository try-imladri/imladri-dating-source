"""Shared outcome types without application or transport dependencies."""


class GatewayCommittedResultUnavailable(RuntimeError):
    """A prior mutation committed; its private image was deliberately not cached."""
