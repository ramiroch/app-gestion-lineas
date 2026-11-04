"""Pruebas de core/produccion.py."""

from datetime import date, time

import pandas as pd
import pytest

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Cliente, Planta, Proceso, RegistroProduccion, Turno
from core.produccion import tabla_produccion, toneladas_por_turno, toneladas_turno


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def test_turno_compartido_por_dos_procesos_suma_las_toneladas(sesion):
    planta = Planta(nombre="Planta Norte", pais="Argentina", cliente=Cliente(nombre="CLIENTE A", pais="Argentina"))
    turno_n = Turno(nombre="N", hora_inicio=time(21, 0), hora_fin=time(4, 59), planta=planta)
    turno_m = Turno(nombre="M", hora_inicio=time(5, 0), hora_fin=time(12, 59), planta=planta)
    termina, empieza = (Proceso(numero=n, temporada=2025, planta=planta) for n in (16, 17))
    dia = date(2025, 9, 17)
    sesion.add_all([planta, turno_n, turno_m, termina, empieza,
                    RegistroProduccion(planta=planta, fecha_produccion=dia, turno=turno_n,
                                       proceso=termina, fruta_t=65.7, cargado_por="prueba"),
                    RegistroProduccion(planta=planta, fecha_produccion=dia, turno=turno_n,
                                       proceso=empieza, fruta_t=53.3, cargado_por="prueba"),
                    RegistroProduccion(planta=planta, fecha_produccion=dia, turno=turno_m,
                                       proceso=empieza, fruta_t=None, cargado_por="prueba")])
    sesion.commit()

    assert toneladas_turno(sesion, planta.id, dia, turno_n.id) == pytest.approx(119.0)
    assert toneladas_turno(sesion, planta.id, dia, turno_m.id) is None          # sin dato
    assert toneladas_turno(sesion, planta.id, date(2025, 9, 18), turno_n.id) is None   # sin registro

    tabla = toneladas_por_turno(sesion, planta.id)
    assert len(tabla) == 1                                                       # el turno M no tiene dato
    fila = tabla.iloc[0]
    assert (fila["fecha_produccion"], fila["turno"]) == (pd.Timestamp(dia), "N")
    assert fila["fruta_t"] == pytest.approx(119.0)


def test_tabla_produccion(sesion):
    planta = Planta(nombre="Planta Norte", pais="Argentina", cliente=Cliente(nombre="CLIENTE A", pais="Argentina"))
    turno = Turno(nombre="M", hora_inicio=time(5, 0), hora_fin=time(12, 59), planta=planta)
    proceso = Proceso(numero=3, temporada=2025, planta=planta)
    sesion.add_all([planta, turno, proceso,
                    RegistroProduccion(planta=planta, fecha_produccion=date(2025, 7, 1), turno=turno, proceso=proceso,
                                       fruta_t=300.0, aceite_envasado_kg=1500.0, cargado_por="prueba"),
                    RegistroProduccion(planta=planta, fecha_produccion=date(2025, 7, 2), turno=turno,
                                       fruta_t=200.0, cargado_por="prueba")])          # sin proceso ni aceite
    sesion.commit()

    tabla = tabla_produccion(sesion, planta.id).sort_values("fecha_produccion", ignore_index=True)

    assert tabla[["turno", "temporada", "fruta_t"]].values.tolist()[0] == ["M", 2025, 300.0]
    assert tabla.loc[0, "aceite_envasado_kg"] == 1500.0
    assert pd.isna(tabla.loc[1, "temporada"]) and pd.isna(tabla.loc[1, "aceite_envasado_kg"])
    assert tabla.loc[1, "fecha_produccion"] == pd.Timestamp(2025, 7, 2)
