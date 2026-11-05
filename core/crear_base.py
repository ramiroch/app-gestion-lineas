"""Crea las tablas de la base de datos y agrega las columnas nuevas de las que ya existen.

Uso (desde la carpeta del proyecto):  python -m core.crear_base
Hacé una copia de data/app.db antes, por las dudas.
"""

from sqlalchemy import Engine, inspect, text

from core.db import RUTA_BASE, crear_motor
from core.models import Base


def crear_tablas(motor: Engine) -> None:
    """Crea todas las tablas definidas en core/models.py.

    Las tablas que ya existen no se modifican (no se borran datos).
    """
    Base.metadata.create_all(motor)


def agregar_columnas_nuevas(motor: Engine) -> list[str]:
    """Agrega a las tablas existentes las columnas de core/models.py que todavía no tienen.

    Usa ALTER TABLE … ADD COLUMN (SQL estándar: sirve en SQLite y en PostgreSQL). Solo agrega
    columnas opcionales: quedan vacías en las filas que ya estaban. Devuelve "tabla.columna" de
    cada columna agregada. Da error si falta una columna obligatoria (esa necesita un valor).
    """
    inspector = inspect(motor)
    existentes = set(inspector.get_table_names())
    nuevas = []
    for tabla in Base.metadata.sorted_tables:
        if tabla.name not in existentes:
            continue
        en_base = {columna["name"] for columna in inspector.get_columns(tabla.name)}
        for columna in tabla.columns:
            if columna.name in en_base:
                continue
            if not columna.nullable:
                raise ValueError(f'La columna obligatoria "{tabla.name}.{columna.name}" no está en la base: '
                                 "no se puede agregar sola (las filas existentes no tendrían valor).")
            nuevas.append(columna)
    with motor.begin() as conexion:
        for columna in nuevas:
            tipo = columna.type.compile(dialect=motor.dialect)
            conexion.execute(text(f"ALTER TABLE {columna.table.name} ADD COLUMN {columna.name} {tipo}"))
    return [f"{columna.table.name}.{columna.name}" for columna in nuevas]


if __name__ == "__main__":
    motor = crear_motor()
    agregadas = agregar_columnas_nuevas(motor)
    crear_tablas(motor)
    print(f"Tablas creadas en {RUTA_BASE}")
    print(f"Columnas agregadas: {', '.join(agregadas)}" if agregadas else "No había columnas nuevas.")
