"""Who is Zeev talking to right now -- the per-user isolation primitives.

Alex's data stays exactly where it always was (`data/zeev.db`); every other
household member gets their own SQLite file under `data/users/<slug>/`, so a
fact, message, reminder or note saved for one person can never be read back
for another. zeev.py owns the connections; this module owns only the
*identity*: a registry of known users and a ContextVar naming the active one.

Why a ContextVar and not a module global: the web server is a
ThreadingHTTPServer, so two people can be mid-turn at once and a global would
interleave them. Why `spawn()` exists: a plain `threading.Thread` starts with
an EMPTY context, i.e. the default user. A background job launched from
Maria's turn (memory extraction, detail pre-generation) would then quietly
read and write ALEX's data -- the exact bleed this whole module exists to
prevent. `spawn()` copies the caller's context so the job stays Maria's.
"""
import contextvars
import os
import re
import threading

DEFAULT_USER = "alex"

_SLUG_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _parse_registry(raw):
    """`alex:Alex,maria:Maria` -> {"alex": "Alex", "maria": "Maria"}.

    A bare slug (`alex,maria`) uses the capitalised slug as display name.
    Malformed entries are skipped, never fatal (import-time constraint, same
    as parse_subjects()). The default user is always present.
    """
    users = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        slug, _, name = part.partition(":")
        slug = slug.strip().lower()
        if not _SLUG_RE.fullmatch(slug):
            continue
        users[slug] = name.strip() or slug.capitalize()
    users.setdefault(DEFAULT_USER, DEFAULT_USER.capitalize())
    return users


def _parse_aliases(raw):
    """`azaurov=alex,maria.z=maria` -> login name -> slug (login names may
    contain characters a slug can't, so they are mapped rather than matched)."""
    out = {}
    for part in (raw or "").split(","):
        login, _, slug = part.partition("=")
        login, slug = login.strip().lower(), slug.strip().lower()
        if login and _SLUG_RE.fullmatch(slug):
            out[login] = slug
    return out


USERS = _parse_registry(os.environ.get("ZEEV_USERS", "alex:Alex,maria:Maria"))
ALIASES = _parse_aliases(os.environ.get("ZEEV_USER_ALIASES", ""))

_current = contextvars.ContextVar("zeev_user", default=DEFAULT_USER)


def resolve(name):
    """A login name / slug / display name -> a registered slug, or None.

    None (not the default user) for anything unrecognised: falling back to
    Alex would file a stranger's data under Alex's memory.
    """
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    if key in USERS:
        return key
    for slug, display in USERS.items():
        if display.lower() == key:
            return slug
    return None


def current():
    return _current.get()


def display_name(slug=None):
    return USERS.get(slug or current(), (slug or current()).capitalize())


def set_current(slug):
    """Make `slug` the active user in this context. Returns a reset token.
    Raises KeyError for an unregistered slug (never silently defaults)."""
    if slug not in USERS:
        raise KeyError(f"unknown user {slug!r}")
    return _current.set(slug)


def reset(token):
    _current.reset(token)


class as_user:
    """`with as_user("maria"): ...` -- scoped switch, restored on exit."""

    def __init__(self, slug):
        self.slug = slug
        self._tok = None

    def __enter__(self):
        self._tok = set_current(self.slug)
        return self.slug

    def __exit__(self, *exc):
        reset(self._tok)
        return False


def spawn(target, *args, daemon=True, name=None, **kwargs):
    """Start a thread that keeps the CALLER's active user.

    Use this (never a bare `threading.Thread`) for any background job that
    touches personal data and is launched from inside a turn.
    """
    ctx = contextvars.copy_context()
    opts = {"name": name} if name else {}
    t = threading.Thread(target=lambda: ctx.run(target, *args, **kwargs),
                         daemon=daemon, **opts)
    t.start()
    return t
