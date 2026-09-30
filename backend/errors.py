"""Errors the API turns into HTTP answers (502 / 404)."""


class UpstreamError(Exception):
    pass


class NotFound(Exception):
    pass
