# db/models.py
from datetime import datetime, date
from sqlalchemy import (
    Column, String, Text, Integer, BigInteger, ForeignKey, Date,
    Enum, JSON, text, DateTime, UniqueConstraint, Float)
from sqlalchemy.orm import DeclarativeBase, Mapped, relationship, declarative_base
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.schema import MetaData
import uuid

class Base(DeclarativeBase):
    pass

Base = declarative_base(metadata=MetaData(schema="public"))
# ---------- hierarchy -----------------------------------------------------
class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    sender_id = Column(String, nullable=True)
    location = Column(String, nullable=True)
    no_of_blocks = Column(Integer, nullable=True)
    floors_per_block = Column(Integer, nullable=True)
    flats_per_floor = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class Flat(Base):
    __tablename__ = "flats"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    block_name = Column(String, nullable=True)
    floor_no = Column(Integer, nullable=False)
    flat_no = Column(Integer, nullable=False)
    bhk_type = Column(Text, nullable=False)
    facing = Column(Text)
    carpet_sft = Column(Integer)

    project = relationship("Project", backref="flats")
    regions = relationship("Region", back_populates="flat")

    __table_args__ = (
        UniqueConstraint('project_id', 'block_name', 'floor_no', 'flat_no', name='uq_flat_identity'),
    )

class Region(Base):
    __tablename__ = "regions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_id = Column(Text, unique=True)
    code = Column(Text, nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    flat_id = Column(BigInteger, ForeignKey("flats.id", ondelete="CASCADE"), nullable=True)
    block_name = Column(Text, nullable=True)
    floor_no = Column(BigInteger, nullable=True)
    meta = Column(JSON)
    flat = relationship("Flat", back_populates="regions")
    project = relationship("Project", backref="regions")
 
class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    region_id = Column(UUID(as_uuid=True), ForeignKey("regions.id", ondelete="SET NULL"))
    
    task_type = Column(String, nullable=False)  # e.g., "Plastering", "Wiring"
    status = Column(String, default="Not Started")  # "Not Started", "In Progress", "Done" → validate in Python
    department = Column(String, nullable=True)
    dynamic_vars = Column(JSON, default=dict)
    
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", backref="tasks")
    region = relationship("Region", backref="tasks")
    jobs = relationship("Job", back_populates="task", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"))
    description = Column(Text)
    material = Column(String)
    worker = Column(String)
    quality = Column(String)
    time = Column(DateTime, default=datetime.utcnow)
    confidence_flags = Column(JSON, default=dict)
    raw_text = Column(Text)

    task = relationship("Task", back_populates="jobs")


class WorkerLog(Base):
    __tablename__ = "worker_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"))

    # Replaced Enum with String
    gender = Column(String, nullable=False)  # "Male", "Female", "Other" → validate in Python
    skill_type = Column(String, nullable=False)  # "Skilled", "Unskilled" → validate in Python
    job_role = Column(String, nullable=False)  # e.g., Mason, Electrician
    count = Column(Integer, nullable=False, default=1)
    contractor_name = Column(String, nullable=True)

    log_date = Column(Date, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", backref="worker_logs")
    task = relationship("Task", backref="worker_logs")

class MaterialInventory(Base):
    __tablename__ = "material_inventory"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    name = Column(String, nullable=False)                  # e.g., Cement
    specification = Column(String, nullable=True)          # e.g., OPC 53 Grade
    unit = Column(String, nullable=False)                  # e.g., bags, tons, sqft
    quantity = Column(Integer, default=0)                  # current available quantity

    updated_at = Column(DateTime, default=datetime.utcnow)
    
    project = relationship("Project", backref="material_inventory")
class Material(Base):
    __tablename__ = "materials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    specification = Column(String)
    unit = Column(String, nullable=False)
    quantity_available = Column(Float, default=0.0)

    logs = relationship("MaterialLog", back_populates="material", cascade="all, delete-orphan")

class MaterialLog(Base):
    __tablename__ = "material_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"))
    material_id = Column(UUID(as_uuid=True), ForeignKey("materials.id", ondelete="SET NULL"))

    change_type = Column(String, nullable=False)  # "Usage", "Refill", "Wastage" → validate in Python
    quantity = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    description = Column(String, nullable=True)
 
    log_date = Column(Date, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", backref="material_logs")
    task = relationship("Task", backref="material_logs")
    material = relationship("Material", backref="material_logs")