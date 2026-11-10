"""Pruebas de core/gestion_alertas.py: estados, causa en histórico, filtros, datos y carta del período."""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from core.carga_scott import evaluar_turnos_cerrados, guardar_ronda
from core.gestion_alertas import (cambiar_estado, carta_de_alerta, datos_de_alerta, guardar_seguimiento,
                                  listar_alertas)
from core.models import Alerta, Cambio
from tests.test_carga_scott import _planta, ronda, sesion  # noqa: F401  (fixture)

AHORA = datetime(2026, 7, 2, 9)


def _alertas(sesion):  # noqa: F811
    """Turno M del 01/07 con la eficiencia del eBOE 1 al 70 %: alertas de regla 1 y de rango, abiertas."""
    planta = _planta(sesion)
    planta.turnos_para_alertar = 1
    sesion.commit()
    guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, 6, 10), salida_ml={1: 30.0}), "Ana",
                  ahora=datetime(2026, 7, 1, 6, 20))
    evaluar_turnos_cerrados(sesion, planta, datetime(2026, 7, 1, 13, 5), "Ramiro")
    return planta, {a.regla: a for a in sesion.scalars(select(Alerta))}


def test_estados_permitidos(sesion):  # noqa: F811
    _, alertas = _alertas(sesion)
    regla_1 = alertas["1"]
    cambiar_estado(sesion, regla_1.id, "en análisis", "Ramiro", AHORA)
    cambiar_estado(sesion, regla_1.id, "cerrada", "Ramiro", AHORA, "fruta de otra finca")
    assert (regla_1.estado, regla_1.cerrada_por, regla_1.cerrada_en) == ("cerrada", "Ramiro", AHORA)
    with pytest.raises(ValueError, match="No se puede pasar"):
        cambiar_estado(sesion, regla_1.id, "en análisis", "Ramiro", AHORA)      # no se reabre
    assert guardar_seguimiento(sesion, regla_1.id, "Ramiro", AHORA, comentario="se habló con la planta") == 1
    estados = [(c.valor_anterior, c.valor_nuevo) for c in sesion.scalars(
        select(Cambio).where(Cambio.campo == "estado").order_by(Cambio.id))]
    assert estados == [("abierta", "en análisis"), ("en análisis", "cerrada")]


def test_historico_no_cambia_de_estado_pero_acepta_causa(sesion):  # noqa: F811
    planta, alertas = _alertas(sesion)
    rango = alertas["rango"]
    rango.estado = "histórico"
    sesion.commit()
    with pytest.raises(ValueError, match="histórico no cambia de estado"):
        cambiar_estado(sesion, rango.id, "en análisis", "Ramiro", AHORA)
    assert guardar_seguimiento(sesion, rango.id, "Ramiro", AHORA, causa="fruta blanda", accion="bajar MTPH") == 2
    (causa,) = sesion.scalars(select(Cambio).where(Cambio.campo == "causa")).all()
    assert (causa.valor_anterior, causa.valor_nuevo, causa.cambiado_por) == (None, "fruta blanda", "Ramiro")
    assert guardar_seguimiento(sesion, rango.id, "Ramiro", AHORA, causa="fruta blanda") == 0    # sin cambios

    assert listar_alertas(sesion, planta.id)["regla"].tolist() == ["1"]                        # abiertas y en análisis
    todas = listar_alertas(sesion, planta.id, estados=("abierta", "en análisis", "histórico"))
    assert sorted(todas["regla"]) == ["1", "rango"]
    con = listar_alertas(sesion, planta.id, estados=("abierta", "histórico"), con_causa=True)
    sin = listar_alertas(sesion, planta.id, estados=("abierta", "histórico"), con_causa=False)
    assert (con["regla"].tolist(), sin["regla"].tolist()) == (["rango"], ["1"])
    assert listar_alertas(sesion, planta.id, desde=date(2026, 7, 2)).empty


def test_datos_y_carta_de_la_alerta(sesion):  # noqa: F811
    planta, alertas = _alertas(sesion)
    regla_1 = alertas["1"]
    datos = datos_de_alerta(sesion, planta, regla_1)
    assert datos["lugar"].tolist() == ["entrada"] and datos["resultado"].round(2).tolist() == [7.0]
    (carta,) = carta_de_alerta(sesion, planta, regla_1)
    tabla = carta["tabla"]
    assert carta["tipo_carta"] == "I-MR-S" and carta["titulo"] == "Eficiencia eBOE · eBOE 1"
    assert tabla["en_episodio"].tolist() == [True] and tabla["media"].round(1).tolist() == [70.0]
    assert (tabla["lc"].iloc[0], tabla["lcs"].iloc[0], tabla["lci"].iloc[0]) == (85, 91, 79)
