"""Pruebas de core/alertas.py (por turno, X̄-S): cada regla, S, rango con el promedio, sin límites, sin
duplicados, histórico, comunes y pH de planta."""

from datetime import date, datetime, time

import pandas as pd
import pytest
from sqlalchemy import select

from core.alertas import (COMENTARIO_COMUN, alertas_de, evaluar_nuevos, leer_reglas, rango_de_alerta,
                          violaciones_rango, violaciones_xbar_s)
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.limites import PREFIJO_INVALIDO, subgrupos_por_turno
from core.models import Alerta, AlertaDato, Cliente, Equipo, Linea, LimitesControl, Planta, RangoReferencia, Turno
from tests.test_limites import turnos

LC, SIGMA = 85.0, 2.0
NUEVOS = datetime(2026, 7, 1, 6)


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="Norte", pais="Argentina", cliente=Cliente(nombre="X", pais="Argentina"),
                        hora_inicio_dia=time(5, 0))
        planta.turnos = [Turno(nombre="M", hora_inicio=time(5), hora_fin=time(12, 59)),
                         Turno(nombre="T", hora_inicio=time(13), hora_fin=time(20, 59)),
                         Turno(nombre="N", hora_inicio=time(21), hora_fin=time(4, 59))]
        linea = Linea(nombre="Línea 1", planta=planta)
        for numero in (1, 2, 3):
            Equipo(codigo=f"N-BOE-0{numero}", nombre=f"BOE {numero}", tipo="BOE", numero=numero, linea=linea)
        Equipo(codigo="N-CEN-01", nombre="ESE 230 (1)", tipo="Centrífuga primaria", numero=1, linea=linea)
        sesion.add_all([planta,
                        RangoReferencia(planta=planta, variable="Eficiencia eBOE", punto_o_equipo="BOE (todos)",
                                        fruta="Naranja", minimo=80, maximo=90, lado_capacidad="inferior"),
                        RangoReferencia(planta=planta, variable="pH emulsión", punto_o_equipo="Emulsión (licor)",
                                        fruta="Naranja", minimo=3, maximo=7, lado_capacidad="ambos"),
                        RangoReferencia(planta=planta, variable="CP fase pesada", punto_o_equipo="Centrífuga primaria",
                                        minimo=2, maximo=3, genera_alerta=False)])
        sesion.commit()
        yield sesion


def _planta(sesion):
    return sesion.scalars(select(Planta)).one()


def _eboe(sesion, numero):
    return sesion.scalars(select(Equipo).where(Equipo.numero == numero, Equipo.tipo == "BOE")).one()


def _limites(sesion, equipo_id=None, planta_id=None, reglas="1,2,3,5,S", variable="eficiencia", lc=LC, sigma=SIGMA):
    sesion.add(LimitesControl(variable=variable, equipo_id=equipo_id, planta_id=planta_id, fruta="naranja",
                              tipo_carta="Xbar-S", n_subgrupo=4, base_desde=date(2025, 6, 1),
                              base_hasta=date(2025, 10, 31), n=500, lc=lc, lcs=lc + 3 * sigma / 2,
                              lci=lc - 3 * sigma / 2, sigma=sigma, mr_lc=0.92 * sigma, mr_lcs=2.09 * sigma,
                              reglas=reglas, congelado_por="prueba"))
    sesion.commit()


def _reglas(valores_por_turno, reglas="1,2,3,5,S", previos=None):
    sub = subgrupos_por_turno(turnos(valores_por_turno, inicio=NUEVOS))
    return [(v["regla"], round(v["valor"], 2)) for v in violaciones_xbar_s(sub, previos, LC, SIGMA, reglas)]


# ---------------------------------------------------------------- cada regla (funciones puras)

def test_regla_1_depende_del_n_del_turno():
    # promedio 88,5: con 4 muestras el límite es 85 + 3·2/√4 = 88 (sale); con 1 muestra es 91 (no sale)
    assert _reglas([[88, 89, 88, 89]], "1") == [("1", 88.5)]
    assert _reglas([[88.5]], "1") == []


def test_regla_2_nueve_turnos_del_mismo_lado():
    assert _reglas([[86, 86]] * 9, "2") == [("2", 86.0)]
    assert _reglas([[86, 86]] * 8, "2") == []


