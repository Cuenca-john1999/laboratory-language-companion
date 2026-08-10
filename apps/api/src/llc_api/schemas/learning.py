"""Compatibility aliases for the provisional Milestone 0 schema path."""

from llc_api.learning_engine.schemas import (
    AttemptCreate,
    AttemptReceipt,
    CurriculumRead,
    CurriculumSkillRead,
    DailyPlanBlockRead,
    DailyPlanCreate,
    DailyPlanRead,
    LearningSkillRead,
    MasteryCriteriaRead,
    ReviewRead,
    ReviewsRead,
    StudentSkillStateRead,
)

EvaluatedAttemptCreate = AttemptCreate
EvidenceReceipt = AttemptReceipt
SkillRead = LearningSkillRead
StudentSkillState = StudentSkillStateRead
LearningPlan = DailyPlanRead

__all__ = [
    "AttemptCreate",
    "AttemptReceipt",
    "CurriculumRead",
    "CurriculumSkillRead",
    "DailyPlanBlockRead",
    "DailyPlanCreate",
    "DailyPlanRead",
    "EvaluatedAttemptCreate",
    "EvidenceReceipt",
    "LearningPlan",
    "LearningSkillRead",
    "MasteryCriteriaRead",
    "ReviewRead",
    "ReviewsRead",
    "SkillRead",
    "StudentSkillState",
    "StudentSkillStateRead",
]
