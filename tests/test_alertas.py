"""Pruebas de core/alertas.py (por turno): cada regla, S, entre/dentro, lado malo, reglas de marca, rango con el
promedio, episodios, sin límites, sin duplicados, histórico, comunes de planta y pH de planta."""

from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from core.alertas import (COMENTARIO_COMUN, alertas_de, evaluar_nuevos, leer_reglas, rango_de_alerta, turnos_entre,
                          violaciones_por_turno, violaciones_rango)
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
                        hora_inicio_dia=time(5, 0), turnos_fin_episodio=3)
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


def _limites(sesion, equipo_id=None, planta_id=None, reglas="1,2,3,5,S", variable="eficiencia", lc=LC, sigma=SIGMA,
             tipo="Xbar-S", sigma_dentro=None, marca=None):
    sesion.add(LimitesControl(variable=variable, equipo_id=equipo_id, planta_id=planta_id, fruta="naranja",
                              tipo_carta=tipo, n_subgrupo=4, base_desde=date(2025, 6, 1), base_hasta=date(2025, 10, 31),
                              n=500, lc=lc, lcs=lc + 3 * sigma, lci=lc - 3 * sigma, sigma=sigma,
                              sigma_dentro=sigma_dentro, mr_lc=1.128 * sigma, mr_lcs=3.267 * 1.128 * sigma,
                              reglas_alerta=reglas, reglas_marca=marca, congelado_por="prueba"))
    sesion.commit()


def _reglas(valores_por_turno, reglas="1,2,3,5,S", previos=None, tipo="Xbar-S", sigma_dentro=None, marca=None,
            lado="ambos", con_alerta=False):
    sub = subgrupos_por_turno(turnos(valores_por_turno, inicio=NUEVOS))
    violaciones = violaciones_por_turno(sub, previos, LC, SIGMA, reglas, tipo, sigma_dentro, marca, lado)
    if con_alerta:
        return [(v["regla"], round(v["valor"], 2), v["alerta"]) for v in violaciones]
    return [(v["regla"], round(v["valor"], 2)) for v in violaciones]


# ---------------------------------------------------------------- cada regla (funciones puras)

def test_xbar_s_regla_1_depende_del_n_del_turno():
    # promedio 88,5: con 4 muestras el límite es 85 + 3·2/√4 = 88 (sale); con 1 muestra es 91 (no sale)
    assert _reglas([[88, 89, 88, 89]], "1") == [("1", 88.5)]
    assert _reglas([[88.5]], "1") == []


def test_entre_dentro_el_limite_del_promedio_no_depende_del_n():
    # σ entre = 2: el límite es 85 + 3·2 = 91 para cualquier n
    assert _reglas([[88, 89, 88, 89]], "1", tipo="I-MR-S") == []
    assert _reglas([[91.5]], "1", tipo="I-MR-S") == [("1", 91.5)]
    assert _reglas([[91, 92, 92, 91]], "1", tipo="I-MR-S") == [("1", 91.5)]
    # la S usa σ dentro: con σ dentro = 1 y n = 4, LCS de S = (0,9213 + 1,1666)·1 = 2,09
    assert _reglas([[83, 87, 83, 87]], "S", tipo="I-MR-S", sigma_dentro=1.0) == [("S", 2.31)]
    assert _reglas([[83, 87, 83, 87]], "S", tipo="I-MR-S") == []          # sin σ dentro no hay carta S
    with pytest.raises(ValueError, match="por turno"):
        _reglas([[85]], "1", tipo="I-MR")


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


