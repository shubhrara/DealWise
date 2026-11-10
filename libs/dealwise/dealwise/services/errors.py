class DealWiseError(Exception):
    """Base class; messages are safe to show to end users."""


class NotFound(DealWiseError):
    """Missing OR not visible to the caller (indistinguishable on purpose)."""


class Forbidden(DealWiseError):
    """Visible to the caller but the action is not permitted."""


class ValidationError(DealWiseError):
    pass


class Conflict(DealWiseError):
    pass


class DuplicateDocument(Conflict):
    pass
