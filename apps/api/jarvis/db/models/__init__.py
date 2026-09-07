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
from jarvis.db.models.chat import ChatMessage, Conversation
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
    PendingLogin,
    RefreshToken,
    User,
)
from jarvis.db.models.job import Job, JobStatus
from jarvis.db.models.llm import ExtractionCache, LLMCall, ProviderHealth
from jarvis.db.models.ops import (
    ActivitySample,
    Artifact,
    AuditLog,
    ChatFeedback,
    Device,
    DeviceConnection,
    Entity,
    EntityAlias,
    Memory,
    NotificationEndpoint,
    Profile,
    Relation,
    Routine,
    Schedule,
    SettingOverride,
    StandingPermission,
    WorkerHeartbeat,
)
from jarvis.db.models.source import (
    ConnectorCursor,
    Event,
    SourceAccount,
    SourceObject,
)

__all__ = [
    "ChatFeedback",
    "Profile",
    "WorkerHeartbeat",
    "Action",
    "ActionStatus",
    "AgentRun",
    "Approval",
    "ActivitySample",
    "Artifact",
    "AuditLog",
    "ChatMessage",
    "Conversation",
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
    "PendingLogin",
    "ProviderHealth",
    "RefreshToken",
    "Relation",
    "Routine",
    "Risk",
    "RunState",
    "RunStatus",
    "Schedule",
    "SettingOverride",
    "SourceAccount",
    "SourceObject",
    "StandingPermission",
    "Task",
    "TaskDependency",
    "User",
    "Verdict",
    "WorkSession",
]
