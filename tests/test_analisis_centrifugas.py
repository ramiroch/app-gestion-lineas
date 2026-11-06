"""Pruebas de core/analisis_centrifugas.py con datos inventados."""

from datetime import datetime, time

import pandas as pd
import pytest

from core.analisis_centrifugas import (A_DEFINIR, CALCULADO, INCOMPLETO, NO_APLICA, PARALELO, RECIRCULADO, SIN_DATOS,
                                       SIN_TITULACION, UNA_SOLA, agua_por_eboe, balance_por_muestreo,
                                       caudal_nominal_primaria, integrar_descarga, perdida_descarga_temporada,
                                       perdida_por_tonelada, por_muestreo, primarias_por_turno, rangos_centrifugas,
                                       relacion_boe, resumen_por_temporada, tabla_muestras, valores_por_variable)
from core.centrifugas import aceite_pct, balance_centrifuga, caudal_descarga_lh, densidad
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.estadistica import cuartiles
from core.models import Cliente, Equipo, Linea, MuestraCentrifuga, Planta, RangoReferencia, Turno

EQUIPOS = {  # código: (nombre, tipo, volumen, destino)
    "CEN-1": ("ESE 230 (1)", "Centrífuga primaria", 16, "recircula al BOE"),
    "CEN-2": ("ESE 230 (2)", "Centrífuga primaria", 16, "recircula al BOE"),
    "SEC-B": ("ESB 130", "Centrífuga secundaria", None, "canal (pérdida)"),
    "SEC-M": ("MSD 50", "Centrífuga secundaria", 2, "canal (pérdida)"),
    "PUL": ("OSD 60", "Pulidora", None, "no aplica"),
}
NORMAL = {"emulsion": 0.8, "fase_liviana": 75, "fase_pesada": 0.05, "descarga": 0.2}


def muestra(codigo, fecha_hora, fase, aceite=None, turno="T", temporada=2026, **datos):
    nombre, tipo, volumen, destino = EQUIPOS[codigo]
    return {"id": None, "codigo": codigo, "equipo": nombre, "tipo": tipo, "volumen_descarga_l": volumen,
            "destino": destino, "fecha_hora": pd.Timestamp(fecha_hora), "temporada": temporada, "proceso": 1,
            "fase": fase, "fruta": "N Val S/R", "caudal_lh": None, "aceite_pct": aceite, "titulada": True,
            "solidos_pct": None, "solidos_marca": None, "cp_liviana_bar": None, "cp_liviana_marca": None,
            "cp_pesada_bar": None, "t_descarga_s": None, "revisar": None, "copiada": False,
            "fecha_produccion": pd.Timestamp(fecha_hora).normalize(), "turno": turno} | datos


def muestreo(codigo, fecha_hora, caudal=15000.0, t=500.0, aceites=None, turno="T", temporada=2026, copiadas=()):
    """Las 4 fases de una primaria (aceites en %)."""
    aceites = NORMAL | (aceites or {})
    filas = []
    for fase, aceite in aceites.items():
        extra = {"caudal_lh": caudal} if fase == "emulsion" else {"t_descarga_s": t} if fase == "descarga" else {}
        if fase in copiadas:
            extra |= {"copiada": True, "revisar": "copiada de la otra primaria"}
        filas.append(muestra(codigo, fecha_hora, fase, aceite, turno, temporada, **extra))
    return filas


def tabla(*grupos):
    return pd.DataFrame([fila for grupo in grupos for fila in grupo])


# ---------------------------------------------------------------- balance por muestreo

def test_balance_de_la_primaria_igual_que_core_centrifugas():
    balance = balance_por_muestreo(por_muestreo(tabla(muestreo("CEN-1", "2026-07-01 14:00"))))
    fila = balance.iloc[0]
    esperado = balance_centrifuga(caudal_emulsion=15000, aceite_emulsion_pct=0.8, aceite_liviana_pct=75,
                                  aceite_pesada_pct=0.05, aceite_descarga_pct=0.2, volumen_descarga_l=16,
                                  t_descarga_s=500)
    assert fila["eficiencia_pct"] == pytest.approx(esperado["eficiencia"].pct)
    assert fila["perdida_descarga_kgh"] == pytest.approx(esperado["perdida_descarga_kgh"])
    assert (fila["estado_eficiencia"], fila["estado_pesada"], fila["estado_descarga"]) == (CALCULADO, RECIRCULADO,
                                                                                         CALCULADO)
    assert fila["recirculado_kgh"] == pytest.approx(esperado["perdida_pesada_kgh"])
    assert pd.isna(fila["perdida_pesada_proceso_kgh"])             # recircula al BOE: no es pérdida del proceso
    assert not fila["fuera_fisico"]


