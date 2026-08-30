"""SQLAlchemy models.

Imported for their side effect of registering with ``Base.metadata`` — Alembic
autogenerate only sees what has been imported.
"""

from jarvis.db.models.agent import (
    Action,
    ActionStatus,
    AgentRun,
    Approval,
    Evidence,
    Risk,
    RunState,
    RunStatus,
    Verdict,
)
from jarvis.db.models.domain import (
    Goal,
    GoalPrediction,
    Task,
    TaskDependency,
    WorkSession,
)
from jarvis.db.models.identity import (
    Identity,
    OAuthClient,
    OAuthCode,
    RefreshToken,
    User,
)
from jarvis.db.models.job import Job, JobStatus
from jarvis.db.models.llm import ExtractionCache, LLMCall, ProviderHealth
from jarvis.db.models.ops import (
    AuditLog,
    Device,
    DeviceConnection,
    Entity,
    EntityAlias,
    Memory,
    NotificationEndpoint,
    Relation,
    Schedule,
    StandingPermission,
)
from jarvis.db.models.source import (
    ConnectorCursor,
    Event,
    SourceAccount,
    SourceObject,
)

__all__ = [
    "Action",
    "ActionStatus",
    "AgentRun",
    "Approval",
    "AuditLog",
    "ConnectorCursor",
    "Device",
    "DeviceConnection",
    "Entity",
    "EntityAlias",
    "Event",
    "Evidence",
    "ExtractionCache",
    "Goal",
    "GoalPrediction",
    "Identity",
    "Job",
    "JobStatus",
    "LLMCall",
    "Memory",
    "NotificationEndpoint",
    "OAuthClient",
    "OAuthCode",
    "ProviderHealth",
    "RefreshToken",
    "Relation",
    "Risk",
    "RunState",
    "RunStatus",
    "Schedule",
    "SourceAccount",
    "SourceObject",
    "StandingPermission",
    "Task",
    "TaskDependency",
    "User",
    "Verdict",
    "WorkSession",
]
