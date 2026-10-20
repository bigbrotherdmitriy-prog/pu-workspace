from app.models.project import Project
from app.models.user import User
from app.models.project_member import ProjectMember
from app.models.dds_plan_version import DdsPlanVersion
from app.models.project_service_settings import ProjectServiceSettings
from app.models.drive_connection import DriveConnection
from app.models.document import Document
from app.models.google_token import GoogleOAuthToken
from app.models.integration_credential import IntegrationCredential
from app.models.storage_oauth_state import StorageOAuthState
from app.models.ai_cache import AIAnalysisCache
from app.models.document_version import DocumentVersion
from app.models.audit_log import AuditLog
from app.models.organizer import (
    OrganizerAction,
    OrganizerOperation,
    OrganizerProposal,
    OrganizerRule,
    OrganizerSession,
)
from app.models.auth_session import AuthSession
from app.models.task import Task, TaskDueDateHistory, TaskHistory
from app.models.response_draft import ResponseDraft
from app.models.automation_rule import AutomationRule, AutomationRun
from app.models.telegram_chat import TelegramChatLink
from app.models.governance import Decision, Risk
from app.models.workspace import ExtractionResult, SourceFolder, VirtualNode, WorkspaceSnapshot
from app.models.organization_contract import Contract, ContractVersion, Organization
from app.models.ai_secretary import Message
from app.models.ai_policy import ProjectAIPolicy
from app.models.management import (
    BookableResource, ManagementHistory, Meeting, MeetingParticipant,
    ManagementDigest, MeetingProposal, MeetingResource, MeetingSourceBinding,
    Notification, NotificationPolicy, Obligation,
)
from app.models.saved_search_view import SavedSearchView
from app.models.execution_finance import (
    AcceptanceAct, BudgetLine, CashFlowEntry, CashFlowPlanMutation, ContractBudgetProposal, CostCategory, DdsArticleBudgetOperation, DdsBudgetLinkOperation, InvoiceExtractionProposal,
    PaymentEvent, ProcurementItem, ScheduleBaseline, ScheduleItem,
)
from app.models.external_resource import ExternalResourceLink
from app.models.project_contact import ContactConflict, ProjectContact
from app.models.task_completion_suggestion import TaskCompletionSuggestion
from app.models.contract_document_link import ContractDocumentLink
from app.models.contract_stage import ContractStage
from app.models.job import BackgroundJob, ServiceHeartbeat
from app.models.notification_device import UserDeviceToken
from app.models.mail_settings import MailUserSettings
from app.models.v54_pilot import (  # additive, inactive pilot foundation
    ConnectionIdentity, MailConnection, SourceReference, SourceVersion, SourceCurrent,
    Evidence, EvidenceAssessment, DeadlineClaim, ContextRelation, ActionPolicy,
    PilotAction, ActionRevision, ActionApproval, ActionReceipt, PendingDispatch, AuditExtension,
)
from app.models.v54_authority import AuthorityState
from app.models.mailbox_identity import (
    MailboxAuthorityState, MailboxCredentialGeneration, MailboxCutoverFlags,
    MailboxProjectCohort,
    MailboxOriginBinding, MailboxOriginCurrent, MailboxOriginDecision,
)
from app.models.materialization import Materialization
from app.models.v54_provider_action import (
    ProviderAction, ProviderActionApproval, ProviderDispatchOutbox,
    ProviderExecutionAttempt, ProviderOutcomeObservation,
)
from app.models.mobile_sync import MobileSyncCommand, MobileSyncConflict
from app.models.workspace_tenant import TenantWorkspace, TenantWorkspaceMember

__all__ = [
    "Project",
    "User",
    "ProjectMember",
    "ProjectServiceSettings",
    "DriveConnection",
    "Document",
    "GoogleOAuthToken",
    "IntegrationCredential",
    "StorageOAuthState",
    "DocumentVersion",
    "AuditLog",
    "OrganizerSession",
    "OrganizerProposal",
    "OrganizerAction",
    "OrganizerOperation",
    "OrganizerRule",
    "AuthSession",
    "Task",
    "TaskDueDateHistory",
    "TaskHistory",
    "ResponseDraft",
    "AutomationRule",
    "AutomationRun",
    "TelegramChatLink",
    "Risk",
    "Decision",
    "SourceFolder",
    "WorkspaceSnapshot",
    "VirtualNode",
    "ExtractionResult",
    "Organization",
    "Contract",
    "ContractVersion",
    "Message",
    "ProjectAIPolicy",
    "Obligation",
    "Meeting",
    "MeetingParticipant",
    "MeetingSourceBinding",
    "MeetingProposal",
    "BookableResource",
    "MeetingResource",
    "Notification",
    "NotificationPolicy",
    "ManagementDigest",
    "ManagementHistory",
    "SavedSearchView",
    "ScheduleBaseline",
    "ScheduleItem",
    "BudgetLine",
    "CashFlowEntry",
    "CashFlowPlanMutation",
    "DdsArticleBudgetOperation",
    "DdsBudgetLinkOperation",
    "CostCategory",
    "ContractBudgetProposal",
    "InvoiceExtractionProposal",
    "PaymentEvent",
    "ProcurementItem",
    "AcceptanceAct",
    "ExternalResourceLink",
    "ProjectContact",
    "ContactConflict",
    "TaskCompletionSuggestion",
    "ContractDocumentLink",
    "ContractStage",
    "BackgroundJob",
    "ServiceHeartbeat",
    "MailUserSettings",
    "AuthorityState",
    "MailboxAuthorityState", "MailboxCredentialGeneration", "MailboxCutoverFlags",
    "MailboxProjectCohort",
    "MailboxOriginBinding", "MailboxOriginCurrent", "MailboxOriginDecision",
    "Materialization",
    "ProviderAction", "ProviderActionApproval", "ProviderDispatchOutbox",
    "ProviderExecutionAttempt", "ProviderOutcomeObservation",
    "MobileSyncCommand", "MobileSyncConflict",
    "UserDeviceToken",
    "TenantWorkspace",
    "TenantWorkspaceMember",
    "DdsPlanVersion",
]

# Registers the Notification -> push outbox hook wherever models are loaded
# (API, worker, scheduler), so no notification path can bypass it.
import app.notification_push  # noqa: E402,F401
