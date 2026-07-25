"""Model package. Importing this module registers all tables on Base.metadata."""
from backend.models.base import Base
from backend.models.chat_message import ChatMessage
from backend.models.chat_session import ChatSession
from backend.models.project import Project
from backend.models.requirement import (
    ChangedBy,
    Priority,
    RelationKind,
    RelationStatus,
    ReqStatus,
    ReqType,
    RequirementItem,
    RequirementRelation,
    RequirementRevision,
)
from backend.models.user import User

__all__ = [
    "Base",
    "User",
    "Project",
    "ChatSession",
    "ChatMessage",
    "RequirementItem",
    "RequirementRelation",
    "RequirementRevision",
    "ReqType",
    "Priority",
    "ReqStatus",
    "RelationKind",
    "RelationStatus",
    "ChangedBy",
]
