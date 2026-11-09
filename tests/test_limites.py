"""Pruebas de core/limites.py: series, subgrupos por turno, fase I (X̄-S e I-MR) con exclusiones y congelado."""

from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.limites import (COLUMNAS_SERIE, IMR, PREFIJO_INVALIDO, SIN_LIMITE_INFERIOR, XBAR_S, ambito_de,
                          calcular_limites_fase1, congelar_limites, congelar_temporada, limites_vigentes,
                          motivo_invalido, series_boe, series_centrifugas, subgrupos_por_turno, texto_lci)
from core.models import Cliente, Equipo, Linea, LimitesControl, LimitesExclusion, Planta
from core.spc import limites_imr, limites_xbar_s, limites_xbar_s_para


def serie(valores, equipo_id=1, inicio=datetime(2025, 7, 1, 6), invalidos=None, tipo="BOE", fruta="naranja",
          tabla="analisis_scott", primer_id=1, paso_h=2, planta_id=None):
    """Una serie inventada (COLUMNAS_SERIE), un dato cada `paso_h` horas. Sin equipo_id: serie de planta."""
    filas = []
    for k, valor in enumerate(valores):
        momento = inicio + timedelta(hours=paso_h * k)
        hora = momento.hour
        filas.append({"tabla": tabla, "dato_id": primer_id + k, "ambito": ambito_de(equipo_id, planta_id),
                      "equipo_id": equipo_id, "planta_id": planta_id, "equipo": f"E{equipo_id}", "tipo": tipo,
                      "fecha_hora": pd.Timestamp(momento),
                      "fecha_produccion": (pd.Timestamp(momento) - pd.Timedelta(hours=5)).normalize(),
                      "turno": "M" if 5 <= hora < 13 else "T" if 13 <= hora < 21 else "N",
                      "temporada": momento.year, "fruta": fruta, "valor": float(valor),
                      "invalido": (invalidos or {}).get(primer_id + k)})
    return pd.DataFrame(filas, columns=COLUMNAS_SERIE)


def turnos(valores_por_turno, equipo_id=1, inicio=datetime(2025, 7, 1, 6), primer_id=1, **opciones):
    """Una serie con varios turnos seguidos (8 h cada uno), con las muestras de cada turno cada 2 h."""
    partes, siguiente = [], primer_id
    for k, valores in enumerate(valores_por_turno):
        partes.append(serie(valores, equipo_id, inicio + timedelta(hours=8 * k), primer_id=siguiente, **opciones))
        siguiente += len(valores)
    return pd.concat(partes, ignore_index=True)


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


def test_motivo_invalido_y_copiada_solo_en_titulaciones():
    assert motivo_invalido(None) is None and motivo_invalido(float("nan")) is None
    assert motivo_invalido("fecha corregida por el Día") is None
    assert motivo_invalido("fecha corregida por el Día; copiada de la otra primaria") == (
        PREFIJO_INVALIDO + "copiada de la otra primaria")
    assert motivo_invalido("copiada de la otra primaria", titulacion=False) is None          # la CP no se copia
    assert motivo_invalido("copiada de la otra primaria; fuera de rango físico", titulacion=False) == (
        PREFIJO_INVALIDO + "fuera de rango físico")


def test_ambito_y_lci_para_mostrar():
    assert ambito_de(5) == "equipo:5" and ambito_de(None, 8) == "planta:8" and ambito_de(float("nan"), 8) == "planta:8"
    with pytest.raises(ValueError, match="equipo o una planta"):
        ambito_de()
    assert texto_lci(-0.25) == SIN_LIMITE_INFERIOR and texto_lci(0.49, 2, "kg/MT") == "0,49 kg/MT"
    assert texto_lci(None) == "—"


def test_subgrupos_por_turno():
    datos = turnos([[84, 86, 85], [90]], invalidos={2: PREFIJO_INVALIDO + "valor pegado"})
    sub = subgrupos_por_turno(datos)
    assert sub["n"].tolist() == [2, 1] and sub["media"].tolist() == [84.5, 90]             # el 86 es inválido
    assert sub["desvio"].iloc[0] == pytest.approx(pd.Series([84, 85]).std()) and pd.isna(sub["desvio"].iloc[1])
    assert sub["datos"].iloc[0] == [("analisis_scott", 1), ("analisis_scott", 3)]
    assert sub["turno"].tolist() == ["M", "T"] and sub["fecha_hora"].iloc[1] == pd.Timestamp("2025-07-01 14:00")


