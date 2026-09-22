"""Workflows, their versioned graphs, and run traces.

The graph is JSONB rather than normalized node/edge tables: the canvas owns its
own shape, and nothing queries inside a graph. Run steps persist every node's
input and output, which is what makes a failed workflow debuggable (ADR 0004).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin

RUN_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")
STEP_STATUSES = ("pending", "running", "succeeded", "failed", "skipped")
TRIGGER_TYPES = ("manual", "schedule", "webhook")


class Workflow(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workflows"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Points at the version the canvas loads and runs.
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    versions: Mapped[list["WorkflowVersion"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )


class WorkflowVersion(UUIDMixin, TimestampMixin, Base):
    """An immutable snapshot of the graph. Editing creates a new version."""

    __tablename__ = "workflow_versions"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # {"nodes": [...], "edges": [...]} as the canvas serializes it.
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    workflow: Mapped[Workflow] = relationship(back_populates="versions")

    __table_args__ = (Index("ix_workflow_versions_unique", "workflow_id", "version", unique=True),)


class WorkflowRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Pinned so a run's trace stays meaningful after the graph is edited.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[Workflow] = relationship(back_populates="runs")
    steps: Mapped[list["WorkflowRunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="WorkflowRunStep.sequence"
    )

    __table_args__ = (Index("ix_workflow_runs_workflow_created", "workflow_id", "created_at"),)


class WorkflowRunStep(UUIDMixin, TimestampMixin, Base):
    """One node execution. Inputs and outputs are kept verbatim — this trace is
    the difference between a debuggable workflow engine and an opaque one."""

    __tablename__ = "workflow_run_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    run: Mapped[WorkflowRun] = relationship(back_populates="steps")

    __table_args__ = (Index("ix_workflow_run_steps_run_sequence", "run_id", "sequence"),)
