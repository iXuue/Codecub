from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .contracts import Origin


class ResourcePools:
    """Separate execution capacity so system work cannot starve users."""

    def __init__(self, user_workers: int = 4, system_workers: int = 2):
        self.user = ThreadPoolExecutor(max_workers=user_workers, thread_name_prefix="codecub-user")
        self.system = ThreadPoolExecutor(max_workers=system_workers, thread_name_prefix="codecub-system")

    def submit(self, origin: Origin, fn, *args, **kwargs):
        pool = self.user if origin is Origin.USER else self.system
        return pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self.user.shutdown(wait=True)
        self.system.shutdown(wait=True)
