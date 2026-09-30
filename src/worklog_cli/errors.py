"""Expected, actionable failures exposed by the CLI."""


class WorklogError(Exception):
    """A user or ledger validation error."""

    def __init__(self, message: str, code: str = "validation_error") -> None:
        super().__init__(message)
        self.code = code
