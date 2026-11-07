"""Pruebas de core/limites.py: series, fase I con exclusiones y congelado con historial."""

from datetime import datetime, time, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.limites import (COLUMNAS_SERIE, PREFIJO_INVALIDO, calcular_limites_fase1, congelar_limites,
                          congelar_temporada, limites_vigentes, motivo_invalido, series_boe, series_centrifugas)
from core.models import Cliente, Equipo, Linea, LimitesControl, LimitesExclusion, Planta
from core.spc import limites_imr


def serie(valores, equipo_id=1, inicio=datetime(2025, 7, 1, 6), invalidos=None, tipo="BOE", fruta="naranja",
          tabla="analisis_scott", primer_id=1):
    """Una serie inventada (COLUMNAS_SERIE), un dato cada 2 h."""
    filas = []
    for k, valor in enumerate(valores):
        momento = inicio + timedelta(hours=2 * k)
        hora = momento.hour
        filas.append({"tabla": tabla, "dato_id": primer_id + k, "equipo_id": equipo_id, "equipo": f"E{equipo_id}",
                      "tipo": tipo, "fecha_hora": pd.Timestamp(momento),
                      "fecha_produccion": (pd.Timestamp(momento) - pd.Timedelta(hours=5)).normalize(),
                      "turno": "M" if 5 <= hora < 13 else "T" if 13 <= hora < 21 else "N",
                      "temporada": momento.year, "fruta": fruta, "valor": float(valor),
                      "invalido": (invalidos or {}).get(primer_id + k)})
    return pd.DataFrame(filas, columns=COLUMNAS_SERIE)


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="Norte", pais="Argentina", cliente=Cliente(nombre="X", pais="Argentina"))
        linea = Linea(nombre="Línea 1", planta=planta)
        for numero in (1, 2):
            Equipo(codigo=f"N-BOE-0{numero}", nombre=f"BOE {numero}", tipo="BOE", numero=numero, linea=linea)
        sesion.add(planta)
        sesion.commit()
        yield sesion


def test_motivo_invalido():
    assert motivo_invalido(None) is None and motivo_invalido("fecha corregida por el Día") is None
    assert motivo_invalido("fecha corregida por el Día; copiada de la otra primaria") == (
        PREFIJO_INVALIDO + "copiada de la otra primaria")
    assert motivo_invalido("fuera de rango físico; valor pegado, sin datos crudos").startswith(
        PREFIJO_INVALIDO + "fuera de rango físico; valor pegado")


def test_fase1_igual_que_limites_imr_y_periodo():
    datos = serie([85, 87, 84, 86, 88, 83, 85, 86])
    calculo = calcular_limites_fase1(datos)
    esperado = limites_imr(datos["valor"])
    assert calculo["n"] == 8 and calculo["lc"] == pytest.approx(esperado["lc"])
    assert (calculo["lcs"], calculo["mr_lcs"]) == (pytest.approx(esperado["lcs"]), pytest.approx(esperado["mr_lcs"]))
    assert calculo["excluidos"] == []
    parcial = calcular_limites_fase1(datos, hasta="2025-07-01")          # los de las 06:00 a las 20:00 del 01/07
    assert parcial["n"] == 8 and parcial["base_hasta"].isoformat() == "2025-07-01"
    assert calcular_limites_fase1(datos, desde="2025-07-01", hasta="2025-07-01")["base_desde"].isoformat() == "2025-07-01"


def test_exclusiones_con_motivo_obligatorio():
    datos = serie([85, 87, 84, 120, 86, 88])
    calculo = calcular_limites_fase1(datos, excluir={4: "muestra mal tomada (balde mezclado)"})
    assert calculo["n"] == 5
    assert calculo["lc"] == pytest.approx(limites_imr([85, 87, 84, 86, 88])["lc"])
    assert calculo["excluidos"][0] | {"fecha_hora": None} == {"tabla": "analisis_scott", "dato_id": 4,
                                                              "fecha_hora": None, "valor": 120.0,
                                                              "motivo": "muestra mal tomada (balde mezclado)"}
    with pytest.raises(ValueError, match="hace falta un motivo"):
        calcular_limites_fase1(datos, excluir={4: " "})
    with pytest.raises(ValueError, match="no están en el período"):
        calcular_limites_fase1(datos, excluir={99: "x"})
    with pytest.raises(ValueError, match="un solo equipo"):
        calcular_limites_fase1(pd.concat([datos, serie([1, 2], equipo_id=2)]))


def test_los_datos_invalidos_se_excluyen_con_su_motivo():
    datos = serie([1.0, 0.9, 5.0, 1.1, 1.0], invalidos={3: PREFIJO_INVALIDO + "fuera de rango físico"},
                  tipo="Centrífuga primaria", tabla="muestra_centrifuga")
    calculo = calcular_limites_fase1(datos)
    assert calculo["n"] == 4 and calculo["excluidos"][0]["motivo"] == PREFIJO_INVALIDO + "fuera de rango físico"
    assert calculo["excluidos"][0]["tabla"] == "muestra_centrifuga"


