"""Datos de producción por turno, para ponderar promedios por toneladas procesadas."""

from datetime import date

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import Proceso, RegistroProduccion, Turno


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


def toneladas_por_turno(sesion: Session, planta_id: int) -> pd.DataFrame:
    """Fruta procesada (t) de cada turno de la planta, para ponderar promedios.

    Columnas: fecha_produccion (fecha de pandas), turno (nombre) y fruta_t, sumando los
    procesos de cada turno como toneladas_turno(). Los turnos sin dato no aparecen.
    """
    filas = sesion.execute(
        select(RegistroProduccion.fecha_produccion, Turno.nombre, func.sum(RegistroProduccion.fruta_t))
        .join(Turno, RegistroProduccion.turno_id == Turno.id)
        .where(RegistroProduccion.planta_id == planta_id, RegistroProduccion.fruta_t.is_not(None))
        .group_by(RegistroProduccion.fecha_produccion, Turno.nombre)).all()
    tabla = pd.DataFrame(filas, columns=["fecha_produccion", "turno", "fruta_t"])
    tabla["fecha_produccion"] = pd.to_datetime(tabla["fecha_produccion"])
    return tabla


COLUMNAS_PRODUCCION = ["fecha_produccion", "turno", "proceso_id", "temporada", "fruta_t", "aceite_envasado_kg"]


def tabla_produccion(sesion: Session, planta_id: int) -> pd.DataFrame:
    """Registros de producción de la planta, uno por turno y proceso (carga manual o importada).

    Columnas: fecha_produccion (fecha de pandas), turno (nombre), proceso_id, temporada (del proceso;
    vacía si el registro no tiene proceso), fruta_t (MT de fruta procesada) y aceite_envasado_kg.
    Se filtra con core.proceso.filtrar_produccion().
    """
    filas = sesion.execute(
        select(RegistroProduccion.fecha_produccion, Turno.nombre, RegistroProduccion.proceso_id, Proceso.temporada,
               RegistroProduccion.fruta_t, RegistroProduccion.aceite_envasado_kg)
        .join(Turno, RegistroProduccion.turno_id == Turno.id)
        .outerjoin(Proceso, RegistroProduccion.proceso_id == Proceso.id)
        .where(RegistroProduccion.planta_id == planta_id)).all()
    tabla = pd.DataFrame(filas, columns=COLUMNAS_PRODUCCION)
    tabla["fecha_produccion"] = pd.to_datetime(tabla["fecha_produccion"])
    return tabla
