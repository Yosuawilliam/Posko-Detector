from datetime import date, datetime
from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MEAL_PRICE = 35000
GALLON_PRICE = 22000
MAX_DAILY_MEALS = 3


class Base(DeclarativeBase):
    pass


class Posko(Base):
    __tablename__ = "poskos"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    stock_meals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_stock_alert: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    stock_gallons: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_gallon_alert: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    residents: Mapped[list["Resident"]] = relationship(
        "Resident",
        back_populates="posko",
        cascade="all, delete-orphan",
    )
    logs: Mapped[list["DistributionLog"]] = relationship(
        "DistributionLog",
        back_populates="scanner_posko",
        cascade="all, delete-orphan",
    )


class Resident(Base):
    __tablename__ = "residents"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nik: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    qr_token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str] = mapped_column(String(4), nullable=False)
    kk_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    posko_code: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("poskos.code", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
        nullable=False,
    )
    face_front_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    face_left_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    face_right_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    posko: Mapped["Posko"] = relationship("Posko", back_populates="residents")
    logs: Mapped[list["DistributionLog"]] = relationship("DistributionLog", back_populates="resident")


class DistributionLog(Base):
    __tablename__ = "distribution_logs"
    __table_args__ = (
        Index("idx_claim_daily", "resident_id", "claim_date", "status"),
        Index("idx_posko_date", "scanner_posko_code", "claim_date", "status"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    claim_date: Mapped[date] = mapped_column(Date, default=date.today, index=True, nullable=False)
    resident_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("residents.id", ondelete="SET NULL"),
        nullable=True,
    )
    resident_name: Mapped[str] = mapped_column(String(150), nullable=False)
    resident_nik: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scanner_posko_code: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("poskos.code", ondelete="CASCADE", onupdate="CASCADE"),
        index=True,
        nullable=False,
    )
    resident_posko_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    meal_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_rupiah: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)

    scanner_posko: Mapped["Posko"] = relationship("Posko", back_populates="logs")
    resident: Mapped["Resident | None"] = relationship("Resident", back_populates="logs")
