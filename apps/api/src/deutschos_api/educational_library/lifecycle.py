from __future__ import annotations

import asyncio
import logging

from deutschos_api.core.config import Settings

from .schemas import LibraryBusyError, LibraryContractError
from .service import EducationalLibraryService

logger = logging.getLogger(__name__)


async def library_polling_loop(settings: Settings) -> None:
    if not settings.educational_library_scan_on_startup:
        return
    while True:
        try:
            service = EducationalLibraryService(settings)
            await asyncio.to_thread(service.scan, process_documents=True)
        except LibraryBusyError:
            logger.info("educational library scan already running")
        except LibraryContractError:
            logger.warning("educational library source root is unavailable")
        except Exception as exc:
            logger.error("educational library scan failed: %s", type(exc).__name__)
        await asyncio.sleep(settings.educational_library_scan_interval_seconds)