def test_fase1_xbar_s_igual_que_spc():
    datos = turnos([[85, 87, 84, 86], [88, 83], [85, 86, 84], [86]])
    calculo = calcular_limites_fase1(datos)
    sub = subgrupos_por_turno(datos)
    esperado = limites_xbar_s(sub["media"], sub["desvio"].astype(float), sub["n"])
    assert calculo["tipo_carta"] == XBAR_S and calculo["n"] == 10 and calculo["n_subgrupos"] == 4
    assert (calculo["lc"], calculo["sigma"]) == (pytest.approx(esperado["lc"]), pytest.approx(esperado["sigma"]))
    tipico = limites_xbar_s_para(esperado["lc"], esperado["sigma"], 2)                    # mediana de 4, 2, 3, 1 → 2
    assert calculo["n_subgrupo"] == 2 and calculo["lcs"] == pytest.approx(tipico["lcs"])
    assert calculo["mr_lcs"] == pytest.approx(tipico["s_lcs"])                           # LCS de la carta S
    imr = calcular_limites_fase1(datos, tipo_carta=IMR)
    assert imr["lc"] == pytest.approx(limites_imr(datos["valor"])["lc"]) and imr["n_subgrupo"] == 1
    with pytest.raises(ValueError, match="no válido"):
        calcular_limites_fase1(datos, tipo_carta="X-R")


def test_exclusiones_con_motivo_obligatorio():
    datos = turnos([[85, 87, 84], [120, 86, 88]])
    calculo = calcular_limites_fase1(datos, excluir={4: "muestra mal tomada (balde mezclado)"})
    assert calculo["n"] == 5 and calculo["excluidos"][0]["motivo"] == "muestra mal tomada (balde mezclado)"
    assert calculo["excluidos"][0]["valor"] == 120
    with pytest.raises(ValueError, match="hace falta un motivo"):
        calcular_limites_fase1(datos, excluir={4: " "})
    with pytest.raises(ValueError, match="no están en el período"):
        calcular_limites_fase1(datos, excluir={99: "x"})
    with pytest.raises(ValueError, match="un solo equipo"):
        calcular_limites_fase1(pd.concat([datos, serie([1, 2], equipo_id=2)]))
    periodo = calcular_limites_fase1(turnos([[85, 86], [84, 87], [80, 81]], inicio=datetime(2025, 7, 1, 6)),
                                     hasta="2025-07-01")
    assert periodo["base_hasta"].isoformat() == "2025-07-01"


def test_los_datos_invalidos_se_excluyen_con_su_motivo():
    datos = turnos([[1.0, 0.9], [5.0, 1.1, 1.0]], invalidos={3: PREFIJO_INVALIDO + "fuera de rango físico"},
                   tipo="Centrífuga primaria", tabla="muestra_centrifuga")
    calculo = calcular_limites_fase1(datos)
    assert calculo["n"] == 4 and calculo["excluidos"][0]["motivo"] == PREFIJO_INVALIDO + "fuera de rango físico"


def test_congelar_con_historial_exclusiones_y_ambito(sesion):
    equipo = sesion.scalars(select(Equipo)).first()
    planta = sesion.scalars(select(Planta)).one()
    datos = turnos([[85, 87], [84, 120], [86, 85]], equipo_id=equipo.id)
    primero = congelar_limites(sesion, "eficiencia", calcular_limites_fase1(datos, excluir={4: "error de carga"}),
                               "prueba", "naranja", equipo_id=equipo.id)
    assert primero.vigente and primero.reglas == "1,2,3,5,S" and primero.tipo_carta == XBAR_S
    assert [e.motivo for e in primero.exclusiones] == ["error de carga"]
    segundo = congelar_limites(sesion, "eficiencia", calcular_limites_fase1(datos), "prueba", "naranja",
                               equipo_id=equipo.id, reglas="1", comentario="nuevo período base")
    sesion.refresh(primero)
    assert not primero.vigente and segundo.vigente                          # el anterior queda como historial
    assert limites_vigentes(sesion, "eficiencia", "naranja", equipo_id=equipo.id).id == segundo.id
    assert limites_vigentes(sesion, "eficiencia", "limón", equipo_id=equipo.id) is None
    ph = congelar_limites(sesion, "ph", calcular_limites_fase1(turnos([[5.5, 5.6], [5.4, 5.7]], equipo_id=None,
                                                                     planta_id=planta.id)),
                          "prueba", "naranja", planta_id=planta.id)
    assert (ph.planta_id, ph.equipo_id) == (planta.id, None)
    assert limites_vigentes(sesion, "ph", "naranja", planta_id=planta.id).id == ph.id
    imr = congelar_limites(sesion, "brix", calcular_limites_fase1(datos, tipo_carta=IMR), "prueba",
                           equipo_id=equipo.id)
    assert imr.reglas == "1,2,3,5,MR"
    assert len(sesion.scalars(select(LimitesControl)).all()) == 4
    assert len(sesion.scalars(select(LimitesExclusion)).all()) == 1
    with pytest.raises(ValueError, match="uno de los dos"):
        congelar_limites(sesion, "ph", calcular_limites_fase1(datos), "x")
    with pytest.raises(ValueError, match="no válida"):
        congelar_limites(sesion, "temperatura", calcular_limites_fase1(datos), "x", equipo_id=equipo.id)
    with pytest.raises(ValueError, match="motivo"):
        LimitesExclusion(motivo="")


