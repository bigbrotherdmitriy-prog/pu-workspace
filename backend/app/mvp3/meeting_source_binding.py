"""Exact meeting/source authority; no content reads, provider calls or owned commits."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.auth import ROLE_LEVEL
from app.core.v54_interfaces import RequestScope
from app.core.v54_refs import ObjectRef, VersionPin
from app.models.audit_log import AuditLog
from app.models.management import Meeting
from app.models.management_digest import ManagementProposalOrigin
from app.models.meeting_source_binding import MeetingSourceBinding
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceCurrent, SourceReference, SourceVersion
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied, ManagementLifecycle


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ManagementDenied("invalid_meeting_source") from None
    return value


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class MeetingSourceBindingService:
    def __init__(self, *, clock=lambda: datetime.now(timezone.utc)):
        self.clock = clock
        self.lifecycle = ManagementLifecycle()

    def _meeting(self, db, *, project_id, meeting_id, actor_user_id, minimum="editor", lock=True):
        # Shared transaction guard; refresh identities before trusting ACL/CAS.
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update()
            .execution_options(populate_existing=True))
        member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id,
            ProjectMember.user_id == actor_user_id).with_for_update().execution_options(populate_existing=True))
        if (project is None or project.archived_at is not None or member is None
                or type(actor_user_id) is not int or db.get(User, actor_user_id) is None
                or ROLE_LEVEL.get(member.role, 0) < ROLE_LEVEL[minimum]):
            raise ManagementDenied("resource_unavailable")
        query = select(Meeting).where(Meeting.id == meeting_id, Meeting.project_id == project_id)
        if lock:
            query = query.with_for_update()
        meeting = db.scalar(query.execution_options(populate_existing=True))
        if meeting is None:
            raise ManagementDenied("resource_unavailable")
        return meeting, self.lifecycle.scope(db, project_id=project_id, actor_user_id=actor_user_id, minimum=minimum)

    @staticmethod
    def _request(scope):
        tenant = {"kind": "int", "value": str(scope.organization_id)}
        return RequestScope(tenant=tenant,
            actor={"namespace": "pu", "type": "user", "tenant_id": tenant,
                "id": {"kind": "int", "value": str(scope.actor_user_id)}},
            project={"namespace": "pu", "type": "project", "tenant_id": tenant,
                "id": {"kind": "int", "value": str(scope.project_id)}},
            correlation_id="meeting-source-binding")

    def _retained_child(self, db, scope, evidence):
        """Authorize exact durable child metadata; never open original/child bytes."""
        from app.models.materialization import Materialization
        # Do not refresh over a caller's uncommitted child revocation. No new
        # child publication is part of this metadata-only meeting transaction.
        if any(isinstance(row, (Evidence, EvidenceAssessment, Materialization))
               for row in set(db.new) | set(db.dirty) | set(db.deleted)):
            raise ManagementDenied("resource_unavailable")
        with db.no_autoflush:
            return self._authorize_retained_child(db, scope, evidence)

    def _authorize_retained_child(self, db, scope, evidence):
        from app.source_evidence.fragment_reader import RepresentationDescriptor
        from app.source_evidence.xlsx_ingestion import require_local_xlsx_fragment
        from app.source_evidence.product import ProductEvidenceResolver
        from app.core.v54_interfaces import require_resolution

        try:
            request = self._request(scope)
            descriptor = RepresentationDescriptor.model_validate(evidence.representation_ref)
            pin = VersionPin(ref=ObjectRef(namespace="pu", type="evidence", tenant_id=request.tenant,
                id={"kind": "uuid", "value": evidence.id}), version_kind="revision", value=evidence.revision)
            source = db.get(SourceReference, evidence.source_id, populate_existing=True)
            version = db.get(SourceVersion, evidence.source_version_id, populate_existing=True)
            if source is None or version is None:
                raise ValueError
            source_ref = ObjectRef(namespace="pu", type="source", tenant_id=request.tenant,
                                  id={"kind": "uuid", "value": source.id})
            version_pin = VersionPin(ref=ObjectRef(namespace="pu", type="source_version", tenant_id=request.tenant,
                id={"kind": "uuid", "value": version.id}), version_kind="revision", value=version.revision)
            if (descriptor.evidence_pin != pin or descriptor.source_ref != source_ref
                    or descriptor.source_version_pin != version_pin):
                raise ValueError
            result = require_local_xlsx_fragment(db, scope=request, source=source, version=version,
                evidence=evidence, descriptor=descriptor, clock=self.clock)
            resolution = ProductEvidenceResolver(clock=self.clock).resolve(db, scope=request,
                pin=pin, operation="fragment", lock=False)
            require_resolution(resolution, scope=request, pin=pin, operation="fragment", now=self.clock())
            assessment = db.get(EvidenceAssessment, evidence.id)
            now = self.clock()
            if (resolution.version != "current" or assessment is None
                    or _utc(assessment.checked_at) is None or _utc(assessment.checked_at) > now
                    or _utc(evidence.extracted_at) is None or _utc(evidence.extracted_at) > now
                    or assessment.verification not in {"verified", "unverified"}
                    or assessment.verification == "verified" and (assessment.reviewed_by is None
                        or _utc(assessment.reviewed_at) is None or _utc(assessment.reviewed_at) > now)):
                raise ValueError
            return result
        except (ValueError, TypeError, AttributeError):
            raise ManagementDenied("resource_unavailable") from None

    def _source(self, db, scope, source_id, source_version_id, *, lock=True):
        with db.no_autoflush:
            return self._source_metadata(db, scope, source_id, source_version_id, lock=lock)

    def _source_metadata(self, db, scope, source_id, source_version_id, *, lock=True):
        _uuid(source_id); _uuid(source_version_id)
        # The shared production local-source boundary owns source ACL. There is
        # no fallback to a project role, a JSON label, or a synthetic grant.
        try:
            from app.source_evidence.local_source import require_local_upload_source
            request = self._request(scope)
            tenant = request.tenant
            source_ref = ObjectRef(namespace="pu", type="source", tenant_id=tenant,
                id={"kind": "uuid", "value": source_id})
            version_pin = VersionPin(ref=ObjectRef(namespace="pu", type="source_version", tenant_id=tenant,
                id={"kind": "uuid", "value": source_version_id}), version_kind="revision", value=1)
            result = require_local_upload_source(db, scope=request, source_ref=source_ref,
                source_version_pin=version_pin, operation="metadata", lock=lock,
                allow_purged_original=True, clock=self.clock)
            if result.original_state == "DERIVED":
                return result
            # The source/version association survives completed original purge
            # only while an independently authorized retained child exists.
            # This is not a claim that original bytes are still available.
            candidates = db.scalars(select(Evidence).where(Evidence.organization_id == scope.organization_id,
                Evidence.source_id == source_id, Evidence.source_version_id == source_version_id,
                Evidence.representation_ref.is_not(None)).order_by(Evidence.id).limit(514)).all()
            if len(candidates) > 513:
                raise ManagementDenied("source_catalog_capacity_exceeded")
            for evidence in candidates:
                try:
                    return self._retained_child(db, scope, evidence)
                except ManagementDenied:
                    continue
            raise ManagementDenied("resource_unavailable")
        except (ImportError, ValueError) as exc:
            raise ManagementDenied("invalid_meeting_source") from exc

    def edit(self, db, *, project_id, meeting_id, actor_user_id, expected_version, minutes, status):
        if type(expected_version) is not int or expected_version < 1:
            raise ManagementConflict("version_conflict")
        meeting, _ = self._meeting(db, project_id=project_id, meeting_id=meeting_id,
            actor_user_id=actor_user_id)
        if type(minutes) is not str or not 3 <= len(minutes.strip()) <= 50000 or status not in {"held", "completed", "cancelled"}:
            raise ManagementDenied("invalid_input")
        version = self.lifecycle._cas(db, Meeting, meeting.id, project_id, expected_version,
            minutes=minutes.strip(), status=status)
        db.add(AuditLog(action="meeting_minutes_recorded", entity_type="meeting", entity_id=meeting_id,
            details=f"version={version};user={actor_user_id}"))
        return db.get(Meeting, meeting_id)

    def bind(self, db, *, project_id, meeting_id, actor_user_id, expected_version,
             command_id, source_id, source_version_id):
        if type(expected_version) is not int or expected_version < 1:
            raise ManagementConflict("version_conflict")
        _uuid(command_id)
        meeting, scope = self._meeting(db, project_id=project_id, meeting_id=meeting_id,
            actor_user_id=actor_user_id, minimum="manager")
        self._source(db, scope, source_id, source_version_id)
        prior = db.scalar(select(MeetingSourceBinding).where(MeetingSourceBinding.meeting_id == meeting_id,
            MeetingSourceBinding.command_id == command_id))
        if prior is not None:
            if (prior.source_id != source_id or prior.source_version_id != source_version_id
                    or prior.bound_by_user_id != actor_user_id or prior.meeting_record_version != expected_version + 1
                    or prior.meeting_record_version != meeting.record_version or meeting.status != "completed"):
                raise ManagementConflict("version_conflict")
            return self.serialize(prior)
        if meeting.status != "completed":
            raise ManagementDenied("invalid_meeting_source")
        version = self.lifecycle._cas(db, Meeting, meeting.id, project_id, expected_version)
        binding = MeetingSourceBinding(organization_id=scope.organization_id, project_id=project_id,
            meeting_id=meeting_id, meeting_record_version=version, source_id=source_id,
            source_version_id=source_version_id, command_id=command_id, bound_by_user_id=actor_user_id)
        db.add(binding); db.flush()
        db.add(AuditLog(action="meeting_source_bound", entity_type="meeting", entity_id=meeting_id,
            details=f"binding_id={binding.id};version={version};user={actor_user_id}"))
        return self.serialize(binding)

    @staticmethod
    def serialize(binding):
        return {"meeting_id": binding.meeting_id, "meeting_record_version": binding.meeting_record_version,
            "binding_id": binding.id, "source_id": binding.source_id, "source_version_id": binding.source_version_id,
            "origin_status": "bound", "confirmation_available": True, "external_actions_created": False}

    def require(self, db, *, project_id, meeting_id, actor_user_id, binding_id, minimum="editor"):
        meeting, scope = self._meeting(db, project_id=project_id, meeting_id=meeting_id,
            actor_user_id=actor_user_id, minimum=minimum)
        if meeting.status != "completed":
            raise ManagementDenied("resource_unavailable")
        if binding_id is None:
            raise ManagementDenied("invalid_meeting_source")
        binding = db.get(MeetingSourceBinding, _uuid(binding_id), populate_existing=True)
        if (binding is None or binding.project_id != project_id or binding.meeting_id != meeting_id
                or binding.organization_id != scope.organization_id or meeting.status != "completed"
                or binding.meeting_record_version != meeting.record_version):
            raise ManagementDenied("invalid_meeting_source")
        self._source(db, scope, binding.source_id, binding.source_version_id)
        return binding, scope

    def evidence(self, db, scope, binding, raw_pins):
        source_binding = self._source(db, scope, binding.source_id, binding.source_version_id)
        for raw in raw_pins:
            try:
                pin = VersionPin.model_validate(raw)
            except ValueError as exc:
                raise ManagementDenied("resource_unavailable") from exc
            if pin.ref.type != "evidence":
                raise ManagementDenied("resource_unavailable")
            evidence = db.get(Evidence, pin.ref.id.value, populate_existing=True)
            assessment = db.scalar(select(EvidenceAssessment).where(EvidenceAssessment.evidence_id == pin.ref.id.value)
                .with_for_update().execution_options(populate_existing=True))
            if (evidence is None or assessment is None or evidence.source_id != binding.source_id
                    or evidence.source_version_id != binding.source_version_id
                    or assessment.organization_id != scope.organization_id
                    or _utc(assessment.valid_until) is None or _utc(assessment.valid_until) <= self.clock()):
                raise ManagementDenied("resource_unavailable")
            if source_binding.original_state == "PURGED" or evidence.extractor.get("name") == "xlsx_cells":
                self._retained_child(db, scope, evidence)
        return self.lifecycle.evidence(db, scope, raw_pins)

    def require_entity(self, db, *, project_id, actor_user_id, entity_type, entity_id, versioned=True):
        links = db.scalars(select(ManagementProposalOrigin).where(
            ManagementProposalOrigin.project_id == project_id, ManagementProposalOrigin.entity_type == entity_type,
            ManagementProposalOrigin.entity_id == entity_id, ManagementProposalOrigin.origin_type == "meeting")
            .order_by(ManagementProposalOrigin.origin_id)).all()
        for link in links:
            if link.meeting_source_binding_id is None:
                raise ManagementDenied("invalid_meeting_source")
            binding, scope = self.require(db, project_id=project_id, meeting_id=link.origin_id,
                actor_user_id=actor_user_id, binding_id=link.meeting_source_binding_id, minimum="manager")
            from app.models.management import Obligation
            from app.models.governance import Decision
            row = db.get(Obligation if entity_type == "obligation" else Decision, entity_id, populate_existing=True)
            if row is None or row.project_id != project_id or row.evidence_pins != link.evidence_pins:
                raise ManagementDenied("resource_unavailable")
            self.evidence(db, scope, binding, link.evidence_pins)
        if links and not versioned:
            raise ManagementConflict("meeting_confirmation_requires_versioned_api")
        return binding if links else None

    def origin(self, db, *, project_id, meeting_id, actor_user_id):
        meeting, scope = self._meeting(db, project_id=project_id, meeting_id=meeting_id,
            actor_user_id=actor_user_id, minimum="viewer")
        binding = db.scalar(select(MeetingSourceBinding).where(MeetingSourceBinding.meeting_id == meeting_id,
            MeetingSourceBinding.meeting_record_version == meeting.record_version))
        if binding is not None and meeting.status == "completed":
            try:
                self._source(db, scope, binding.source_id, binding.source_version_id)
                return self.serialize(binding)
            except ManagementDenied:
                pass
        return {"meeting_id": meeting_id, "meeting_record_version": meeting.record_version,
            "origin_status": "invalid_source", "origin_reason": "meeting_source_binding_required",
            "confirmation_available": False}

    def eligible(self, db, *, project_id, meeting_id, actor_user_id):
        meeting, scope = self._meeting(db, project_id=project_id, meeting_id=meeting_id,
            actor_user_id=actor_user_id, minimum="manager")
        sources = []
        candidates = db.scalars(select(SourceCurrent).join(SourceReference, SourceReference.id == SourceCurrent.source_id)
            .where(SourceCurrent.organization_id == scope.organization_id, SourceReference.origin_project_id == project_id)
            .order_by(SourceCurrent.source_id).limit(501)).all()
        if len(candidates) > 500:
            raise ManagementDenied("source_catalog_capacity_exceeded")
        for current in candidates:
            try:
                self._source(db, scope, current.source_id, current.version_id)
            except ManagementDenied:
                continue
            pins = []
            for evidence in db.scalars(select(Evidence).where(Evidence.organization_id == scope.organization_id,
                    Evidence.source_id == current.source_id, Evidence.source_version_id == current.version_id)):
                raw = {"ref": {"namespace": "pu", "type": "evidence", "tenant_id": {"kind": "int", "value": str(scope.organization_id)},
                    "id": {"kind": "uuid", "value": evidence.id}}, "version_kind": "revision", "value": evidence.revision}
                try:
                    self.evidence(db, scope, evidence, [raw])
                    pins.append(raw)
                except ManagementDenied:
                    continue
            sources.append({"source_id": current.source_id, "source_version_id": current.version_id, "evidence_pins": pins})
        return {"meeting_id": meeting_id, "meeting_record_version": meeting.record_version,
            "sources": sources, "external_actions_created": False}
