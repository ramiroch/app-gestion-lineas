"""Pruebas de core/parametros.py."""

from datetime import time

import pytest

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Cliente, Planta
from core.parametros import ParametrosIncompletos, parametros_planta


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def crear_planta(sesion, **parametros) -> int:
    planta = Planta(nombre="Planta Norte", pais="Argentina",
                    cliente=Cliente(nombre="CLIENTE A", pais="Argentina"), **parametros)
    sesion.add(planta)
    sesion.commit()
    return planta.id


COMPLETOS = {"blanco_ml": 0.2, "factor_aceite_fruta": 68.8416, "factor_aceite_emulsion": 3.45,
             "hora_inicio_dia": time(5, 0)}


def test_devuelve_los_parametros(sesion):
    assert parametros_planta(sesion, crear_planta(sesion, **COMPLETOS)) == COMPLETOS


def test_blanco_cero_es_valido(sesion):
    planta_id = crear_planta(sesion, **(COMPLETOS | {"blanco_ml": 0.0}))
    assert parametros_planta(sesion, planta_id)["blanco_ml"] == 0.0


def test_falta_un_parametro(sesion):
    planta_id = crear_planta(sesion, **(COMPLETOS | {"factor_aceite_fruta": None}))

    with pytest.raises(ParametrosIncompletos) as error:
        parametros_planta(sesion, planta_id)

    assert str(error.value).startswith(
        'La planta "Planta Norte" no tiene definido el factor de aceite en fruta. Completalo')


def test_faltan_todos(sesion):
    with pytest.raises(ParametrosIncompletos) as error:
        parametros_planta(sesion, crear_planta(sesion))

    assert ("no tiene definidos el blanco (mL), el factor de aceite en fruta, el factor de "
            "aceite en emulsión y la hora de inicio del día de producción. Completalos"
            ) in str(error.value)


def test_factor_cero_o_blanco_negativo(sesion):
    with pytest.raises(ParametrosIncompletos, match="tiene que ser mayor que 0"):
        parametros_planta(sesion, crear_planta(sesion, **(COMPLETOS | {"factor_aceite_emulsion": 0})))
    with pytest.raises(ParametrosIncompletos, match="blanco negativo"):
        parametros_planta(sesion, crear_planta(sesion, **(COMPLETOS | {"blanco_ml": -0.1})))


def test_planta_inexistente(sesion):
    with pytest.raises(ValueError, match="No existe la planta"):
        parametros_planta(sesion, 999)
