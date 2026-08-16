from datetime import datetime, timezone
from sqlalchemy import Boolean, String, Float, Integer, DateTime, JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from .config import get_settings

class Base(DeclarativeBase): pass
class Prediction(Base):
    __tablename__="predictions"
    id:Mapped[int]=mapped_column(primary_key=True); ticker:Mapped[str]=mapped_column(String(12),index=True); horizon:Mapped[int]; bias:Mapped[str]=mapped_column(String(32)); probabilities:Mapped[dict]=mapped_column(JSON); confidence:Mapped[float]; starting_price:Mapped[float]; stock_score:Mapped[int]; market_score:Mapped[int]; created_at:Mapped[datetime]=mapped_column(DateTime,default=lambda:datetime.now(timezone.utc)); actual_return:Mapped[float|None]=mapped_column(Float,nullable=True); evaluated_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True); direction_correct:Mapped[bool|None]=mapped_column(Boolean,nullable=True); invalidation_triggered:Mapped[bool|None]=mapped_column(Boolean,nullable=True)
class UserSetting(Base):
    __tablename__="user_settings"
    key:Mapped[str]=mapped_column(String(80),primary_key=True); value:Mapped[dict]=mapped_column(JSON)

engine=create_async_engine(get_settings().database_url)
Session=async_sessionmaker(engine,expire_on_commit=False)
async def init_db():
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
async def get_db():
    async with Session() as db: yield db
