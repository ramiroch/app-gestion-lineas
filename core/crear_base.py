"""Crea las tablas de la base de datos.

Uso (desde la carpeta del proyecto):  python -m core.crear_base
"""

from sqlalchemy import Engine

from core.db import RUTA_BASE, crear_motor
from core.models import Base


def crear_tablas(motor: Engine) -> None:
    """Crea todas las tablas definidas en core/models.py.

    Las tablas que ya existen no se modifican (no se borran datos).
    """
    Base.metadata.create_all(motor)


if __name__ == "__main__":
    crear_tablas(crear_motor())
    print(f"Tablas creadas en {RUTA_BASE}")