def test_congelar_temporada_por_ambito(sesion):
    uno, dos = sesion.scalars(select(Equipo).order_by(Equipo.numero)).all()
    planta = sesion.scalars(select(Planta)).one()
    datos = pd.concat([turnos([[85, 86], [84, 87]], equipo_id=uno.id),
                       turnos([[80, 81], [82]], equipo_id=dos.id, primer_id=10),
                       turnos([[70, 71]], equipo_id=uno.id, inicio=datetime(2026, 7, 1, 6), primer_id=20)])
    ph = turnos([[5.5, 5.6, 5.4], [5.7, 5.5]], equipo_id=None, planta_id=planta.id, primer_id=40)
    registros = congelar_temporada(sesion, {"eficiencia": datos, "ph": ph}, 2025, ["eficiencia", "ph", "brix"],
                                   "prueba")
    assert sorted((r.variable, r.equipo_id, r.planta_id, r.n) for r in registros) == [
        ("eficiencia", uno.id, None, 4), ("eficiencia", dos.id, None, 3), ("ph", None, planta.id, 5)]   # 2026 no


def test_series_boe_y_centrifugas():
    analisis = pd.DataFrame({
        "analisis_id": [1, 2], "lugar": ["Entrada", "Salida"], "eboe": [2, 2],
        "fecha_hora": pd.to_datetime(["2025-07-01 10:00"] * 2), "fecha_produccion": pd.to_datetime(["2025-07-01"] * 2),
        "turno": ["M", "M"], "temporada": [2025, 2025], "fruta": ["N Val S/R"] * 2, "eficiencia_par": [0.85, 0.85],
        "aceite_fruta": [7.0, 1.05]})
    emulsion = pd.DataFrame({"analisis_id": [3], "eboe": [2], "fecha_hora": pd.to_datetime(["2025-07-01 10:00"]),
                             "fecha_produccion": pd.to_datetime(["2025-07-01"]), "turno": ["M"], "temporada": [2025],
                             "aceite_emulsion": [1.0], "brix": [4.0], "ph": [5.5]})
    series = series_boe(analisis, emulsion, {2: (20, "BOE Nº 2")}, planta_id=8)
    assert series["eficiencia"]["valor"].tolist() == [85.0] and series["eficiencia"]["ambito"].tolist() == ["equipo:20"]
    assert series["aceite_salida"]["dato_id"].tolist() == [2]
    assert series["brix"]["fruta"].tolist() == ["naranja"]                 # la fruta sale de la Entrada
    assert series["ph"]["ambito"].tolist() == ["planta:8"] and series["ph"]["equipo"].tolist() == ["Planta"]
    muestras = pd.DataFrame({
        "id": [7, 8, 9], "codigo": ["C1"] * 3, "equipo": ["ESE"] * 3, "tipo": ["Centrífuga primaria"] * 3,
        "fecha_hora": pd.to_datetime(["2025-07-01 10:00"] * 3), "fecha_produccion": pd.to_datetime(["2025-07-01"] * 3),
        "turno": ["M"] * 3, "temporada": [2025] * 3, "fruta": ["N Val S/R"] * 3,
        "fase": ["fase_pesada", "descarga", "emulsion"], "aceite_pct": [0.1, 0.3, 1.0], "cp_pesada_bar": [6.0, None, None],
        "revisar": ["copiada de la otra primaria", "copiada de la otra primaria", None]})
    centrifugas = series_centrifugas(muestras, {"C1": 5}, planta_id=8)
    cp = centrifugas["cp_pesada"]
    assert cp["valor"].tolist() == [6.0] and cp["invalido"].isna().all()           # la copia no invalida la CP
    descarga = centrifugas["aceite_descarga"]
    assert descarga["dato_id"].tolist() == [8] and descarga["invalido"].iloc[0].startswith(PREFIJO_INVALIDO)
    assert set(descarga.columns) == set(COLUMNAS_SERIE) and descarga["tabla"].iloc[0] == "muestra_centrifuga"
