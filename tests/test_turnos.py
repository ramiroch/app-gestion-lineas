"""Pruebas de core/turnos.py con la configuración de LEDESMA."""

from datetime import date, datetime, time

import pytest

from core.models import Planta, Turno
from core.parametros import ParametrosIncompletos
from core.turnos import fecha_produccion, turno_de


@pytest.fixture
def planta():
    """Planta en memoria con los turnos y el inicio del día de LEDESMA."""
    return Planta(nombre="LEDESMA", hora_inicio_dia=time(5, 0), turnos=[
        Turno(nombre="M", hora_inicio=time(5, 0), hora_fin=time(12, 59)),
        Turno(nombre="T", hora_inicio=time(13, 0), hora_fin=time(20, 59)),
        Turno(nombre="N", hora_inicio=time(21, 0), hora_fin=time(4, 59)),
    ])


# (hora del 10/07/2025, turno esperado, día de producción esperado)
CASOS = [
    ("04:59", "N", date(2025, 7, 9)),
    ("05:00", "M", date(2025, 7, 10)),
    ("12:59", "M", date(2025, 7, 10)),
    ("13:00", "T", date(2025, 7, 10)),
    ("20:59", "T", date(2025, 7, 10)),
    ("21:00", "N", date(2025, 7, 10)),
    ("23:30", "N", date(2025, 7, 10)),
    ("00:30", "N", date(2025, 7, 9)),
]


@pytest.mark.parametrize("hora, turno, dia", CASOS)
def test_turno_y_fecha_de_produccion(planta, hora, turno, dia):
    momento = datetime.combine(date(2025, 7, 10), time.fromisoformat(hora))

    assert turno_de(planta, momento).nombre == turno
    assert fecha_produccion(planta, momento) == dia


def test_25_de_junio_a_las_2_es_turno_n_del_24(planta):
    momento = datetime(2025, 6, 25, 2, 0)

    assert turno_de(planta, momento).nombre == "N"
    assert fecha_produccion(planta, momento) == date(2025, 6, 24)


def test_segundos_se_recortan_al_minuto(planta):
    assert turno_de(planta, datetime(2025, 7, 10, 12, 59, 59)).nombre == "M"


def test_planta_sin_turnos():
    with pytest.raises(ValueError, match="no tiene turnos"):
        turno_de(Planta(nombre="Vacía"), datetime(2025, 7, 10, 8, 0))


def test_hora_sin_turno_o_en_dos_turnos(planta):
    planta.turnos[2].hora_inicio = time(22, 0)          # hueco de 21:00 a 21:59
    with pytest.raises(ValueError, match="no cae en ningún turno"):
        turno_de(planta, datetime(2025, 7, 10, 21, 30))

    planta.turnos[2].hora_inicio = time(20, 0)          # T y N se pisan de 20:00 a 20:59
    with pytest.raises(ValueError, match=r"más de un turno .*\(T, N\)"):
        turno_de(planta, datetime(2025, 7, 10, 20, 30))


def test_fecha_produccion_sin_hora_de_inicio():
    with pytest.raises(ParametrosIncompletos, match="hora de inicio del día"):
        fecha_produccion(Planta(nombre="Citromax"), datetime(2025, 7, 10, 8, 0))