def test_secundaria_pulidora_y_estados():
    hora = "2026-08-10 09:00"
    datos = tabla(muestreo("CEN-1", hora),
                  [muestra("SEC-M", hora, "fase_liviana", 95), muestra("SEC-M", hora, "descarga", 2.0, t_descarga_s=500)],
                  [muestra("SEC-B", hora, "fase_liviana", 97), muestra("SEC-B", hora, "fase_pesada", 0.1),
                   muestra("SEC-B", hora, "descarga", 0.3, t_descarga_s=600)],
                  [muestra("PUL", hora, "descarga", None, t_descarga_s=1800, titulada=False),
                   muestra("PUL", hora, "fase_liviana", None, cp_liviana_bar=0, titulada=False)])
    balance = balance_por_muestreo(por_muestreo(datos)).set_index("codigo")
    msd = balance.loc["SEC-M"]
    assert (msd["estado_eficiencia"], msd["estado_pesada"]) == (SIN_DATOS, SIN_DATOS)   # no se muestrea la pesada
    assert msd["perdida_descarga_kgh"] == pytest.approx(0.02 * densidad(0.02) * caudal_descarga_lh(2, 500))
    esb = balance.loc["SEC-B"]                                          # con fase pesada pero sin volumen
    assert (esb["estado_eficiencia"], esb["estado_pesada"], esb["estado_descarga"]) == (A_DEFINIR, A_DEFINIR, A_DEFINIR)
    pul = balance.loc["PUL"]
    assert (pul["estado_eficiencia"], pul["estado_pesada"], pul["estado_descarga"]) == (NO_APLICA, NO_APLICA,
                                                                                      SIN_TITULACION)


def test_secundaria_con_fase_pesada_y_volumen_usa_la_alimentacion_de_las_primarias():
    hora = "2025-07-10 09:00"
    datos = tabla(muestreo("CEN-1", hora, temporada=2025), muestreo("CEN-2", hora, temporada=2025),
                  [muestra("SEC-M", hora, f, a, temporada=2025, **({"t_descarga_s": 500} if f == "descarga" else {}))
                   for f, a in (("fase_liviana", 95), ("fase_pesada", 0.5), ("descarga", 0.8))])
    balance = balance_por_muestreo(por_muestreo(datos)).set_index("codigo")
    liviana = balance.loc["CEN-1", "caudal_liviana"] + balance.loc["CEN-2", "caudal_liviana"]
    esperado = balance_centrifuga(caudal_emulsion=liviana, aceite_emulsion_pct=75, aceite_liviana_pct=95,
                                  aceite_pesada_pct=0.5, aceite_descarga_pct=0.8, volumen_descarga_l=2, t_descarga_s=500)
    secundaria = balance.loc["SEC-M"]
    assert secundaria["eficiencia_pct"] == pytest.approx(esperado["eficiencia"].pct)
    assert secundaria["perdida_pesada_proceso_kgh"] == pytest.approx(esperado["perdida_pesada_kgh"])   # va al canal


def test_fuera_de_rango_fisico_se_marca_y_muestreo_incompleto():
    datos = tabla(muestreo("CEN-1", "2026-07-01 14:00", t=40, aceites={"descarga": 10.0}),
                  muestreo("CEN-2", "2026-07-01 16:00")[:3])                       # sin descarga
    balance = balance_por_muestreo(por_muestreo(datos)).set_index("codigo")
    assert balance.loc["CEN-1", "eficiencia_pct"] < 0 and balance.loc["CEN-1", "fuera_fisico"]
    assert balance.loc["CEN-2", "estado_eficiencia"] == INCOMPLETO
    assert balance.loc["CEN-2", "estado_descarga"] == SIN_DATOS


# ---------------------------------------------------------------- primarias en marcha y agua por eBOE

def _turnos():
    return tabla(
        muestreo("CEN-1", "2026-07-01 13:10", 14000), muestreo("CEN-2", "2026-07-01 15:10", 15000),      # paralelo
        muestreo("CEN-1", "2026-07-01 17:10", 13000),
        muestreo("CEN-1", "2026-07-01 21:10", 15000, turno="N"), muestreo("CEN-2", "2026-07-01 23:10", 17000, turno="N"),
        muestreo("CEN-2", "2026-09-02 07:10", 19000, turno="M"), muestreo("CEN-2", "2026-09-02 09:10", 18500, turno="M"))


