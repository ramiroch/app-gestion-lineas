"""Copia la base real (data/app.db) a data/app_prueba.db, para probar la app sin tocar los datos reales.

Uso: python -m core.copia_prueba (si data/app_prueba.db ya existe, se reemplaza por una copia nueva). Después se abre
la app con la variable de entorno APP_DB=data/app_prueba.db (ver core.db).
"""

import shutil
from pathlib import Path

from core.db import RUTA_BASE

RUTA_PRUEBA = RUTA_BASE.with_name("app_prueba.db")


def copiar_base(origen: Path = RUTA_BASE, destino: Path = RUTA_PRUEBA) -> Path:
    """Copia el archivo de la base origen al destino (lo reemplaza si existe). Devuelve el destino."""
    origen, destino = Path(origen), Path(destino)
    if not origen.exists():
        raise FileNotFoundError(f"No existe la base {origen}: no hay nada para copiar.")
    if origen.resolve() == destino.resolve():
        raise ValueError("La copia de prueba no puede ser la misma base real.")
    shutil.copy2(origen, destino)
    return destino


if __name__ == "__main__":
    destino = copiar_base()
    print(f"Copia de prueba lista: {destino}")
    print("Para abrir la app con esa base (PowerShell): $env:APP_DB = \"data\\app_prueba.db\"; streamlit run app/app.py")
