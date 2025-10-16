# db/models.py
from datetime import datetime, date
from sqlalchemy import (
    ARRAY, Boolean, Column, String, Text, Integer, BigInteger, ForeignKey, Date, 
    Index, Enum, JSON, text, DateTime, UniqueConstraint, Float, Numeric, CheckConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, relationship, declarative_base, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from sqlalchemy.schema import MetaData
from enum import Enum as PyEnum
import uuid
from sqlalchemy.sql import func
from sqlalchemy import TIMESTAMP
from app.db import Base

# class Base(DeclarativeBase):
#     metadata = MetaData(schema="public")

class WhatsAppEvent(Base):
    __tablename__ = "whatsapp_events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
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
    material_requests = relationship(
        "MaterialRequest",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

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

class RequestStatus(PyEnum):
    DRAFT = "draft"
    REQUESTED = "requested"
    QUOTED = "quoted"
    APPROVED = "approved" 

class MaterialRequest(Base):
    __tablename__ = "material_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    
    sender_id = Column(String, nullable=False)  # WhatsApp user ID
    status = Column(Enum(RequestStatus), default=RequestStatus.DRAFT, nullable=False)  # draft / requested / quoted / approved
    delivery_location = Column(String, nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expected_delivery_date = Column(Date, nullable=True)
    user_editable = Column(Boolean, default=True)
    
    items = relationship(
        "MaterialRequestItem",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    project = relationship("Project", back_populates="material_requests")
    vendor_quote_items = relationship(
        "VendorQuoteItem",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

class MaterialRequestItem(Base):
    __tablename__ = "material_request_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_request_id = Column(UUID(as_uuid=True), ForeignKey("material_requests.id", ondelete="CASCADE"))
    material_name = Column(String, nullable=False)
    sub_type = Column(String, nullable=True)  # e.g., OPC 53 Grade
    dimensions = Column(String, nullable=True)  # e.g., 20, 10, 50
    dimension_units = Column(String, nullable=True)  # e.g., mm, kg
    quantity = Column(Float, nullable=False)
    quantity_units = Column(String, nullable=True)  # e.g., units, bags
    unit_price = Column(Float, nullable=True)  # till vendor give a quote
    status = Column(Enum(RequestStatus), default=RequestStatus.DRAFT, nullable=False)  # draft / requested / quoted / approved
    vendor_notes = Column(Text, nullable=True)
     
    request = relationship("MaterialRequest", back_populates="items")
    
    vendor_quote_items = relationship(
        "VendorQuoteItem",
        back_populates="request_item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_mri_request_id", "material_request_id"),
    )
class MaterialCategory(PyEnum):
    SITE_PREPARATION = "Site Preparation & Earthwork"
    SAND_GRAVEL = "Sand & Gravel"
    CEMENT_CONCRETE = "Cement & Concrete"
    STEEL_TMT = "Steel (TMT Bars & Binding Wire)"
    BRICKS_BLOCKS = "Bricks / AAC Blocks / Red Bricks"
    AGGREGATE_STONE = "Stone Aggregate"
    FOUNDATION_CHEMICALS = "Waterproofing & Anti-termite Chemicals"
    SHUTTERING_FORMWORK = "Shuttering / Formwork"
    MASONRY = "Masonry & Mortar"
    SCAFFOLDING = "Scaffolding"
    RCC_COMPONENTS = "RCC & Structural Components"
    PLASTER = "Cement Plaster & Wall Putty"
    POP = "POP & Surface Prep"
    PLUMBING_PIPES = "Plumbing Pipes & Fittings"
    SANITARY_FIXTURES = "Sanitary Fixtures"
    WATER_STORAGE = "Water Tanks & Pumps"
    ELECTRICAL_WIRING = "Electrical Wires & Cables"
    SWITCHES_FIXTURES = "Switches & Lighting Fixtures"
    DOORS_WINDOWS = "Doors, Windows & Grills"
    FLOORING_TILES = "Flooring & Wall Tiles"
    TILE_ADHESIVES = "Tile Adhesives & Grouts"
    PAINTS = "Paints & Primers"
    WOOD_POLISH = "Wood Polish & Varnish"
    HVAC = "HVAC & Ventilation"
    PLYWOOD = "Plywood, MDF, Laminates"
    MODULAR_UNITS = "Modular Kitchen & Wardrobe Units"
    FALSE_CEILING = "False Ceilings (Gypsum, POP)"
    CLEANING_MATERIALS = "Cleaning & Handover Materials"
    SAFETY_EQUIPMENT = "Safety Equipment"
    SOLAR_SYSTEMS = "Solar Panels & Inverters"
    ELEVATORS = "Elevators / Lifts"
    SMART_HOME = "Smart Home Devices"
    SECURITY_SYSTEMS = "Security & Surveillance"

class Vendor(Base):
    __tablename__ = 'vendors'

    vendor_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    phone_number = Column(String(15))
    email = Column(String)
    address = Column(String)
    pincode = Column(String(10), nullable=False)
    material_categories = Column(ARRAY(Enum(MaterialCategory)), nullable=False)
    gst_number = Column(String(20))
    rating = Column(Float)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    vendor_quote_items = relationship(
        "VendorQuoteItem",
        back_populates="vendor",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    
class UserCategory(PyEnum):
    USER = "USER"
    SUPERVISOR = "SUPERVISOR"
    VENDOR = "VENDOR"
    BUILDER = "BUILDER"
    MANAGER = "MANAGER"
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    
class UserStage(PyEnum):
    NEW = "new"
    CURIOUS = "curious"
    IDENTIFIED = "identified" 
    ENGAGED = "engaged"
    TRUSTED = "trusted"

class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_id = Column(String, unique=True, index=True, nullable=False)
    user_full_name = Column(String, nullable=False) 
    user_category = Column(Enum(UserCategory), default=UserCategory.USER)  # USER / SUPERVISOR / ADMIN
    user_stage = Column(Enum(UserStage), default=UserStage.NEW)  # new / onboarding / active / inactive
    user_identity = Column(String, nullable=True)  # e.g., phone number, email
    credit_offer_pending = Column(Boolean, default=False)
    user_actions = Column(ARRAY(String), default=list)  # e.g., ["asked_for_material_quote", "used_credit_feature"]
    last_action_ts = Column(DateTime, default=datetime.utcnow)
    user_score = Column(Integer, default=0)
    
    credit_profile = relationship("CreditProfile", back_populates="user", uselist=False)
    __table_args__ = (UniqueConstraint('sender_id', name='uq_user_sender_id'),)
class QuoteRequestVendor(Base):
        __tablename__ = "quote_request_vendors"

        quote_request_id = Column(UUID(as_uuid=True), ForeignKey("material_requests.id", ondelete="CASCADE"), primary_key=True)
        vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.vendor_id", ondelete="CASCADE"), primary_key=True)

class QuoteResponse(Base):
        __tablename__ = "quote_responses"
        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
        quote_request_id = Column(UUID(as_uuid=True), ForeignKey("material_requests.id", ondelete="CASCADE"))
        vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.vendor_id", ondelete="CASCADE"), nullable=False)
        material_name = Column(String, nullable=False)
        specification = Column(String, nullable=True)
        unit = Column(String, nullable=False)
        price = Column(Float, nullable=False)
        available_quantity = Column(Float, nullable=True)
        notes = Column(Text, nullable=True)
        created_at = Column(DateTime, default=datetime.utcnow)
        vendor = relationship("Vendor")

class QuoteStatus(PyEnum):
    PENDING = "pending"
    QUOTED = "quoted"
    APPROVED = "approved"
    REJECTED = "rejected"
class VendorQuoteItem(Base):
    __tablename__ = "vendor_quote_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Anchors
    quote_request_id = Column(UUID(as_uuid=True), ForeignKey("material_requests.id", ondelete="CASCADE"), nullable=False)
    request_item_id  = Column(UUID(as_uuid=True), ForeignKey("material_request_items.id", ondelete="CASCADE"), nullable=False)
    vendor_id        = Column(UUID(as_uuid=True), ForeignKey("vendors.vendor_id", ondelete="CASCADE"), nullable=False)

    # Vendor’s response
    quoted_price  = Column(Float, nullable=False)     # numeric value
    price_unit    = Column(String, nullable=False)    # e.g., "bag", "kg", "ton", "piece"
    delivery_days = Column(Integer, nullable=True)    # e.g., 7 days
    delivery_date = Column(Date, nullable=True)      # alternative exact date
    comments      = Column(Text, nullable=True)       # vendor's comments or notes
    status        = Column(Enum(QuoteStatus), default=QuoteStatus.QUOTED, nullable=False)  # quoted / approved

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("quote_request_id", "vendor_id", "request_item_id", name="uq_vendor_quote_unique_line"),
    )

    # Relationships
    request      = relationship("MaterialRequest", back_populates="vendor_quote_items")
    request_item = relationship("MaterialRequestItem", back_populates="vendor_quote_items")
    vendor       = relationship("Vendor", back_populates="vendor_quote_items")


# -------------------------------------------------------------------------
# sku_master  (canonical product list)
# -------------------------------------------------------------------------

class SkuMaster(Base):
    __tablename__ = "sku_master"

    # Opaque ID (ULID/UUID acceptable). Using TEXT as per doc.
    sku_id       = Column(String, primary_key=True)
    brand        = Column(String, nullable=False)
    category     = Column(String, nullable=False)    # e.g., tmt, cement, paint, tiles, wire

    # Base UOM for normalized pricing (e.g., kg, L, m)
    uom_code     = Column(String, nullable=False)

    # Pack info (kept simple)
    pack_qty     = Column(Numeric, nullable=False, default=1)
    pack_uom     = Column(String, nullable=False, default="kg")

    description  = Column(Text, nullable=True)

    # Category-specific attributes (JSONB); index added below
    attributes   = Column(JSONB, nullable=False)

    # Identity string generated in app layer (e.g., lower(brand)|grade|size…)
    canonical_key = Column(String, nullable=True)

    # Marks placeholder/low-confidence SKUs created from uncertain matches
    ambiguous    = Column(Boolean, nullable=False, default=False)
    type_norm    = Column(String, nullable=True)
    size_mm_primary = Column(Numeric, nullable=True)
    size_mm_secondary = Column(Numeric, nullable=True)
    primary_size_native = Column(Text, nullable=True)
    primary_size_unit   = Column(String, nullable=True)
    secondary_size_native = Column(Text, nullable=True)
    secondary_size_unit   = Column(String, nullable=True)
    search_text  = Column(Text, nullable=True)
    tsv          = Column(TSVECTOR)

    # active | retired
    status       = Column(String, nullable=False, default="active")
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_sku_cankey", "canonical_key"),
        Index("idx_sku_attrs", "attributes", postgresql_using="gin"),
        Index("idx_sku_ambiguous", "ambiguous"),
        Index("idx_sku_type_norm", "type_norm"),
        Index("idx_sku_size_mm", "size_mm_primary", "size_mm_secondary"),
        Index("idx_sku_primary_size", "primary_size_unit", "primary_size_native"),
        Index("idx_sku_secondary_size", "secondary_size_unit", "secondary_size_native"),
        Index("idx_sku_search_trgm", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
        Index("idx_sku_tsv", "tsv", postgresql_using="gin"),
        CheckConstraint("status IN ('active','retired')", name="ck_sku_status"),
    )


# -------------------------------------------------------------------------
# sku_vendor_price  (every vendor price row; append-only)
# -------------------------------------------------------------------------

class SkuVendorPrice(Base):
    __tablename__ = "sku_vendor_price"

    # BIGSERIAL equivalent
    id         = Column(BigInteger, primary_key=True, autoincrement=True)

    # FK to sku_master.sku_id (TEXT)
    sku_id     = Column(String, ForeignKey("sku_master.sku_id", ondelete="CASCADE"), nullable=False)

    # Use your existing vendors.vendor_id (UUID)
    vendor_id  = Column(UUID(as_uuid=True), ForeignKey("vendors.vendor_id", ondelete="CASCADE"), nullable=False)
    quoted_at  = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Price normalized to sku_master.uom_code
    price      = Column(Numeric, nullable=False)
    currency   = Column(String, nullable=False, default="INR")

    resolved   = Column(Boolean, nullable=False, default=False)   # true if unique match
    quote_ref  = Column(String, nullable=True)                    # shared across ambiguous candidates
    tag        = Column(Text, nullable=True)                      # free note, e.g., "TEMP — Vizag Fe550D 12mm (length ?)"

    pincode    = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Prevent dupes for the same ambiguous line
        UniqueConstraint("vendor_id", "quote_ref", "sku_id", name="uq_vendor_quote_sku"),
        Index("idx_svp_vendor_sku", "vendor_id", "sku_id"),
        Index("idx_svp_resolved", "resolved"),
        Index("idx_svp_sku_resolved", "sku_id", "resolved"),
        Index("idx_svp_quoted_at", "quoted_at"),
    )
# -------------------------------------------------------------------------

class CreditStatus(PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CLOSED = "closed"

class CreditProfile(Base):
    __tablename__ = "credit_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    aadhaar = Column(String(20), nullable=True)
    pan = Column(String(10), nullable=True)
    gst = Column(String(20), nullable=True)
    status = Column(Enum(CreditStatus), default=CreditStatus.PENDING, nullable=False)
    limit = Column(Float, default=0.0)   # Approved credit limit
    used = Column(Float, default=0.0)    # Amount already used
    trust_score = Column(Float, default=0.0)  # Thirtee  Trust Score
    nbfc_partner = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User", back_populates="credit_profile")


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credit_profile_id = Column(UUID(as_uuid=True), ForeignKey("credit_profiles.id", ondelete="CASCADE"), nullable=False)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.vendor_id", ondelete="SET NULL"))
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    transaction_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")  # pending, settled, failed

    profile = relationship("CreditProfile", backref="transactions")
    vendor = relationship("Vendor")

class PartnerStatusHistory(Base):
    __tablename__ = "partner_status_history"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_id = Column(String, ForeignKey("users.sender_id", ondelete="CASCADE"), index=True, nullable=False)
    partner = Column(String, nullable=False)              # "NBFC_X"
    status = Column(String, nullable=False)               # "pending"/"approved"/"rejected"
    score  = Column(Float, nullable=True)                 # normalize to 0–100 if partner gives raw value
    limit  = Column(Float, nullable=True)
    payload = Column(JSON, default=dict)                  # redacted/hashed; never store sensitive raw IDs
    occurred_at = Column(DateTime, default=datetime.utcnow, index=True)

    __all__ = [
    "Base",
    "RequestStatus",
    "Project",
    "Vendor",
    "MaterialRequest",
    "MaterialRequestItem",
    "VendorQuoteItem",
]


# -------------------------------------------------------------------------
# sku_alias  (alias terms mapped to canonical/master SKU)
# -------------------------------------------------------------------------


class SkuAlias(Base):
    __tablename__ = "sku_alias"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    master_sku_id = Column(String, ForeignKey("sku_master.sku_id", ondelete="CASCADE"), nullable=False)
    alias_text    = Column(String, nullable=False)
    region        = Column(String, nullable=True)   # optional locality tag (e.g., city/state)
    vendor_id     = Column(UUID(as_uuid=True), ForeignKey("vendors.vendor_id", ondelete="SET NULL"), nullable=True)
    confidence    = Column(Numeric, nullable=True)  # optional 0..1

    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_sku_alias_master", "master_sku_id"),
        Index("idx_sku_alias_text", "alias_text"),
    )