def test_primarias_por_turno():
    turnos = primarias_por_turno(_turnos(), caudal_maximo=16000).set_index(["fecha_produccion", "turno"])
    tarde = turnos.loc[(pd.Timestamp("2026-07-01"), "T")]
    assert (tarde["modo"], tarde["caudal_total_lh"], tarde["inferido"]) == (PARALELO, 13500 + 15000, True)
    assert turnos.loc[(pd.Timestamp("2026-07-01"), "N"), "modo"] == UNA_SOLA        # la (2) supera el nominal
    manana = turnos.loc[(pd.Timestamp("2026-09-02"), "M")]
    assert (manana["modo"], manana["primarias"], manana["caudal_total_lh"]) == (UNA_SOLA, "ESE 230 (2)", 18750)


def test_caudal_nominal_de_los_rangos():
    assert caudal_nominal_primaria({("caudal_emulsion", "Centrífuga primaria"): {"maximo": 16000}}) == 16000
    with pytest.raises(ValueError, match="Caudal de emulsión"):
        caudal_nominal_primaria({})


def test_agua_por_eboe():
    datos = _turnos()
    balance = balance_por_muestreo(por_muestreo(datos))
    turnos = primarias_por_turno(datos, 16000)
    analisis = pd.DataFrame({"fecha_produccion": pd.Timestamp("2026-07-01"), "turno": "T", "lugar": "Entrada",
                             "eboe": [1, 2, 1], "caudal_fase_pesada": [6.0, 6.4, 6.2]})
    agua = agua_por_eboe(balance, turnos, analisis).set_index(["fecha_produccion", "turno"])
    tarde = agua.loc[(pd.Timestamp("2026-07-01"), "T")]
    pesadas = balance[balance["turno"] == "T"].groupby("equipo")["caudal_pesada"].median().sum()
    assert tarde["eboe_en_marcha"] == 2 and not tarde["eboe_por_defecto"]
    assert tarde["agua_por_eboe_m3h"] == pytest.approx(pesadas / 1000 / 2)
    assert (tarde["agua_lab_m3h"], tarde["n_lab"]) == (6.2, 3)
    manana = agua.loc[(pd.Timestamp("2026-09-02"), "M")]
    assert manana["eboe_en_marcha"] == 3 and manana["eboe_por_defecto"]              # sin análisis: 3


# ---------------------------------------------------------------- resumen por temporada y equipo

RANGOS = {("aceite_emulsion", "Centrífuga primaria"): {"minimo": 0.7, "maximo": 1.1, "unidad": "%", "fuente": "P",
                                                       "genera_alerta": True},
          ("cp_pesada", "Centrífuga primaria"): {"minimo": 2, "maximo": 3, "unidad": "bar", "fuente": "GEA",
                                                 "genera_alerta": False}}


def test_resumen_n_cuartiles_de_minitab_y_fuera_de_rango():
    aceites = [0.6, 0.8, 0.9, 1.0, 1.2]
    datos = tabla(*[muestreo("CEN-1", f"2026-07-01 {8 + 2 * k}:00", aceites={"emulsion": a}) for k, a in
                    enumerate(aceites)])
    resumen = resumen_por_temporada(valores_por_variable(datos, balance_por_muestreo(por_muestreo(datos))), RANGOS)
    fila = resumen[resumen["variable"] == "aceite_emulsion"].iloc[0]
    assert (fila["q1"], fila["mediana"], fila["q3"]) == pytest.approx(cuartiles(aceites))
    assert (fila["n"], fila["n_fuera"], fila["pct_fuera"]) == (5, 2, 40)
    assert (fila["fuente"], fila["genera_alerta"], fila["pocos_datos"]) == ("P", True, False)


