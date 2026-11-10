"""Conexión a la base de datos con SQLAlchemy.

Por defecto usa SQLite en data/app.db (la base real). Modo prueba: si la variable de entorno APP_DB tiene otra ruta
(por ejemplo data/app_prueba.db, armada con python -m core.copia_prueba), la app y los scripts usan esa base. Una
ruta relativa se toma desde la carpeta del proyecto. Para pasar a PostgreSQL alcanza con cambiar la URL.
"""

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

CARPETA_PROYECTO = Path(__file__).resolve().parent.parent
RUTA_BASE = CARPETA_PROYECTO / "data" / "app.db"           # la base real
URL_BASE = f"sqlite:///{RUTA_BASE.as_posix()}"
VARIABLE_BASE = "APP_DB"


def ruta_base() -> Path:
    """Ruta de la base que se usa: la de la variable de entorno APP_DB (relativa a la carpeta del proyecto si no es
    absoluta) o, si no está, la real (data/app.db)."""
    valor = os.environ.get(VARIABLE_BASE, "").strip()
    if not valor:
        return RUTA_BASE
    ruta = Path(valor)
    return ruta if ruta.is_absolute() else CARPETA_PROYECTO / ruta


def es_base_de_prueba() -> bool:
    """True si la base que se usa no es la real (modo prueba)."""
    return ruta_base().resolve() != RUTA_BASE.resolve()


def crear_motor(url: str | None = None) -> Engine:
    """Crea la conexión (motor) a la base de datos indicada por `url`.

    Sin url: la base de ruta_base(). La real crea la carpeta data/ si no existe; una base de prueba tiene que existir
    (si no, error claro: se arma con python -m core.copia_prueba).
    """
    if url is None:
        ruta = ruta_base()
        if es_base_de_prueba() and not ruta.exists():
            raise FileNotFoundError(f"No existe la base de prueba {ruta} (variable {VARIABLE_BASE}). Armala con "
                                    "python -m core.copia_prueba o borrá la variable para usar la base real.")
        ruta.parent.mkdir(exist_ok=True)
        url = f"sqlite:///{ruta.as_posix()}"
    return create_engine(url)


def crear_sesion(motor: Engine) -> Session:
    """Abre una sesión para leer y guardar datos con el motor dado."""
    return sessionmaker(bind=motor)()
