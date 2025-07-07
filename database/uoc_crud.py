from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select, update, delete
from typing import Optional, List
from database.models import Project, Flat, Region, WorkerLog, MaterialInventory, MaterialLog
from sqlalchemy.exc import SQLAlchemyError
import logging
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

def to_dict(model):
    return {c.key: getattr(model, c.key) for c in model.__table__.columns}


class DatabaseCRUD:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ----------------- Generic UPSERT -----------------
    async def upsert(self, model, index_elements: List[str], update_fields: dict, insert_fields: dict):
        try:
            stmt = (
                pg_insert(model)
                .values(**insert_fields)
                .on_conflict_do_update(
                    index_elements=index_elements,
                    set_=update_fields
                )
                .returning(model)
            )
            result = await self.session.execute(stmt)
            await self.session.commit()
            return result.scalar_one_or_none()
        except Exception as e:
            await self.session.rollback()
            logging.error(f"Upsert error on {model.__tablename__}: {e}")
            raise

    # ----------------- Project -----------------
    async def create_project(self, project: dict) -> Project:
        try:
            db_project = Project(
                name=project["name"],
                sender_id=project.get("sender_id"),
                location=project.get("location"),
                no_of_blocks=project.get("no_of_blocks"),
                floors_per_block=project.get("floors_per_block"),
                flats_per_floor=project.get("flats_per_floor")
            )
            print(f"uoc_crud:::::Created Project::::: {db_project.__dict__}")
            self.session.add(db_project)
            await self.session.commit()
            await self.session.refresh(db_project)
            return db_project
        except Exception as e:
            await self.session.rollback()
            logging.error(f"Error creating project: {e}")
            raise

    async def get_project(self, project_id: str) -> Optional[Project]:
        try:
            query = select(Project).where(Project.id == project_id)
            result = await self.session.execute(query)
            return result.scalar_one_or_none()
        except Exception as e:
            logging.error(f"Error fetching project: {e}")
            raise

    async def update_project(self, project_id: str, project_data: dict) -> Optional[Project]:
        try:
            query = (
                update(Project)
                .where(Project.id == project_id)
                .values(**project_data)
                .returning(Project)
            )
            result = await self.session.execute(query)
            await self.session.commit()
            return result.scalar_one_or_none()
        except Exception as e:
            await self.session.rollback()
            logging.error(f"Error updating project: {e}")
            raise

    async def delete_project(self, project_id: str) -> bool:
        try:
            query = delete(Project).where(Project.id == project_id)
            result = await self.session.execute(query)
            await self.session.commit()
            return result.rowcount > 0
        except Exception as e:
            await self.session.rollback()
            logging.error(f"Error deleting project: {e}")
            raise
    async def get_projects_by_sender(self, sender_id: str) -> List[dict]:

        try:
            stmt = select(Project).where(Project.sender_id == sender_id).order_by(Project.created_at.desc())
            result = await self.session.execute(stmt)
            projects = result.scalars().all()

            return [
            {
               "id": proj.id,
                "title": proj.name,
                "location": proj.location,
                "no_of_blocks": proj.no_of_blocks,
                "floors_per_block": proj.floors_per_block,
                "flats_per_floor": proj.flats_per_floor,
                "created_at": proj.created_at.isoformat() if proj.created_at else None
            }
            for proj in projects
            ]
        except Exception as e:
            logging.error(f"Error fetching projects by sender: {e}")
            raise

    async def create_flat(self, flat: dict) -> Flat:
        try:
            db_flat = Flat(
                project_id=flat["project_id"],
                block_name=flat["block_name"],
                floor_no=flat["floor_no"],
                flat_no=flat["flat_no"],
                bhk_type=flat["bhk_type"],
                facing=flat.get("facing"),
                carpet_sft=flat.get("carpet_sft")
            )
            self.session.add(db_flat)
            await self.session.commit()
            await self.session.refresh(db_flat)
            return db_flat
        except Exception as e:
            await self.session.rollback()
            logging.error(f"Error creating flat: {e}")
            raise

    async def get_flats_by_project_and_floor(self, project_id: str, floor_no: int) -> List[Flat]:
        try:
            query = select(Flat).where(
                Flat.project_id == project_id,
                Flat.floor_no == floor_no
            )
            result = await self.session.execute(query)
            return list(result.scalars().all())
        except Exception as e:
            logging.error(f"Error fetching flats: {e}")
            raise

    # ----------------- Region -----------------
    async def create_region(self, region: dict) -> Region:
        try:
            db_region = Region(
                full_id=region["full_id"],
                code=region["code"],
                project_id=region["project_id"],
                flat_id=region.get("flat_id"),
                block_name=region.get("block_name"),
                floor_no=region.get("floor_no"),
                meta=region.get("meta", {})
            )
            self.session.add(db_region)
            await self.session.commit()
            await self.session.refresh(db_region)
            return db_region
        except Exception as e:
            await self.session.rollback()
            logging.error(f"Error creating region: {e}")
            raise
    

    async def upsert_region(self, region: dict) -> Region:
         return await self.upsert(
        model=Region,
        index_elements=["full_id"],
        update_fields={
            "code": region["code"],
            "project_id": region["project_id"],
            "flat_id": region.get("flat_id"),
            "block_name": region.get("block_name"),
            "floor_no": region.get("floor_no"),
            "meta": region.get("meta", {})
        },
        insert_fields={
            "full_id": region["full_id"],
            "code": region["code"],
            "project_id": region["project_id"],
            "flat_id": region.get("flat_id"),
            "block_name": region.get("block_name"),
            "floor_no": region.get("floor_no"),
            "meta": region.get("meta", {})
        }
    )

    async def get_regions_by_project(self, project_id: str) -> List[Region]:
        try:
            query = select(Region).where(Region.project_id == project_id)
            result = await self.session.execute(query)
            return list(result.scalars().all())
        except Exception as e:
            logging.error(f"Error fetching regions: {e}")
            raise
 # ----------------- Flat -----------------
    async def upsert_flat(self, flat_data: dict) -> dict:
        stmt = pg_insert(Flat).values(**flat_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "block_name", "floor_no", "flat_no"],
            set_={
                "bhk_type": stmt.excluded.bhk_type,
                "facing": stmt.excluded.facing,
                "carpet_sft": stmt.excluded.carpet_sft
            }
        ).returning(Flat)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar().__dict__
    
 # ----------------- Worker -----------------

    async def create_worker_log(self, log: dict) -> WorkerLog:
        try:
            db_log = WorkerLog(**log)
            self.session.add(db_log)
            await self.session.commit()
            await self.session.refresh(db_log)
            return db_log
        except Exception as e:
            await self.session.rollback()
            logging.error(f"Error creating worker log: {e}")
            raise