def test_cp_no_medible_aparte_y_copiadas_afuera():
    datos = tabla(muestreo("CEN-1", "2026-07-01 08:00"), muestreo("CEN-2", "2026-07-01 08:00", copiadas=("fase_pesada",)),
                  [muestra("PUL", "2026-07-01 08:00", "fase_liviana", cp_liviana_bar=0,
                           cp_liviana_marca="no medible (valor cercano a 0 o manómetro a revisar)"),
                   muestra("PUL", "2026-07-01 10:00", "fase_liviana", cp_liviana_bar=0.4)])
    balance = balance_por_muestreo(por_muestreo(datos))
    resumen = resumen_por_temporada(valores_por_variable(datos, balance), {}).set_index(["equipo", "variable"])
    assert (resumen.loc[("OSD 60", "cp_liviana"), "n"], resumen.loc[("OSD 60", "cp_liviana"), "n_no_medible"]) == (1, 1)
    assert ("ESE 230 (2)", "aceite_pesada") not in resumen.index                       # copiada: afuera
    assert ("ESE 230 (2)", "eficiencia") not in resumen.index                          # el balance la usa
    assert resumen.loc[("ESE 230 (2)", "perdida_descarga"), "n"] == 1                  # la descarga no es copia
    con = resumen_por_temporada(valores_por_variable(datos, balance, incluir_copiadas=True), {})
    assert ((con["equipo"] == "ESE 230 (2)") & (con["variable"] == "aceite_pesada")).any()


def test_pocos_datos_con_n_menor_que_3():
    datos = tabla(muestreo("CEN-1", "2026-07-01 08:00"))
    resumen = resumen_por_temporada(valores_por_variable(datos, balance_por_muestreo(por_muestreo(datos))), RANGOS)
    assert resumen["pocos_datos"].all()


# ---------------------------------------------------------------- pérdida en descarga estimada

def test_integracion_con_maximo_de_4_horas():
    datos = tabla(muestreo("CEN-1", "2026-07-01 08:00"), muestreo("CEN-1", "2026-07-01 10:00"),
                  muestreo("CEN-1", "2026-07-01 20:00"), muestreo("CEN-2", "2026-07-01 08:00", copiadas=("descarga",)))
    balance = balance_por_muestreo(por_muestreo(datos))
    integrado = integrar_descarga(balance)
    assert integrado["horas"].tolist() == [2, 4, 0]                  # 2 h, 10 h → 4 h, el último no suma
    assert "ESE 230 (2)" not in integrado["equipo"].tolist()           # descarga copiada: afuera
    kgh = balance["perdida_descarga_kgh"].iloc[0]
    por_temporada = perdida_descarga_temporada(balance).set_index("equipo").loc["ESE 230 (1)"]
    assert (por_temporada["kg"], por_temporada["horas"], por_temporada["n"]) == (pytest.approx(6 * kgh), 6, 3)
    assert perdida_descarga_temporada(balance, incluir_copiadas=True)["equipo"].tolist() == ["ESE 230 (1)",
                                                                                            "ESE 230 (2)"]


def test_perdida_por_tonelada_por_dia():
    datos = tabla(muestreo("CEN-1", "2026-07-01 08:00"), muestreo("CEN-1", "2026-07-01 11:00"),
                  muestreo("CEN-1", "2026-07-02 08:00"), muestreo("CEN-1", "2026-07-02 09:00"))
    balance = balance_por_muestreo(por_muestreo(datos))
    produccion = pd.DataFrame({"fecha_produccion": pd.to_datetime(["2026-07-01", "2026-07-01"]), "fruta_t": [300, 200]})
    tabla_dia = perdida_por_tonelada(balance, produccion).set_index("fecha_produccion")
    kgh = balance["perdida_descarga_kgh"].iloc[0]
    primer_dia = tabla_dia.loc[pd.Timestamp("2026-07-01")]
    assert primer_dia["kg"] == pytest.approx(kgh * 7)                    # 3 h + 4 h (hasta el 02/07 08:00: tope 4 h)
    assert primer_dia["kg_mt"] == pytest.approx(kgh * 7 / 500)
    assert pd.isna(tabla_dia.loc[pd.Timestamp("2026-07-02"), "kg_mt"])    # sin toneladas: no se calcula


# ---------------------------------------------------------------- relación con el BOE

def test_relacion_con_el_boe_por_dia():
    datos = tabla(muestreo("CEN-1", "2026-07-01 08:00", aceites={"emulsion": 0.8}),
                  muestreo("CEN-2", "2026-07-01 10:00", aceites={"emulsion": 1.0}))
    dia = pd.Timestamp("2026-07-01")
    emulsion_boe = pd.DataFrame({"fecha_produccion": [dia] * 3, "aceite_emulsion": [1.1, 1.2, None]})
    fruta_boe = pd.DataFrame({"fecha_produccion": [dia] * 4, "lugar": ["Entrada", "Salida", "Entrada", "Salida"],
                              "aceite_fruta": [7.0, 0.7, 7.5, 0.9], "programa": ["N8", None, "N9", None]})
    fila = relacion_boe(datos, emulsion_boe, fruta_boe).iloc[0]
    assert (fila["aceite_emulsion_cen"], fila["n_cen"]) == (pytest.approx(0.9), 2)
    assert (fila["aceite_emulsion_boe"], fila["n_boe"]) == (pytest.approx(1.15), 2)
    assert (fila["aceite_salida"], fila["programa_medio"]) == (pytest.approx(0.8), 8.5)


