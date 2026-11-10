"""Pruebas de core/repetidas.py: entradas con los mismos g y mL en varios eBOE (posible copia)."""

from datetime import datetime

import pandas as pd
from sqlalchemy import select

from core.analisis import tabla_analisis
from core.models import AnalisisScott, Proceso, PuntoMuestreo
from core.repetidas import (REVISAR_ENTRADA_REPETIDA, con_motivo, entradas_repetidas, marcar_entradas_repetidas,
                            sin_entradas_repetidas)
from tests.test_analisis import planta, sesion  # noqa: F401  (fixtures)

MANANA = datetime(2025, 6, 10, 8, 0)


def test_entradas_repetidas_misma_ronda_mismos_g_y_ml():
    entradas = pd.DataFrame({
        "analisis_id": [1, 2, 3, 4, 5, 6],
        "fecha_hora": [MANANA] * 3 + [datetime(2025, 6, 10, 10)] * 3,
        "eboe": [1, 2, 3, 1, 2, 3],
        "peso_muestra_g": [10.0, 10.0, 10.0, 10.0, 10.0, 9.8],
        "vol_ml": [8.5, 8.5, 8.5, 8.5, 8.6, 8.5]})           # ronda 1: los 3 iguales; ronda 2: ninguno igual
    assert entradas_repetidas(entradas) == {1, 2, 3}
    dos = entradas.assign(vol_ml=[8.5, 8.5, 9.0, 8.5, 8.6, 8.5])
    assert entradas_repetidas(dos) == {1, 2}                  # solo en 2 eBOE también cuenta
    otra_hora = entradas.assign(fecha_hora=[MANANA + pd.Timedelta(hours=h) for h in range(6)])
    assert entradas_repetidas(otra_hora) == set()            # mismos g y mL, otra ronda: no


def test_con_motivo_agrega_y_saca_sin_tocar_los_demas():
    assert con_motivo(None, "b", True) == "b"
    assert con_motivo("a", "b", True) == "a; b"
    assert con_motivo("a; b", "b", True) == "a; b"           # sin repetir
    assert con_motivo("a; b", "b", False) == "a"
    assert con_motivo("b", "b", False) is None


def test_marcar_en_la_base_y_tabla_de_analisis(sesion, planta):  # noqa: F811
    p2025 = sesion.scalars(select(Proceso).where(Proceso.temporada == 2025)).one()
    punto = {(p.equipo.numero, p.tipo): p for p in sesion.scalars(select(PuntoMuestreo))}
    # el eBOE 2 con la misma entrada que el eBOE 1 (10 g, 10 mL) a la misma hora
    copia = AnalisisScott(punto_muestreo=punto[(2, "fruta entrada")], proceso=p2025, fecha_hora=MANANA,
                          analista="Prueba", peso_muestra_g=10.0, vol_ml=10.0, blanco_ml=0.0, normalidad=0.1,
                          cargado_por="prueba")
    sesion.add_all([copia, AnalisisScott(punto_muestreo=punto[(2, "fruta salida")], proceso=p2025, fecha_hora=MANANA,
                                         analista="Prueba", peso_muestra_g=10.0, vol_ml=2.0, blanco_ml=0.0,
                                         normalidad=0.1, cargado_por="prueba")])
    sesion.commit()

    resumen = marcar_entradas_repetidas(sesion, planta.id).set_index("temporada")
    assert resumen.loc[2025, ["rondas", "rondas_repetidas", "rondas_3", "entradas"]].tolist() == [2, 1, 0, 2]
    assert copia.revisar == REVISAR_ENTRADA_REPETIDA
    tabla = tabla_analisis(sesion, planta.id)
    marcadas = tabla[tabla["entrada_repetida"]]
    assert sorted(zip(marcadas["eboe"], marcadas["lugar"])) == [(1, "Entrada"), (1, "Salida"), (2, "Entrada"),
                                                                (2, "Salida")]   # las dos filas de cada par
    assert len(sin_entradas_repetidas(tabla)) == len(tabla) - 4
    assert len(sin_entradas_repetidas(tabla, incluir=True)) == len(tabla)

    copia.vol_ml = 10.4                                       # se corrige el dato: la marca se saca sola
    sesion.commit()
    marcar_entradas_repetidas(sesion, planta.id)
    assert copia.revisar is None
