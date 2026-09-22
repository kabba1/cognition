"""Shared relational metadata; domain tables belong in explicit migrations."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """One application's metadata with deterministic constraint/index names."""

    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ix": "ix_%(column_0_label)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
        }
    )
