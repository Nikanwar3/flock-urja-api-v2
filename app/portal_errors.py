"""Exceptions raised by the portal adapter, translated to HTTP responses in main.py."""


class PortalError(Exception):
    """Base class for anything that went wrong talking to the portal."""


class PortalUnavailableError(PortalError):
    """The portal is unreachable, timed out, or returned a 5xx — nothing we
    can do but ask the caller to retry later."""


class PortalAuthError(PortalError):
    """Login failed outright (bad credentials) — not a transient thing."""


class MeterNotFoundError(PortalError):
    def __init__(self, meter_id: str):
        self.meter_id = meter_id
        super().__init__(f"Meter not found: {meter_id}")
