"""Pruebas de core/mensual.py."""

from datetime import datetime, time

import pandas as pd
import pytest

from core.analisis import pares_con_emulsion, tabla_analisis
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.mensual import eficiencia_proceso_por_mes, eje_de_meses, etiqueta_mes, mes_de, por_mes_y_eboe
from core.models import AnalisisScott, Cliente, Equipo, Linea, Planta, Proceso, PuntoMuestreo, Turno

SIN_EMULSION = pd.DataFrame(columns=["eboe", "fecha_hora", "aceite_emulsion"])


def test_etiquetas_y_meses():
    assert etiqueta_mes(pd.Timestamp(2025, 7, 1)) == "Jul 2025" and etiqueta_mes(pd.Timestamp(2026, 1, 1)) == "Ene 2026"
    assert mes_de(pd.Series(pd.to_datetime(["2025-07-31", "2025-08-01"]))).tolist() == [
        pd.Timestamp(2025, 7, 1), pd.Timestamp(2025, 8, 1)]


def test_eje_con_hueco_entre_temporadas():
    meses = pd.to_datetime(["2025-09-01", "2025-07-01", "2025-08-01", "2026-07-01", "2026-08-01", "2025-07-01"])

    assert eje_de_meses(meses) == ["Jul 2025", "Ago 2025", "Sep 2025", " ", "Jul 2026", "Ago 2026"]
    assert eje_de_meses(pd.to_datetime(["2025-07-01", "2025-09-01", "2026-07-01"])) == [
        "Jul 2025", " ", "Sep 2025", "  ", "Jul 2026"]                    # cada hueco con etiqueta única
    assert eje_de_meses([]) == []


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def test_turno_n_que_cruza_el_fin_de_mes_cuenta_en_julio(sesion):
    """Una muestra del 1/08 a las 02:00 es del día de producción 31/07: cuenta en julio."""
    planta = Planta(nombre="Planta Norte", pais="Argentina", factor_aceite_fruta=70.0, hora_inicio_dia=time(5, 0),
                    cliente=Cliente(nombre="CLIENTE A", pais="Argentina"))
    for nombre, inicio, fin in (("M", time(5), time(12, 59)), ("T", time(13), time(20, 59)), ("N", time(21), time(4, 59))):
        sesion.add(Turno(nombre=nombre, hora_inicio=inicio, hora_fin=fin, planta=planta))
    equipo = Equipo(codigo="PN-BOE-01", nombre="eBOE 1", tipo="BOE", numero=1,
                    linea=Linea(nombre="Línea 1", planta=planta))
    entrada, salida = (PuntoMuestreo(tipo=t, nombre=t, equipo=equipo) for t in ("fruta entrada", "fruta salida"))
    proceso = Proceso(numero=5, temporada=2025, planta=planta)

    def analisis(punto, fecha_hora, vol_ml):     # factor 70, blanco 0, N 0,1, 10 g → aceite = vol × 0,7 kg/MT
        return AnalisisScott(punto_muestreo=punto, proceso=proceso, fecha_hora=fecha_hora, analista="Prueba",
                             peso_muestra_g=10.0, vol_ml=vol_ml, blanco_ml=0.0, normalidad=0.1, cargado_por="prueba")

    madrugada, manana = datetime(2025, 8, 1, 2, 0), datetime(2025, 8, 1, 8, 0)
    sesion.add_all([planta,
                    analisis(entrada, madrugada, 10.0), analisis(salida, madrugada, 1.0),     # 90 %
                    analisis(entrada, manana, 10.0), analisis(salida, manana, 3.0)])          # 70 %
    sesion.commit()
    pares = pares_con_emulsion(tabla_analisis(sesion, planta.id), SIN_EMULSION)

    mensual = por_mes_y_eboe(pares, "eficiencia_pct", None).set_index("etiqueta")

    assert list(mensual.index) == ["Jul 2025", "Ago 2025"]
    assert mensual.loc["Jul 2025", "valor"] == pytest.approx(90.0) and mensual.loc["Jul 2025", "n"] == 1
    assert mensual.loc["Ago 2025", "valor"] == pytest.approx(70.0)


def _pares():
    """eBOE 1 y 2 en julio (turnos M y T del 10/07) y eBOE 1 en agosto (turno M del 5/08, sin toneladas)."""
    return pd.DataFrame({
        "eboe": [1, 1, 2, 1], "turno": ["M", "T", "M", "M"],
        "fecha_produccion": pd.to_datetime(["2025-07-10", "2025-07-10", "2025-07-10", "2025-08-05"]),
        "eficiencia_pct": [80.0, 90.0, 70.0, 85.0], "aceite_fruta": [8.0, 10.0, 9.0, 8.5], "tph": [10.0, 14.0, 12.0, None],
        "aceite_salida": [1.6, 1.0, 2.7, 1.3],
    })


def _toneladas():
    return pd.DataFrame({"fecha_produccion": pd.to_datetime(["2025-07-10", "2025-07-10"]), "turno": ["M", "T"],
                         "fruta_t": [100.0, 300.0]})


def test_por_mes_y_eboe_ponderado_y_simple():
    ponderado = por_mes_y_eboe(_pares(), "eficiencia_pct", _toneladas()).set_index(["etiqueta", "eboe"])
    assert ponderado.loc[("Jul 2025", 1), "valor"] == pytest.approx((80 * 100 + 90 * 300) / 400)   # 87,5
    assert ponderado.loc[("Jul 2025", 1), "ponderada"]
    assert ponderado.loc[("Ago 2025", 1), "valor"] == pytest.approx(85.0)                       # sin toneladas
    assert not ponderado.loc[("Ago 2025", 1), "ponderada"]

    mtph = por_mes_y_eboe(_pares(), "tph", None).set_index(["etiqueta", "eboe"])
    assert mtph.loc[("Jul 2025", 1), "valor"] == pytest.approx(12.0) and mtph.loc[("Jul 2025", 1), "n"] == 2
    assert pd.isna(mtph.loc[("Ago 2025", 1), "valor"]) and mtph.loc[("Ago 2025", 1), "n"] == 0   # vacío = NaN


def test_eficiencia_proceso_por_mes():
    produccion = pd.DataFrame({
        "fecha_produccion": pd.to_datetime(["2025-07-10", "2025-07-10", "2025-08-05"]), "turno": ["M", "T", "M"],
        "proceso_id": [1, 1, 1], "temporada": [2025] * 3,
        "fruta_t": [100.0, 300.0, 200.0], "aceite_envasado_kg": [600.0, 2200.0, None],
    })

    mensual = eficiencia_proceso_por_mes(_pares(), _toneladas(), produccion).set_index("etiqueta")

    julio = mensual.loc["Jul 2025"]
    assert julio["rendimiento"] == pytest.approx(2800 / 400)                                       # 7 kg/MT
    # aceite de entrada ponderado de julio: turno M (8 y 9 → 8,5) × 100 y turno T (10) × 300
    assert julio["aceite_entrada"] == pytest.approx((8.5 * 100 + 10 * 300) / 400)
    assert julio["eficiencia_proceso"] == pytest.approx(7 / ((8.5 * 100 + 10 * 300) / 400) * 100)
    assert (julio["n"], julio["n_pares"]) == (2, 3)
    agosto = mensual.loc["Ago 2025"]
    assert pd.isna(agosto["rendimiento"]) and pd.isna(agosto["eficiencia_proceso"]) and agosto["n"] == 0