def test_regla_3_seis_turnos_en_aumento():
    assert _reglas([[84.0], [84.3], [84.6], [84.9], [85.2], [85.5]], "3") == [("3", 85.5)]


def test_regla_5_dos_de_tres_mas_alla_de_2_sigma():
    assert _reglas([[87.5, 87.5], [85], [87.5, 87.5]], "5") == []                  # con n = 2: 85 + 2σ/√2 = 87,83
    assert _reglas([[88, 88, 88, 88], [85], [88, 88, 88, 88]], "5") == [("5", 88.0)]   # 2σ/√4 = 2 → 87


def test_regla_s_mucha_dispersion_dentro_del_turno():
    # n = 4: LCS de S = (0,9213 + 1,1666)·2 = 4,18; desvío de [80, 90, 80, 90] = 5,77
    assert _reglas([[80, 90, 80, 90]], "S") == [("S", 5.77)]
    assert _reglas([[84, 86, 84, 86]], "S") == []
    assert _reglas([[95]], "S") == []                                            # 1 muestra: no hay S


def test_mira_los_turnos_anteriores():
    previos = subgrupos_por_turno(turnos([[86, 86]] * 6, inicio=datetime(2026, 6, 29, 6), primer_id=100))
    assert _reglas([[86, 86]] * 3, "2", previos) == [("2", 86.0)]               # 6 anteriores + 3 nuevos = 9
    assert _reglas([[86, 86]] * 3, "2") == []


def test_leer_reglas():
    assert leer_reglas("1, 2,3,5,S") == ([1, 2, 3, 5], {"S"})
    assert leer_reglas("mr") == ([], {"MR"})
    with pytest.raises(ValueError, match="no válida"):
        leer_reglas("1,9")


def test_rango_con_el_promedio_del_turno_y_su_lado():
    sub = subgrupos_por_turno(turnos([[78, 83], [78, 79], [92, 93]], inicio=NUEVOS))
    assert [v["valor"] for v in violaciones_rango(sub, 80, 90, "inferior")] == [78.5]   # 80,5 no; 92,5 no (lado)
    assert [v["valor"] for v in violaciones_rango(sub, 80, 90, "ambos")] == [78.5, 92.5]
    assert [v["valor"] for v in violaciones_rango(sub, 80, 90, "superior")] == [92.5]


# ---------------------------------------------------------------- con la base

def test_crea_una_alerta_por_turno_con_sus_datos(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    datos = turnos([[85, 86], [77, 78, 79, 78]], equipo_id=equipo.id, inicio=NUEVOS)
    resumen = evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    alertas = {a.regla: a for a in sesion.scalars(select(Alerta))}
    assert resumen["creadas"] == len(alertas) and set(alertas) == {"1", "rango"}
    regla_1 = alertas["1"]
    assert (regla_1.estado, regla_1.origen, regla_1.turno.nombre, regla_1.ambito) == ("abierta", "nelson", "T",
                                                                                      f"equipo:{equipo.id}")
    assert regla_1.valor == 78 and regla_1.limite == pytest.approx(LC - 3 * SIGMA / 2)
    assert sorted(d.dato_id for d in regla_1.datos) == [3, 4, 5, 6]           # las 4 muestras del turno
    assert alertas["rango"].limite == 80
    assert {a.id for a in alertas_de(sesion, "analisis_scott", 4)} == {regla_1.id, alertas["rango"].id}
    assert alertas_de(sesion, "analisis_scott", 1) == []


def test_sin_duplicados_al_evaluar_dos_veces(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    datos = turnos([[85], [77, 78, 79, 78]], equipo_id=equipo.id, inicio=NUEVOS)
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    segundo = evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    assert segundo["creadas"] == 0 and segundo["actualizadas"] == 0
    assert len(sesion.scalars(select(Alerta)).all()) == 2 and len(sesion.scalars(select(AlertaDato)).all()) == 8


def test_sin_limites_solo_evalua_el_rango(sesion):
    equipo = _eboe(sesion, 2)
    datos = turnos([[95, 96], [78, 79], [85]], equipo_id=equipo.id, inicio=NUEVOS)
    resumen = evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    assert resumen["sin_limites"] == [f"equipo:{equipo.id}"]
    assert [(a.origen, a.valor) for a in sesion.scalars(select(Alerta))] == [("rango", 78.5)]   # 95,5 no: lado inferior


def test_historico_no_queda_abierto(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", turnos([[77, 78]], equipo_id=equipo.id, inicio=NUEVOS),
                   historico=True)
    assert {a.estado for a in sesion.scalars(select(Alerta))} == {"histórico"}


def test_reglas_configurables(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id, reglas="1")
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", turnos([[86, 86]] * 9 + [[80, 90, 80, 90]],
                                                                  equipo_id=equipo.id, inicio=NUEVOS))
    assert {a.regla for a in sesion.scalars(select(Alerta))} == set()          # sin regla 2 ni S configuradas