# ---------------------------------------------------------------- lectura de la base

def test_tabla_muestras_y_rangos_desde_la_base():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="Norte", pais="Argentina", cliente=Cliente(nombre="X", pais="Argentina"),
                        factor_aceite_emulsion=3.45, hora_inicio_dia=time(5, 0))
        planta.turnos = [Turno(nombre="M", hora_inicio=time(5), hora_fin=time(12, 59)),
                         Turno(nombre="T", hora_inicio=time(13), hora_fin=time(20, 59)),
                         Turno(nombre="N", hora_inicio=time(21), hora_fin=time(4, 59))]
        equipo = Equipo(codigo="N-CEN-01", nombre="ESE 230 (1)", tipo="Centrífuga primaria", numero=1,
                        volumen_descarga_l=16, destino_fase_pesada="recircula al BOE",
                        linea=Linea(nombre="Línea 1", planta=planta))
        sesion.add_all([
            MuestraCentrifuga(equipo=equipo, fecha_hora=datetime(2026, 7, 2, 1, 10), fase="emulsion", gramos=3.5,
                              ml=7.0, normalidad=0.1, blanco_ml=0.1, caudal_lh=15000, cargado_por="prueba"),
            MuestraCentrifuga(equipo=equipo, fecha_hora=datetime(2026, 7, 2, 1, 10), fase="fase_pesada", gramos=2.5,
                              ml=3.0, normalidad=0.01, blanco_ml=0.1, revisar="copiada de la otra primaria",
                              cargado_por="prueba"),
            MuestraCentrifuga(equipo=equipo, fecha_hora=datetime(2026, 7, 2, 1, 10), fase="fase_liviana",
                              aceite_pct_medido=75, cargado_por="prueba"),
            RangoReferencia(planta=planta, variable="Aceite en fase liviana", punto_o_equipo="Centrífuga primaria",
                            minimo=75, maximo=85, fuente="GEA 2019"),
            RangoReferencia(planta=planta, variable="Aceite en fase liviana", punto_o_equipo="Centrífuga primaria",
                            fruta="Naranja", minimo=70, maximo=80, fuente="Planilla"),
            RangoReferencia(planta=planta, variable="Intervalo de descarga", punto_o_equipo="Pulidora", minimo=30,
                            maximo=60, unidad="min", fuente="GEA 2019"),
            RangoReferencia(planta=planta, variable="CP fase pesada", punto_o_equipo="Centrífuga primaria", minimo=2,
                            maximo=3, fuente="GEA 2019", genera_alerta=False),
            RangoReferencia(planta=planta, variable="Eficiencia eBOE", punto_o_equipo="BOE (todos)", minimo=80)])
        sesion.commit()
        muestras = tabla_muestras(sesion, planta.id).set_index("fase")
        rangos = rangos_centrifugas(sesion, planta.id)

    assert muestras.loc["emulsion", "aceite_pct"] == pytest.approx(aceite_pct(7.0, 0.1, 0.1, 3.5, 3.45))
    assert muestras.loc["fase_liviana", "aceite_pct"] == 75 and not muestras.loc["fase_liviana", "titulada"]
    assert muestras.loc["fase_pesada", "copiada"] and not muestras.loc["emulsion", "copiada"]
    assert (muestras.loc["emulsion", "fecha_produccion"], muestras.loc["emulsion", "turno"]) == (
        pd.Timestamp("2026-07-01"), "N")                                          # 01:10 → día anterior, turno N
    assert rangos[("aceite_liviana", "Centrífuga primaria")]["fuente"] == "Planilla"     # el de la planta gana
    assert (rangos[("t_descarga", "Pulidora")]["minimo"], rangos[("t_descarga", "Pulidora")]["maximo"]) == (1800, 3600)
    assert rangos[("cp_pesada", "Centrífuga primaria")]["genera_alerta"] is False
    assert len(rangos) == 3                                                       # el del BOE no entra
