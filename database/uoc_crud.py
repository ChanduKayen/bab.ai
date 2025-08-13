from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select, update, delete
from typing import Optional, List
from database.models import Project, Flat, Region, WorkerLog, MaterialInventory, MaterialLog, Task
from sqlalchemy.exc import SQLAlchemyError
import logging
from sqlalchemy.orm import declarative_base
from datetime import datetime
from uuid import UUID
import uuid

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

    # -------------------- Tasks --------------------
    async def get_scopes_in_region(self, region_full_id: str) -> List[str]:
        print(f"uoc_crud:::get_scopes_in_region::: --Fetching scopes for region full_id {region_full_id} --")
        try:
            # First, get the region UUID from the full_id

            region_query = select(Region.id).where(Region.full_id == (region_full_id).strip())
            region_result = await self.session.execute(region_query)
            region_id = region_result.all()
            region_id = region_id[0][0] if region_id else None
            print(f"uoc_crud:::get_scopes_in_region::: --Region ID found: {region_id} -- with the complete result {region_result} --")
            if not region_id:
                print(f"uoc_crud:::No region found with full_id {region_full_id}")
                return []
            # Now, fetch scopes from Task using the region UUID
            stmt = select(Task.task_type).where(Task.region_id == region_id)
            result = await self.session.execute(stmt)
            scopes = result.scalars().all()
            return scopes
        except Exception as e:
            print(f"uoc_crud:::Error fetching scopes for region full_id {region_full_id}: {e}")
            return []

    async def get_task(self, region_id: UUID, scope: str) -> Optional[Task]:
        print(f"uoc_crud:::get_task::: --Fetching task for region {region_id} and scope '{scope}' --")
        try:
            stmt = select(Task).where(
                Task.region_id == region_id,
                Task.task_type == scope
            )
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except Exception as e:
            print(f"Error fetching task for region {region_id} and scope '{scope}': {e}")
            return None

    async def create_task(self, project_id, region_full_id: str, scope) -> Optional[Task]:

        print(f"uoc_crud:::create_task::: --Creating task for region {region_full_id} and scope '{scope}' --")
        region_query = select(Region.id).where(Region.full_id == (region_full_id).strip())
        region_result = await self.session.execute(region_query)
        region_id = region_result.all()           
        region_id = region_id[0][0] if region_id else None
        print(f"uoc_crud:::create_task::: --Region ID found: {region_id} ")
        try:
            task = Task(
                id=uuid.uuid4(),
                project_id=project_id, 
                region_id=region_id,
                task_type=scope,
                status="Not Started",
                created_at=datetime.utcnow()
            )
            self.session.add(task)
            await self.session.commit()
            await self.session.refresh(task)
            print(f"uoc_crud:::create_task::: --Task created: {task.__dict__} --")
            return task
        except Exception as e:
            await self.session.rollback()
            print(f"Error creating task for region {region_id} and scope '{scope}': {e}")
            return None

    async def get_or_create_task(self, region_id: UUID, scope: str) -> Optional[Task]:
        print(f"uoc_crud:::get_or_create_task::: --Getting or creating task for region {region_id} and scope '{scope}' --")
        task = await self.get_task(region_id, scope)
        return task if task else await self.create_task(region_id, region_id, scope)
 
    async def get_task_summary(self):
        print("uoc_crud:::get_task_summary::: --Fetching task summary --")
        return []  # implement as needed

    async def get_region_full_ids_by_project(self, project_id: UUID) -> List[str]:
        print(f"uoc_crud::::::get_region_full_ids_by_project::: --Fetching region full IDs for project {project_id} --")
        try:
            stmt = select(Region.full_id).where(Region.project_id == project_id)
            result = await self.session.execute(stmt)
            return result.scalars().all()
        except Exception as e:
            print(f"uoc_crud:::Error fetching regions for project {project_id}: {e}")
            return []


