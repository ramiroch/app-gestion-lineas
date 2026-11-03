"""Turno y fecha de producción de un momento dado, según la configuración de la planta.

Ejemplo LEDESMA: turnos M 05:00–12:59, T 13:00–20:59, N 21:00–04:59 y día de
producción de 05:00 a 04:59. El 25/06 a las 02:00 es turno N del día de producción 24/06.
Las funciones reciben la planta ya leída (con sus turnos): no consultan la base.
"""

from datetime import date, datetime, time, timedelta

from core.models import Planta, Turno
from core.parametros import validar_parametros


def _contiene(turno: Turno, hora: time) -> bool:
    """True si la hora cae dentro del turno (hora_fin incluida)."""
    if turno.hora_inicio <= turno.hora_fin:
        return turno.hora_inicio <= hora <= turno.hora_fin
    # Cruza la medianoche (por ejemplo 21:00–04:59): desde el inicio O hasta el fin.
    return hora >= turno.hora_inicio or hora <= turno.hora_fin


def turno_de(planta: Planta, fecha_hora: datetime) -> Turno:
    """Devuelve el turno de la planta al que pertenece fecha_hora.

    La hora se recorta al minuto (12:59:30 cuenta como 12:59). Da error si la
    planta no tiene turnos, o si la hora no cae en ninguno o cae en más de uno.
    """
    if not planta.turnos:
        raise ValueError(f'La planta "{planta.nombre}" no tiene turnos cargados (hoja Turno).')
    hora = fecha_hora.time().replace(second=0, microsecond=0)
    turnos = [turno for turno in planta.turnos if _contiene(turno, hora)]
    if len(turnos) == 1:
        return turnos[0]
    if not turnos:
        raise ValueError(f'La hora {hora:%H:%M} no cae en ningún turno de la planta "{planta.nombre}". '
                         "Revisá los horarios en la hoja Turno.")
    nombres = ", ".join(turno.nombre for turno in turnos)
    raise ValueError(f'La hora {hora:%H:%M} cae en más de un turno de la planta "{planta.nombre}" '
                     f"({nombres}). Revisá los horarios en la hoja Turno.")


def fecha_produccion(planta: Planta, fecha_hora: datetime) -> date:
    """Día de producción de fecha_hora: la misma fecha si la hora es ≥ hora_inicio_dia;
    si no, el día anterior (la madrugada pertenece al día de producción que empezó antes).
    """
    inicio = validar_parametros(planta, ["hora_inicio_dia"])["hora_inicio_dia"]
    if fecha_hora.time() >= inicio:
        return fecha_hora.date()
    return fecha_hora.date() - timedelta(days=1)
