"""UI language of the current request ("lt" | "en"); every user-facing text goes through L()."""
import contextvars


LANG = contextvars.ContextVar("lang", default="lt")


def L(lt, en):
    return en if LANG.get() == "en" else lt
