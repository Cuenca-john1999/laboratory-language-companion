from fastapi import Depends
from sqlalchemy.orm import Session

from llc_api.db.session import get_db
from llc_api.educational_library.dependencies import get_library_editorial
from llc_api.educational_library.editorial import LibraryEditorialService

from .service import GuidedStudyService


def get_study_service(
    db: Session = Depends(get_db),
    editorial: LibraryEditorialService = Depends(get_library_editorial),
) -> GuidedStudyService:
    return GuidedStudyService(db, editorial)