def test_los_turnos_de_la_fase_1_no_cuentan(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id, reglas="2")
    base = turnos([[86, 86]] * 6, equipo_id=equipo.id, inicio=datetime(2025, 10, 29, 6), primer_id=100)
    fase2 = turnos([[86, 86]] * 6, equipo_id=equipo.id, inicio=datetime(2026, 6, 29, 6), primer_id=200)
    nuevos = turnos([[86, 86]] * 3, equipo_id=equipo.id, inicio=NUEVOS)
    planta = _planta(sesion)
    assert evaluar_nuevos(sesion, planta, "eficiencia", nuevos, previos=base)["creadas"] == 0
    assert evaluar_nuevos(sesion, planta, "eficiencia", nuevos, previos=pd.concat([base, fase2]))["creadas"] == 1


def test_datos_invalidos_no_se_evaluan(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    datos = turnos([[85, 99]], equipo_id=equipo.id, inicio=NUEVOS, invalidos={2: PREFIJO_INVALIDO + "valor pegado"})
    assert evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)["creadas"] == 0


def test_comunes_a_los_tres_eboe(sesion):
    planta = _planta(sesion)
    for numero in (1, 2, 3):
        evaluar_nuevos(sesion, planta, "eficiencia", turnos([[78, 77]], equipo_id=_eboe(sesion, numero).id,
                                                             inicio=NUEVOS, primer_id=10 * numero))
    for numero in (1, 2):                                                       # otro turno: solo en 2 eBOE
        evaluar_nuevos(sesion, planta, "eficiencia", turnos([[77]], equipo_id=_eboe(sesion, numero).id,
                                                             inicio=datetime(2026, 7, 1, 15), primer_id=50 + numero))
    alertas = sesion.scalars(select(Alerta)).all()
    comunes = [a for a in alertas if a.comun_eboe]
    assert len(comunes) == 3 and {a.turno.nombre for a in comunes} == {"M"}
    assert all(a.comentario == COMENTARIO_COMUN for a in comunes)
    assert not any(a.comun_eboe for a in alertas if a.turno.nombre == "T")


def test_ph_de_planta(sesion):
    planta = _planta(sesion)
    _limites(sesion, planta_id=planta.id, variable="ph", lc=5.6, sigma=0.3, reglas="1")
    datos = turnos([[5.5, 5.7, 5.6], [7.2, 7.4, 7.3]], equipo_id=None, planta_id=planta.id, inicio=NUEVOS)
    resumen = evaluar_nuevos(sesion, planta, "ph", datos)
    alertas = {a.regla: a for a in sesion.scalars(select(Alerta))}
    assert set(alertas) == {"1", "rango"} and resumen["sin_limites"] == []
    assert (alertas["1"].planta_id, alertas["1"].equipo_id, alertas["1"].ambito) == (planta.id, None,
                                                                                     f"planta:{planta.id}")
    assert not alertas["1"].comun_eboe


def test_rango_sin_alerta_no_se_usa(sesion):
    centrifuga = sesion.scalars(select(Equipo).where(Equipo.tipo == "Centrífuga primaria")).one()
    assert rango_de_alerta(sesion, _planta(sesion).id, "cp_pesada", "Centrífuga primaria", "naranja") is None
    datos = turnos([[6.0, 6.5]], equipo_id=centrifuga.id, inicio=NUEVOS, tipo="Centrífuga primaria",
                   tabla="muestra_centrifuga")
    assert evaluar_nuevos(sesion, _planta(sesion), "cp_pesada", datos)["creadas"] == 0     # GEA 2–3 bar: referencia


def test_estado_y_origen_validos():
    with pytest.raises(ValueError, match="no válido"):
        Alerta(estado="resuelta")
    with pytest.raises(ValueError, match="no válido"):
        Alerta(origen="manual")