def test_solo_alerta_el_lado_malo():
    # regla 1 con σ entre = 2: 91,5 está arriba y 78,5 abajo
    assert _reglas([[91.5]], "1", tipo="I-MR-S", lado="abajo", con_alerta=True) == [("1", 91.5, False)]
    assert _reglas([[91.5]], "1", tipo="I-MR-S", lado="arriba", con_alerta=True) == [("1", 91.5, True)]
    assert _reglas([[78.5]], "1", tipo="I-MR-S", lado="abajo", con_alerta=True) == [("1", 78.5, True)]
    # regla 2: 9 turnos debajo de la LC alertan si el lado malo es abajo; arriba, no
    assert _reglas([[84, 84]] * 9, "2", lado="abajo", con_alerta=True) == [("2", 84.0, True)]
    assert _reglas([[86, 86]] * 9, "2", lado="abajo", con_alerta=True) == [("2", 86.0, False)]
    # regla 3: cuenta el sentido de la tendencia, no el lado de la LC (todos estos puntos están debajo de 85)
    subiendo = [[83.0], [83.3], [83.6], [83.9], [84.2], [84.5]]
    assert _reglas(subiendo, "3", lado="abajo", con_alerta=True) == [("3", 84.5, False)]
    assert _reglas(subiendo, "3", lado="arriba", con_alerta=True) == [("3", 84.5, True)]
    assert _reglas(subiendo[::-1], "3", lado="abajo", con_alerta=True) == [("3", 83.0, True)]
    with pytest.raises(ValueError, match="Lado de alerta"):
        _reglas([[85]], "1", lado="izquierda")


def test_reglas_de_marca_no_alertan():
    # la regla 5 y la S se calculan y se devuelven, pero con alerta = no
    turnos_5 = [[88, 88, 88, 88], [85], [88, 88, 88, 88]]
    assert _reglas(turnos_5, "1,2,3", marca="5,S", con_alerta=True) == [("5", 88.0, False)]   # 88 = LCS: no es 1
    assert _reglas([[80, 90, 80, 90]], "1,2,3", marca="5,S", con_alerta=True) == [("S", 5.77, False)]
    assert _reglas([[80, 90, 80, 90]], "1,2,3") == []                           # sin marcar: ni se calcula


def test_mira_los_turnos_anteriores():
    previos = subgrupos_por_turno(turnos([[86, 86]] * 6, inicio=datetime(2026, 6, 29, 6), primer_id=100))
    assert _reglas([[86, 86]] * 3, "2", previos) == [("2", 86.0)]               # 6 anteriores + 3 nuevos = 9
    assert _reglas([[86, 86]] * 3, "2") == []


def test_leer_reglas_y_turnos_entre():
    assert leer_reglas("1, 2,3,5,S") == ([1, 2, 3, 5], {"S"})
    assert leer_reglas("mr") == ([], {"MR"})
    with pytest.raises(ValueError, match="no válida"):
        leer_reglas("1,9")
    tiempos = [datetime(2026, 7, 1, h) for h in (6, 14, 22)]
    assert turnos_entre(tiempos, datetime(2026, 7, 1, 6), datetime(2026, 7, 1, 22)) == 1


def test_rango_con_el_promedio_del_turno_y_su_lado():
    sub = subgrupos_por_turno(turnos([[78, 83], [78, 79], [92, 93]], inicio=NUEVOS))
    assert [v["valor"] for v in violaciones_rango(sub, 80, 90, "inferior")] == [78.5]   # 80,5 no; 92,5 no (lado)
    assert [v["valor"] for v in violaciones_rango(sub, 80, 90, "ambos")] == [78.5, 92.5]
    assert [v["valor"] for v in violaciones_rango(sub, 80, 90, "superior")] == [92.5]


# ---------------------------------------------------------------- con la base

def _evaluar(sesion, valores_por_turno, numero=1, inicio=NUEVOS, primer_id=1, **opciones):
    datos = turnos(valores_por_turno, equipo_id=_eboe(sesion, numero).id, inicio=inicio, primer_id=primer_id)
    return evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos, **opciones)


def _rango(sesion):
    return sorted((a for a in sesion.scalars(select(Alerta)) if a.regla == "rango"), key=lambda a: a.fecha_hora)


