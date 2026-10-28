"""Conexión a la base de datos con SQLAlchemy.

Por defecto usa SQLite en data/app.db. Para pasar a PostgreSQL alcanza con
cambiar la URL.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

RUTA_BASE = Path(__file__).resolve().parent.parent / "data" / "app.db"
URL_BASE = f"sqlite:///{RUTA_BASE.as_posix()}"


def crear_motor(url: str = URL_BASE) -> Engine:
    """Crea la conexión (motor) a la base de datos indicada por `url`.

    Si es la base por defecto, crea la carpeta data/ si no existe.
    """
    if url == URL_BASE:
        RUTA_BASE.parent.mkdir(exist_ok=True)
    return create_engine(url)


def crear_sesion(motor: Engine) -> Session:
    """Abre una sesión para leer y guardar datos con el motor dado."""
    return sessionmaker(bind=motor)()
