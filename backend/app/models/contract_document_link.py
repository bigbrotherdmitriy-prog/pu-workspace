from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ContractDocumentLink(Base):
    __tablename__ = "contract_document_links"
    __table_args__ = (UniqueConstraint("contract_id", "document_id", name="uq_contract_document_link"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="application", server_default="application")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
