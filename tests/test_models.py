"""Pruebas de core/models.py y core/crear_base.py."""

from datetime import time

import pytest
from sqlalchemy import select

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Cliente, Equipo, Linea, Planta, PuntoMuestreo

TIPOS_PUNTO = ["fruta entrada", "fruta salida", "emulsión"]


@pytest.fixture
def sesion():
    """Base vacía en memoria, con las tablas creadas. Se descarta al terminar."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def crear_ledesma(sesion):
    """Carga LEDESMA: 1 línea, 3 eBOE y 3 puntos de muestreo por eBOE."""
    cliente = Cliente(nombre="LEDESMA", pais="Argentina")
    planta = Planta(nombre="LEDESMA", cliente=cliente)
    linea = Linea(nombre="Línea 1", planta=planta)
    for numero in [1, 2, 3]:
        equipo = Equipo(tipo="eBOE", numero=numero, linea=linea)
        for tipo in TIPOS_PUNTO:
            PuntoMuestreo(tipo=tipo, nombre=f"eBOE {numero} – {tipo}", equipo=equipo)
    sesion.add(cliente)
    sesion.commit()
    return planta


def test_ledesma_tiene_9_puntos_de_muestreo(sesion):
    crear_ledesma(sesion)

    puntos = sesion.scalars(select(PuntoMuestreo)).all()

    assert len(puntos) == 9


def test_cada_punto_sabe_a_que_eboe_pertenece(sesion):
    crear_ledesma(sesion)

    for numero in [1, 2, 3]:
        equipo = sesion.scalars(select(Equipo).where(Equipo.numero == numero)).one()
        assert equipo.tipo == "eBOE"
        assert len(equipo.puntos_muestreo) == 3
        for punto in equipo.puntos_muestreo:
            assert punto.equipo.numero == numero
            assert punto.nombre.startswith(f"eBOE {numero}")
        assert sorted(p.tipo for p in equipo.puntos_muestreo) == sorted(TIPOS_PUNTO)


def test_planta_usa_parametros_de_calculo_por_defecto(sesion):
    planta = crear_ledesma(sesion)

    assert planta.blanco_ml == 0.2
    assert planta.factor_aceite_fruta == 68.8416
    assert planta.factor_aceite_emulsion == 3.45
    assert planta.hora_inicio_dia == time(5, 0)
