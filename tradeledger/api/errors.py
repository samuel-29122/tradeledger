"""One error format for the whole API.

Every error response has the same shape and never contains stack traces,
SQL, file paths or secrets:

    {"error": {"code": "validation_error",
               "message": "The submitted trade is invalid.",
               "details": {"position_size": "must be greater than zero"}}}

`details` is present only when there is something useful to say about
specific fields.
"""

from flask import current_app, jsonify, request


class ApiError(Exception):
    """Raise anywhere in an API request to return a JSON error."""

    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def error_response(status, code, message, details=None):
    body = {"code": code, "message": message}
    if details:
        body["details"] = details
    # Log what happened, never the request body (it may hold trading data).
    log = current_app.logger.warning if status >= 500 else current_app.logger.info
    log("API %s %s -> %s %s", request.method, request.path, status, code)
    return jsonify(error=body), status


def is_api_request():
    return request.path.startswith("/api/")


# Status codes Flask/Werkzeug raise before our code runs (unknown URL,
# wrong method, body too large), mapped to API error codes.
HTTP_ERRORS = {
    404: ("not_found", "The requested resource does not exist."),
    405: ("method_not_allowed", "This HTTP method is not supported for this URL."),
    413: ("payload_too_large", "The request body is too large."),
    500: ("internal_error", "An unexpected error occurred."),
}
