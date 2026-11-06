"""Pruebas de core/parametros.py."""

from datetime import time

import pytest

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Cliente, Planta
from core.parametros import ParametrosIncompletos, parametros_planta, validar_parametros


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
             "hora_inicio_dia": time(5, 0), "blanco_centrifugas_ml": 0.1,
             "umbral_paralelo_lh": 17000.0}


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
            "aceite en emulsión, la hora de inicio del día de producción, el blanco de centrífugas (mL) y el umbral "
            "de primarias en paralelo (L/h). Completalos"
            ) in str(error.value)


def test_factor_cero_o_blanco_negativo(sesion):
    with pytest.raises(ParametrosIncompletos, match="tiene que ser mayor que 0"):
        parametros_planta(sesion, crear_planta(sesion, **(COMPLETOS | {"factor_aceite_emulsion": 0})))
    with pytest.raises(ParametrosIncompletos, match=r"el blanco \(mL\) negativo"):
        parametros_planta(sesion, crear_planta(sesion, **(COMPLETOS | {"blanco_ml": -0.1})))
    with pytest.raises(ParametrosIncompletos, match=r"el blanco de centrífugas \(mL\) negativo"):
        parametros_planta(sesion, crear_planta(sesion, **(COMPLETOS | {"blanco_centrifugas_ml": -0.1})))
    with pytest.raises(ParametrosIncompletos, match="umbral de primarias en paralelo .L/h. en 0"):
        parametros_planta(sesion, crear_planta(sesion, **(COMPLETOS | {"umbral_paralelo_lh": 0})))


def test_blanco_de_centrifugas_es_aparte_del_del_boe(sesion):
    planta_id = crear_planta(sesion, **(COMPLETOS | {"blanco_centrifugas_ml": None}))
    assert parametros_planta(sesion, planta_id, ["blanco_ml"]) == {"blanco_ml": 0.2}
    with pytest.raises(ParametrosIncompletos, match="no tiene definido el blanco de centrífugas"):
        parametros_planta(sesion, planta_id, ["blanco_centrifugas_ml"])


def test_planta_inexistente(sesion):
    with pytest.raises(ValueError, match="No existe la planta"):
        parametros_planta(sesion, 999)


def test_solo_los_parametros_requeridos(sesion):
    planta_id = crear_planta(sesion, hora_inicio_dia=time(5, 0))   # sin blanco ni factores

    assert parametros_planta(sesion, planta_id, ["hora_inicio_dia"]) == {"hora_inicio_dia": time(5, 0)}
    with pytest.raises(ParametrosIncompletos, match="no tiene definido el blanco"):
        parametros_planta(sesion, planta_id, ["blanco_ml", "hora_inicio_dia"])


def test_validar_parametros_con_la_planta_en_memoria():
    planta = Planta(nombre="Sin guardar", factor_aceite_emulsion=3.45)

    assert validar_parametros(planta, ["factor_aceite_emulsion"]) == {"factor_aceite_emulsion": 3.45}
    with pytest.raises(ValueError, match="desconocido"):
        validar_parametros(planta, ["factor_inventado"])