async def get_worker_logs_by_project(self, project_id: str) -> List[WorkerLog]:
    try:
        stmt = select(WorkerLog).where(WorkerLog.project_id == project_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    except Exception as e:
        logging.error(f"Error fetching worker logs: {e}")
        raise

 # ----------------- Inventory -----------------
async def create_material_inventory(self, inventory: dict) -> MaterialInventory:
    try:
        db_inventory = MaterialInventory(**inventory)
        self.session.add(db_inventory)
        await self.session.commit()
        await self.session.refresh(db_inventory)
        return db_inventory
    except Exception as e:
        await self.session.rollback()
        logging.error(f"Error creating material inventory: {e}")
        raise

async def update_material_quantity(self, inventory_id: str, quantity: int) -> Optional[MaterialInventory]:
    try:
        stmt = (
            update(MaterialInventory)
            .where(MaterialInventory.id == inventory_id)
            .values(quantity=quantity, updated_at=datetime.utcnow())
            .returning(MaterialInventory)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar_one_or_none()
    except Exception as e:
        await self.session.rollback()
        logging.error(f"Error updating material quantity: {e}")
        raise

async def upsert_material_inventory(self, inventory_data: dict) -> MaterialInventory:
    try:
        stmt = pg_insert(MaterialInventory).values(**inventory_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "name", "specification", "unit"],
            set_={
                "quantity": stmt.excluded.quantity,
                "updated_at": datetime.utcnow()
            }
        ).returning(MaterialInventory)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar_one()
    except Exception as e:
        await self.session.rollback()
        logging.error(f"Upsert error for material inventory: {e}")
        raise

async def get_material_inventory_by_project(self, project_id: str) -> List[MaterialInventory]:
    try:
        stmt = select(MaterialInventory).where(MaterialInventory.project_id == project_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    except Exception as e:
        logging.error(f"Error fetching material inventory: {e}")
        raise

 # ----------------- Material log -----------------
async def create_material_log(self, log: dict) -> MaterialLog:
    try:
        db_log = MaterialLog(**log)
        self.session.add(db_log)
        await self.session.commit()
        await self.session.refresh(db_log)
        return db_log
    except Exception as e:
        await self.session.rollback()
        logging.error(f"Error creating material log: {e}")
        raise

async def get_material_logs_by_inventory(self, inventory_id: str) -> List[MaterialLog]:
    try:
        stmt = select(MaterialLog).where(MaterialLog.inventory_id == inventory_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    except Exception as e:
        logging.error(f"Error fetching material logs: {e}")
        raise

async def create_material_log_and_update_inventory(self, log_data: dict) -> MaterialLog:
    try:
        # Create material log
        log = MaterialLog(**log_data)
        self.session.add(log)

        # Adjust inventory quantity
        inventory_id = log_data["inventory_id"]
        quantity_change = log_data["quantity"]
        change_type = log_data["change_type"]

        current_inventory = await self.session.get(MaterialInventory, inventory_id)
        if not current_inventory:
            raise ValueError("Inventory not found")

        if change_type == "Usage" or change_type == "Wastage":
            current_inventory.quantity -= abs(quantity_change)
        elif change_type == "Refill":
            current_inventory.quantity += abs(quantity_change)

        current_inventory.updated_at = datetime.utcnow()
        await self.session.commit()
        await self.session.refresh(log)
        return log

    except Exception as e:
        await self.session.rollback()
        logging.error(f"Error creating material log and updating inventory: {e}")
        raise