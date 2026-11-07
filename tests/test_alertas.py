"""Pruebas de core/alertas.py: cada regla, el rango, sin límites, sin duplicados, histórico y comunes."""

from datetime import date, datetime, time

import pandas as pd
import pytest
from sqlalchemy import select

from core.alertas import (COMENTARIO_COMUN, alertas_de, evaluar_nuevos, leer_reglas, rango_de_alerta,
                          violaciones_nelson, violaciones_rango)
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.limites import PREFIJO_INVALIDO
from core.models import Alerta, AlertaDato, Cliente, Equipo, Linea, LimitesControl, Planta, RangoReferencia, Turno
from tests.test_limites import serie

LC, SIGMA = 85.0, 2.0
MR_LCS = 3.267 * SIGMA * 1.128                 # D4(2) · MR̄, con MR̄ = σ · d2(2)
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
                        RangoReferencia(planta=planta, variable="CP fase pesada", punto_o_equipo="Centrífuga primaria",
                                        minimo=2, maximo=3, genera_alerta=False)])
        sesion.commit()
        yield sesion


def _planta(sesion):
    return sesion.scalars(select(Planta)).one()


def _eboe(sesion, numero):
    return sesion.scalars(select(Equipo).where(Equipo.numero == numero, Equipo.tipo == "BOE")).one()


def _limites(sesion, equipo_id, reglas="1,2,3,5,MR", variable="eficiencia"):
    sesion.add(LimitesControl(variable=variable, equipo_id=equipo_id, fruta="naranja", base_desde=date(2025, 6, 1),
                              base_hasta=date(2025, 10, 31), n=500, lc=LC, lcs=LC + 3 * SIGMA, lci=LC - 3 * SIGMA,
                              sigma=SIGMA, mr_lc=SIGMA * 1.128, mr_lcs=MR_LCS, reglas=reglas, congelado_por="prueba"))
    sesion.commit()


def _reglas(valores, reglas="1,2,3,5,MR", previos=None):
    datos = serie(valores, inicio=NUEVOS)
    return [(v["regla"], datos.loc[v["indice"], "valor"]) for v in
            violaciones_nelson(datos, previos, LC, SIGMA, MR_LCS, reglas)]


# ---------------------------------------------------------------- cada regla (funciones puras)

def test_regla_1_fuera_de_3_sigma():
    assert _reglas([85, 92, 85]) == [("1", 92.0)]
    datos = serie([85, 78.5], inicio=NUEVOS)
    violacion = [v for v in violaciones_nelson(datos, None, LC, SIGMA, MR_LCS, "1") if v["regla"] == "1"][0]
    assert violacion["limite"] == pytest.approx(LC - 3 * SIGMA)


def test_regla_2_nueve_del_mismo_lado():
    assert _reglas([86] * 9, "2") == [("2", 86.0)]
    assert _reglas([86] * 8, "2") == []


def test_regla_3_seis_en_aumento():
    assert _reglas([84.0, 84.3, 84.6, 84.9, 85.2, 85.5], "3") == [("3", 85.5)]        # 6 puntos subiendo
    assert _reglas([84.0, 84.3, 84.6, 84.9, 85.2], "3") == []


def test_regla_5_dos_de_tres_mas_alla_de_2_sigma():
    assert _reglas([89.5, 85, 89.5], "5") == [("5", 89.5)]


def test_regla_mr_salto_entre_dos_datos():
    assert _reglas([82, 90], "MR") == [("MR", 90.0)]                    # |90 − 82| = 8 > LCS del MR (7,4)
    assert _reglas([82, 88], "MR") == []


def test_mira_los_puntos_anteriores():
    previos = serie([86] * 6, inicio=datetime(2026, 6, 30, 6), primer_id=100)
    assert _reglas([86, 86, 86], "2", previos) == [("2", 86.0)]        # 6 anteriores + 3 nuevos = 9
    assert _reglas([86, 86, 86], "2") == []


def test_leer_reglas():
    assert leer_reglas("1, 2,3,5,MR") == ([1, 2, 3, 5], True)
    assert leer_reglas("mr") == ([], True)
    with pytest.raises(ValueError, match="no válida"):
        leer_reglas("1,9")


def test_rango_respeta_el_lado():
    datos = serie([78, 85, 92], inicio=NUEVOS)
    assert [datos.loc[v["indice"], "valor"] for v in violaciones_rango(datos, 80, 90, "inferior")] == [78.0]
    assert [datos.loc[v["indice"], "valor"] for v in violaciones_rango(datos, 80, 90, "ambos")] == [78.0, 92.0]
    assert [datos.loc[v["indice"], "valor"] for v in violaciones_rango(datos, 80, 90, "superior")] == [92.0]


# ---------------------------------------------------------------- con la base