def test_congelar_con_historial_y_exclusiones(sesion):
    equipo = sesion.scalars(select(Equipo)).first()
    primero = congelar_limites(sesion, "eficiencia", equipo.id, "naranja", calcular_limites_fase1(
        serie([85, 87, 84, 120, 86], equipo_id=equipo.id), excluir={4: "error de carga"}), "prueba")
    assert primero.vigente and primero.reglas == "1,2,3,5,MR" and primero.tipo_carta == "I-MR"
    assert [e.motivo for e in primero.exclusiones] == ["error de carga"]
    segundo = congelar_limites(sesion, "eficiencia", equipo.id, "naranja",
                               calcular_limites_fase1(serie([80, 82, 81], equipo_id=equipo.id)), "prueba",
                               reglas="1,MR", comentario="nuevo período base")
    sesion.refresh(primero)
    assert not primero.vigente and segundo.vigente                          # el anterior queda como historial
    assert limites_vigentes(sesion, "eficiencia", equipo.id, "naranja").id == segundo.id
    assert limites_vigentes(sesion, "eficiencia", equipo.id, "limón") is None
    assert len(sesion.scalars(select(LimitesControl)).all()) == 2
    assert len(sesion.scalars(select(LimitesExclusion)).all()) == 1
    with pytest.raises(ValueError, match="no válida"):
        congelar_limites(sesion, "temperatura", equipo.id, None, calcular_limites_fase1(serie([1, 2, 3])), "x")
    with pytest.raises(ValueError, match="motivo"):
        LimitesExclusion(motivo="")


def test_congelar_temporada_por_equipo(sesion):
    uno, dos = sesion.scalars(select(Equipo).order_by(Equipo.numero)).all()
    datos = pd.concat([serie([85, 86, 84], equipo_id=uno.id), serie([80, 81], equipo_id=dos.id, primer_id=10),
                       serie([70, 71, 72], equipo_id=uno.id, inicio=datetime(2026, 7, 1, 6), primer_id=20)])
    registros = congelar_temporada(sesion, {"eficiencia": datos}, 2025, ["eficiencia", "brix"], "prueba")
    assert sorted((r.equipo_id, r.n) for r in registros) == [(uno.id, 3), (dos.id, 2)]      # 2026 no entra
    assert registros[0].base_desde.isoformat() == "2025-07-01"


def test_series_boe_y_centrifugas():
    analisis = pd.DataFrame({
        "analisis_id": [1, 2], "lugar": ["Entrada", "Salida"], "eboe": [2, 2],
        "fecha_hora": pd.to_datetime(["2025-07-01 10:00"] * 2), "fecha_produccion": pd.to_datetime(["2025-07-01"] * 2),
        "turno": ["M", "M"], "temporada": [2025, 2025], "fruta": ["N Val S/R"] * 2, "eficiencia_par": [0.85, 0.85],
        "aceite_fruta": [7.0, 1.05]})
    emulsion = pd.DataFrame({"analisis_id": [3], "eboe": [2], "fecha_hora": pd.to_datetime(["2025-07-01 10:00"]),
                             "fecha_produccion": pd.to_datetime(["2025-07-01"]), "turno": ["M"], "temporada": [2025],
                             "aceite_emulsion": [1.0], "brix": [4.0], "ph": [None]})
    series = series_boe(analisis, emulsion, {2: (20, "BOE Nº 2")})
    assert series["eficiencia"]["valor"].tolist() == [85.0] and series["eficiencia"]["equipo_id"].tolist() == [20]
    assert series["aceite_salida"]["dato_id"].tolist() == [2]
    assert series["brix"]["fruta"].tolist() == ["naranja"]                 # la fruta sale de la Entrada
    assert series["ph"].empty
    muestras = pd.DataFrame({
        "id": [7, 8, 9], "codigo": ["C1"] * 3, "equipo": ["ESE"] * 3, "tipo": ["Centrífuga primaria"] * 3,
        "fecha_hora": pd.to_datetime(["2025-07-01 10:00"] * 3), "fecha_produccion": pd.to_datetime(["2025-07-01"] * 3),
        "turno": ["M"] * 3, "temporada": [2025] * 3, "fruta": ["N Val S/R"] * 3,
        "fase": ["fase_pesada", "descarga", "emulsion"], "aceite_pct": [0.1, 0.3, 1.0], "cp_pesada_bar": [6.0, None, None],
        "revisar": [None, "copiada de la otra primaria", None]})
    centrifugas = series_centrifugas(muestras, {"C1": 5})
    assert centrifugas["cp_pesada"]["valor"].tolist() == [6.0]
    descarga = centrifugas["aceite_descarga"]
    assert descarga["dato_id"].tolist() == [8] and descarga["invalido"].iloc[0].startswith(PREFIJO_INVALIDO)
    assert set(descarga.columns) == set(COLUMNAS_SERIE) and descarga["tabla"].iloc[0] == "muestra_centrifuga"
