"""LLM console/file diagnostics with metadata only, never request/response text."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import re
from urllib.parse import urlsplit

LOGGER = logging.getLogger("pawapodiff.llm")
CONTEXT = ContextVar("llm_diagnostic_context", default={})


@contextmanager
def logging_context(**fields):
    token = CONTEXT.set({**CONTEXT.get(), **fields})
    try:
        yield
    finally:
        CONTEXT.reset(token)


def origin(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "invalid"
        if ":" in host:
            host = "[" + host + "]"
        return f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "")
    except ValueError:
        return "invalid"


def event(level, name, *, secret="", **metadata):
    # Only explicit metadata is passed here; never pass bodies or exception text.
    fields = {**CONTEXT.get(), **metadata}

    def safe(value):
        text = str(value)
        if secret:
            text = text.replace(secret, "[redacted]")
        return re.sub(r"[\x00-\x1f\x7f]", "?", text)[:240]

    message = " ".join(
        f"{key}={json.dumps(safe(value), ensure_ascii=False)}"
        for key, value in fields.items()
    )
    LOGGER.log(level, "[LLM] %s %s", name, message)


class LLMError(ValueError):
    def __init__(self, code, message, *, request_id=None):
        self.code, self.request_id = code, request_id
        super().__init__(
            f"[{code}"
            + (f" / request={request_id}" if request_id else "")
            + f"] {message}"
        )
