"""Errors shared across modules: the AWS error code of an exception, and input the user must fix."""


class InputError(ValueError):
    """Input the user must fix; the HTTP routers answer 422 with this message (routes.install_error_handlers)."""


def error_code(exc):
    """The AWS error code of a ClientError (AccessDenied, ThrottlingException...), else the exception's class name."""
    from botocore.exceptions import ClientError  # lazy: the draw.io modules use InputError without the AWS SDK
    if isinstance(exc, ClientError):
        return (exc.response.get("Error") or {}).get("Code") or type(exc).__name__
    return type(exc).__name__
