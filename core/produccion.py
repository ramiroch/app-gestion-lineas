"""Datos de producción por turno, para ponderar promedios por toneladas procesadas."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import RegistroProduccion


def toneladas_turno(sesion: Session, planta_id: int, fecha_produccion: date, turno_id: int) -> float | None:
    """Fruta procesada (t) en un turno de un día de producción.

    Suma los registros de todos los procesos del turno (un turno puede tener dos si en él
    termina un proceso y empieza otro). Devuelve None si ningún registro tiene el dato:
    en ese caso el análisis usa el promedio simple y lo aclara en pantalla.
    """
    valores = sesion.scalars(
        select(RegistroProduccion.fruta_t).where(
            RegistroProduccion.planta_id == planta_id,
            RegistroProduccion.fecha_produccion == fecha_produccion,
            RegistroProduccion.turno_id == turno_id)).all()
    con_dato = [valor for valor in valores if valor is not None]
    return sum(con_dato) if con_dato else None