def test_crea_alertas_abiertas_con_sus_datos(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    datos = serie([85, 92, 85, 78.5], equipo_id=equipo.id, inicio=NUEVOS)
    resumen = evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    alertas = sesion.scalars(select(Alerta)).all()
    assert resumen["creadas"] == len(alertas) and resumen["por_regla"]["1"] == 1     # 92 y 78,5: mismo turno M
    regla_1 = next(a for a in alertas if a.regla == "1")
    assert regla_1.estado == "abierta" and regla_1.origen == "nelson" and regla_1.turno.nombre == "M"
    assert regla_1.fecha_produccion == date(2026, 7, 1) and regla_1.valor == 92
    assert sorted(d.dato_id for d in regla_1.datos) == [2, 4]                         # los dos datos del turno
    rango = next(a for a in alertas if a.origen == "rango")
    assert (rango.valor, rango.limite, [d.dato_id for d in rango.datos]) == (78.5, 80, [4])
    assert {a.id for a in alertas_de(sesion, "analisis_scott", 4)} == {regla_1.id, rango.id}
    assert alertas_de(sesion, "analisis_scott", 1) == []


def test_sin_duplicados_al_evaluar_dos_veces(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    datos = serie([85, 92, 85], equipo_id=equipo.id, inicio=NUEVOS)
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    segundo = evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    assert segundo["creadas"] == 0 and segundo["actualizadas"] == 0
    assert len(sesion.scalars(select(Alerta)).all()) == 1 and len(sesion.scalars(select(AlertaDato)).all()) == 1


def test_sin_limites_solo_evalua_el_rango(sesion):
    equipo = _eboe(sesion, 2)
    datos = serie([95, 78, 85], equipo_id=equipo.id, inicio=NUEVOS)              # 95: rule 1 si hubiera límites
    resumen = evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)
    alertas = sesion.scalars(select(Alerta)).all()
    assert resumen["sin_limites"] == [equipo.id]
    assert [(a.origen, a.valor) for a in alertas] == [("rango", 78)]              # 95 no: el lado es inferior


def test_historico_no_queda_abierto(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", serie([92, 78], equipo_id=equipo.id, inicio=NUEVOS),
                   historico=True)
    assert {a.estado for a in sesion.scalars(select(Alerta))} == {"histórico"}


def test_reglas_configurables(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id, reglas="1")
    evaluar_nuevos(sesion, _planta(sesion), "eficiencia", serie([86] * 9 + [82, 90.5], equipo_id=equipo.id,
                                                                 inicio=NUEVOS))
    assert {a.regla for a in sesion.scalars(select(Alerta))} == set()         # sin regla 2 ni MR configuradas


def test_los_previos_de_la_fase_1_no_cuentan(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id, reglas="2")
    base = serie([86] * 6, equipo_id=equipo.id, inicio=datetime(2025, 10, 30, 6), primer_id=100)   # período base
    fase2 = serie([86] * 6, equipo_id=equipo.id, inicio=datetime(2026, 6, 30, 6), primer_id=200)
    nuevos = serie([86, 86, 86], equipo_id=equipo.id, inicio=NUEVOS)
    planta = _planta(sesion)
    assert evaluar_nuevos(sesion, planta, "eficiencia", nuevos, previos=base)["creadas"] == 0
    assert evaluar_nuevos(sesion, planta, "eficiencia", nuevos, previos=pd.concat([base, fase2]))["creadas"] == 1


def test_datos_invalidos_no_se_evaluan(sesion):
    equipo = _eboe(sesion, 1)
    _limites(sesion, equipo.id)
    datos = serie([85, 99, 85], equipo_id=equipo.id, inicio=NUEVOS, invalidos={2: PREFIJO_INVALIDO + "valor pegado"})
    assert evaluar_nuevos(sesion, _planta(sesion), "eficiencia", datos)["creadas"] == 0


def test_comunes_a_los_tres_eboe(sesion):
    planta = _planta(sesion)
    for numero in (1, 2, 3):
        equipo = _eboe(sesion, numero)
        evaluar_nuevos(sesion, planta, "eficiencia", serie([78], equipo_id=equipo.id, inicio=NUEVOS,
                                                           primer_id=10 * numero))
    # otro turno: solo en 2 eBOE
    for numero in (1, 2):
        equipo = _eboe(sesion, numero)
        evaluar_nuevos(sesion, planta, "eficiencia", serie([77], equipo_id=equipo.id,
                                                           inicio=datetime(2026, 7, 1, 15), primer_id=50 + numero))
    alertas = sesion.scalars(select(Alerta)).all()
    comunes = [a for a in alertas if a.comun_eboe]
    assert len(comunes) == 3 and {a.turno.nombre for a in comunes} == {"M"}
    assert all(a.comentario == COMENTARIO_COMUN for a in comunes)
    assert not any(a.comun_eboe for a in alertas if a.turno.nombre == "T")


def test_rango_sin_alerta_no_se_usa(sesion):
    centrifuga = sesion.scalars(select(Equipo).where(Equipo.tipo == "Centrífuga primaria")).one()
    assert rango_de_alerta(sesion, _planta(sesion).id, "cp_pesada", "Centrífuga primaria", "naranja") is None
    datos = serie([6.0, 6.5], equipo_id=centrifuga.id, inicio=NUEVOS, tipo="Centrífuga primaria",
                  tabla="muestra_centrifuga")
    assert evaluar_nuevos(sesion, _planta(sesion), "cp_pesada", datos)["creadas"] == 0     # GEA 2–3 bar: referencia


def test_estado_y_origen_validos():
    with pytest.raises(ValueError, match="no válido"):
        Alerta(estado="resuelta")
    with pytest.raises(ValueError, match="no válido"):
        Alerta(origen="manual")
