"""SQLAlchemy 2.x data model (PostgreSQL/TimescaleDB in production, SQLite for the demo).

All timestamps are timezone-aware UTC. Raw samples are stored as received (immutable); quality
assessments and reconciled values are separate tables that reference them (data lineage).
No secrets or credentials are stored in plaintext (passwords as PBKDF2 hashes only)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Plant(Base):
    __tablename__ = "plants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    data_origin: Mapped[str] = mapped_column(String(32), default="SYNTHETIC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlantConfigVersion(Base):
    __tablename__ = "plant_config_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("plants.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64))
    author: Mapped[str] = mapped_column(String(100))
    comment: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("plant_id", "version"),)


class UserRole(Base):
    __tablename__ = "users_roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    role: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    demo_account: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TagDefinition(Base):
    __tablename__ = "tag_definitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(ForeignKey("plants.id"), index=True)
    tag: Mapped[str] = mapped_column(String(64))
    site_tag: Mapped[str] = mapped_column(String(128), default="")
    unit: Mapped[str] = mapped_column(String(32))
    definition: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("plant_id", "tag"),)


class RawSample(Base):
    __tablename__ = "raw_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingest_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_raw_samples_plant_ts", "plant_id", "ts"),)


class QualityAssessment(Base):
    __tablename__ = "quality_assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_sample_id: Mapped[int] = mapped_column(ForeignKey("raw_samples.id"), index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    score: Mapped[float] = mapped_column(Float)
    flags: Mapped[dict] = mapped_column(JSON)


class ReconciledSample(Base):
    __tablename__ = "reconciled_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_reconciled_plant_ts", "plant_id", "ts"),)


class MaterialAssay(Base):
    __tablename__ = "material_assays"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    material_id: Mapped[str] = mapped_column(String(64))
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    analysis: Mapped[dict] = mapped_column(JSON)


class ChargingEvent(Base):
    __tablename__ = "charging_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    charge_no: Mapped[int] = mapped_column(Integer)
    masses_t: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_charging_plant_ts", "plant_id", "ts"),)


class TappingEvent(Base):
    __tablename__ = "tapping_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ts_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cast_no: Mapped[int] = mapped_column(Integer)
    taphole: Mapped[int] = mapped_column(Integer)
    hot_metal_t: Mapped[float] = mapped_column(Float)
    slag_t: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    meta: Mapped[dict] = mapped_column(JSON)
    approval_status: Mapped[str] = mapped_column(String(40), default="NOT_APPROVED_FOR_PLANT_USE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CalibrationRun(Base):
    __tablename__ = "calibration_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    method: Mapped[str] = mapped_column(String(80))
    result: Mapped[dict] = mapped_column(JSON)


class ObserverSnapshot(Base):
    __tablename__ = "observer_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict] = mapped_column(JSON)


class SimulationJob(Base):
    __tablename__ = "simulation_jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    request: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class ScenarioInput(Base):
    __tablename__ = "scenario_inputs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("simulation_jobs.id"), index=True)
    spec: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))


class ScenarioResult(Base):
    __tablename__ = "scenario_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("simulation_jobs.id"), index=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON)


class MPCRecommendation(Base):
    __tablename__ = "mpc_recommendations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)


class RecommendationAcknowledgment(Base):
    __tablename__ = "recommendation_acknowledgments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("mpc_recommendations.id"), index=True)
    user: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alarm(Base):
    __tablename__ = "alarms"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    severity: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON)
    acknowledged_by: Mapped[str] = mapped_column(String(80), nullable=True)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[dict] = mapped_column(JSON)
    request_id: Mapped[str] = mapped_column(String(40), default="")


class DataRetentionPolicy(Base):
    __tablename__ = "data_retention_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(64), unique=True)
    retain_days: Mapped[int] = mapped_column(Integer)
    soft_delete: Mapped[bool] = mapped_column(Boolean, default=False)
