from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class AICoordinator:
    """Cooperative priority signal; background work yields between atomic units."""

    def __init__(self) -> None:
        self._interactive = 0
        self._available = asyncio.Event()
        self._available.set()

    @property
    def interactive_active(self) -> bool:
        return self._interactive > 0

    async def wait_for_interactive_idle(self) -> None:
        await self._available.wait()

    @asynccontextmanager
    async def interactive(self):
        self._interactive += 1
        self._available.clear()
        try:
            yield
        finally:
            self._interactive -= 1
            if self._interactive == 0:
                self._available.set()


ai_coordinator = AICoordinator()
