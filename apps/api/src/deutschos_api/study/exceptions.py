class StudyError(Exception):
    """Base class for known guided-study domain errors."""


class StudyNotFoundError(StudyError):
    pass


class StudyConflictError(StudyError):
    pass


class StudyContractError(StudyError):
    pass


class StudyLibraryUnavailableError(StudyError):
    pass
