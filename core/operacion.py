"""Operación de los eBOE según el PLC (LecturaPLC): horas, marcha y paradas, carga y programas.

Las lecturas llegan cada 5 minutos. Cada lectura representa el intervalo hasta la siguiente del mismo
eBOE (como máximo INTERVALO_MAXIMO: un hueco más largo es falta de datos, no tiempo en marcha).
- Horómetro = horas_maquina + minutos / 60 (h).
- En marcha: elevador = 1. Con fruta: TON calculadas > 0. En marcha sin fruta: elevador = 1 y TON
  calculadas = 0.
- Las TON calculadas salen de baldes por minuto × kg por balde: no son un peso real.
"""

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Equipo, LecturaPLC, Linea

COLUMNAS_PLC = ["equipo_id", "eboe", "fecha_hora", "horas_maquina", "minutos", "elevador", "programa",
                "ton_seteadas", "ton_calculadas"]


def lecturas_plc(sesion: Session, planta_id: int) -> pd.DataFrame:
    """Lecturas del PLC de los eBOE de la planta, ordenadas por eBOE y fecha y hora (COLUMNAS_PLC)."""
    filas = sesion.execute(
        select(LecturaPLC.equipo_id, Equipo.numero, LecturaPLC.fecha_hora, LecturaPLC.horas_maquina,
               LecturaPLC.minutos, LecturaPLC.elevador, LecturaPLC.programa, LecturaPLC.ton_seteadas,
               LecturaPLC.ton_calculadas)
        .join(Equipo, LecturaPLC.equipo_id == Equipo.id).join(Linea, Equipo.linea_id == Linea.id)
        .where(Linea.planta_id == planta_id)).all()
    tabla = pd.DataFrame(filas, columns=COLUMNAS_PLC)
    tabla["fecha_hora"] = pd.to_datetime(tabla["fecha_hora"])
    return tabla.sort_values(["eboe", "fecha_hora"], ignore_index=True)
