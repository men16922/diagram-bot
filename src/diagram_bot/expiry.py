"""Expiry for the in-memory maps that live as long as the server (sessions, login failures, SSO flows): expired entries
are dropped when the owner touches the map, at most once per `every` seconds, with no timer thread."""


class Purge:
    def __init__(self, expired, every=60):
        # expired(entry, now) -> True when the entry can go
        self.expired, self.every, self.last = expired, every, None

    def __call__(self, entries, now):
        """Call under the lock that guards `entries`."""
        if self.last is not None and now - self.last < self.every:
            return
        self.last = now
        for key in [k for k, v in entries.items() if self.expired(v, now)]:
            del entries[key]