def test_crea_una_alerta_por_turno_con_sus_datos(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    resumen = _evaluar(sesion, [[85, 86], [77, 78, 79, 78]])
    alertas = {a.regla: a for a in sesion.scalars(select(Alerta))}
    assert resumen["creadas"] == len(alertas) and set(alertas) == {"1", "rango"}
    regla_1 = alertas["1"]
    assert (regla_1.estado, regla_1.origen, regla_1.turno.nombre, regla_1.ambito) == ("abierta", "nelson", "T",
                                                                                      f"equipo:{equipo.id}")
    assert regla_1.valor == 78 and regla_1.limite == pytest.approx(LC - 3 * SIGMA / 2)
    assert (regla_1.turnos_afectados, regla_1.fin, regla_1.episodio_abierto) == (1, regla_1.fecha_hora, True)
    assert sorted(d.dato_id for d in regla_1.datos) == [3, 4, 5, 6]           # las 4 muestras del turno
    assert {a.id for a in alertas_de(sesion, "analisis_scott", 4)} == {regla_1.id, alertas["rango"].id}
    assert alertas_de(sesion, "analisis_scott", 1) == []


def test_episodio_junta_turnos_seguidos(sesion):
    resumen = _evaluar(sesion, [[78], [85], [77], [79], [86]])              # 2 turnos sin violar en el medio: sigue
    (episodio,) = _rango(sesion)
    assert resumen["creadas"] == 1 and resumen["turnos"] == 3
    assert episodio.turnos_afectados == 3 and episodio.fin == NUEVOS + timedelta(hours=24)
    assert sorted(d.dato_id for d in episodio.datos) == [1, 3, 4] and episodio.episodio_abierto


def test_tres_turnos_sin_violar_cierran_el_episodio(sesion):
    _evaluar(sesion, [[78], [85], [86], [84], [77]])                          # 3 turnos sin violar: otro episodio
    primero, segundo = _rango(sesion)
    assert (primero.turnos_afectados, primero.episodio_abierto) == (1, False)
    assert (segundo.turnos_afectados, segundo.episodio_abierto) == (1, True)


def test_el_episodio_se_cierra_solo_al_final(sesion):
    _evaluar(sesion, [[78], [85], [86], [84]])
    assert _rango(sesion)[0].episodio_abierto is False


def test_entre_evaluaciones_cuenta_los_turnos_previos(sesion):
    equipo = _eboe(sesion, 1)
    primeros = turnos([[78], [85]], equipo_id=equipo.id, inicio=NUEVOS)
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", primeros)
    siguientes = turnos([[77]], equipo_id=equipo.id, inicio=NUEVOS + timedelta(hours=16), primer_id=10)
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", siguientes, previos=primeros)
    (episodio,) = _rango(sesion)                                             # 1 turno sin violar: mismo episodio
    assert episodio.turnos_afectados == 2


def test_una_alerta_cerrada_a_mano_no_se_reabre(sesion):
    _evaluar(sesion, [[78]])
    (alerta,) = _rango(sesion)
    alerta.estado, alerta.cerrada_por = "cerrada", "Ramiro"
    sesion.commit()
    _evaluar(sesion, [[77]], inicio=NUEVOS + timedelta(hours=8), primer_id=10)
    cerrada, nueva = _rango(sesion)
    assert (cerrada.turnos_afectados, nueva.turnos_afectados, nueva.estado) == (1, 1, "abierta")


def test_sin_duplicados_al_evaluar_dos_veces(sesion):
    _limites(sesion, _eboe(sesion, 1).id)
    _evaluar(sesion, [[85], [77, 78, 79, 78], [78]])
    antes = (len(sesion.scalars(select(Alerta)).all()), len(sesion.scalars(select(AlertaDato)).all()),
             sorted(a.turnos_afectados for a in sesion.scalars(select(Alerta))))
    segundo = _evaluar(sesion, [[85], [77, 78, 79, 78], [78]])
    despues = (len(sesion.scalars(select(Alerta)).all()), len(sesion.scalars(select(AlertaDato)).all()),
               sorted(a.turnos_afectados for a in sesion.scalars(select(Alerta))))
    assert segundo["creadas"] == 0 and segundo["turnos"] == 0 and antes == despues
    assert antes[0] == 3                                                     # reglas 1 y 5, y rango


def test_sin_limites_solo_evalua_el_rango(sesion):
    equipo = _eboe(sesion, 2)
    resumen = _evaluar(sesion, [[95, 96], [78, 79], [85]], numero=2)
    assert resumen["sin_limites"] == [f"equipo:{equipo.id}"]
    assert [(a.origen, a.valor) for a in sesion.scalars(select(Alerta))] == [("rango", 78.5)]   # 95,5 no: lado inferior


def test_historico_no_queda_abierto(sesion):
    _limites(sesion, _eboe(sesion, 1).id)
    _evaluar(sesion, [[77, 78]], historico=True)
    assert {a.estado for a in sesion.scalars(select(Alerta))} == {"histórico"}


def test_reglas_configurables(sesion):
    _limites(sesion, _eboe(sesion, 1).id, reglas="1")
    _evaluar(sesion, [[86, 86]] * 9 + [[80, 90, 80, 90]])
    assert {a.regla for a in sesion.scalars(select(Alerta))} == set()          # sin regla 2 ni S configuradas


def test_los_turnos_de_la_fase_1_no_cuentan(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id, reglas="2")
    base = turnos([[84, 84]] * 6, equipo_id=equipo.id, inicio=datetime(2025, 10, 29, 6), primer_id=100)
    fase2 = turnos([[84, 84]] * 6, equipo_id=equipo.id, inicio=datetime(2026, 6, 29, 6), primer_id=200)
    nuevos = turnos([[84, 84]] * 3, equipo_id=equipo.id, inicio=NUEVOS)
    planta = _planta(sesion)
    assert evaluar_nuevos(sesion, planta, "eficiencia", nuevos, previos=base)["creadas"] == 0
    assert evaluar_nuevos(sesion, planta, "eficiencia", nuevos, previos=pd.concat([base, fase2]))["creadas"] == 1


def test_datos_invalidos_no_se_evaluan(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    datos = turnos([[85, 99]], equipo_id=equipo.id, inicio=NUEVOS, invalidos={2: PREFIJO_INVALIDO + "valor pegado"})
    assert evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)["creadas"] == 0


def test_falta_el_parametro_de_episodios(sesion):
    planta = _planta(sesion)
    planta.turnos_fin_episodio = None
    sesion.commit()
    with pytest.raises(ValueError, match="cierran un episodio"):
        _evaluar(sesion, [[78]])


def _varios_eboe(sesion, valores_por_eboe, inicio=NUEVOS):
    return pd.concat([turnos(valores, equipo_id=_eboe(sesion, numero).id, inicio=inicio, primer_id=100 * numero)
                      for numero, valores in valores_por_eboe.items()], ignore_index=True)


def test_comun_a_los_tres_eboe_es_una_alerta_de_planta(sesion):
    planta = _planta(sesion)
    # turno 1: los 3 eBOE debajo de 80 (común); turno 2: solo el BOE 1
    datos = _varios_eboe(sesion, {1: [[78, 77], [76]], 2: [[79], [85]], 3: [[77], [86]]})
    resumen = evaluar_nuevos(sesion, planta, "eficiencia", datos)
    alertas = sesion.scalars(select(Alerta)).all()
    (comun,) = [a for a in alertas if a.comun_eboe]
    assert (comun.ambito, comun.equipo_id, comun.planta_id) == (f"planta:{planta.id}", None, planta.id)
    assert comun.comentario == COMENTARIO_COMUN and comun.regla == "rango" and comun.turno.nombre == "M"
    assert comun.valor == pytest.approx((77.5 + 79 + 77) / 3) and comun.limite == 80
    assert sorted(d.dato_id for d in comun.datos) == [100, 101, 200, 300]     # las muestras de los 3 eBOE
    (propia,) = [a for a in alertas if not a.comun_eboe]                        # el turno T solo del BOE 1
    assert (propia.equipo_id, propia.turno.nombre, propia.valor) == (_eboe(sesion, 1).id, "T", 76)
    assert resumen["creadas"] == 2


def test_en_dos_eboe_no_es_comun(sesion):
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", _varios_eboe(sesion, {1: [[77]], 2: [[78]], 3: [[85]]}))
    alertas = sesion.scalars(select(Alerta)).all()
    assert len(alertas) == 2 and not any(a.comun_eboe for a in alertas)
    assert {a.ambito for a in alertas} == {f"equipo:{_eboe(sesion, n).id}" for n in (1, 2)}


def test_lado_bueno_no_crea_alerta(sesion):
    _limites(sesion, _eboe(sesion, 1).id, reglas="1,2,3", tipo="I-MR-S")
    assert _evaluar(sesion, [[95, 96]])["creadas"] == 0                         # eficiencia muy alta: lado bueno
    assert _evaluar(sesion, [[76]], inicio=NUEVOS + timedelta(hours=8), primer_id=10)["por_regla"]["1"] == 1


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
