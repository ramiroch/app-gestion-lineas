"""Pruebas de core/carga_scott.py: hora propuesta, cálculo en vivo, semáforo, validación (normalidad, g < 1, valor
poco habitual, entrada copiada), guardado, chequeo inmediato y turnos cerrados."""

from datetime import date, datetime, time

import pytest
from sqlalchemy import select

from core.carga_scott import (calcular_ronda, contexto_validacion, evaluar_turnos_cerrados, guardar_ronda,
                              hora_siguiente, percentiles_historicos, rangos_resultados, semaforo, validar_ronda,
                              valores_por_defecto)
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import (Alerta, AnalisisScott, Cliente, Equipo, LecturaOperacion, LimitesControl, Linea, Planta,
                         Proceso, PuntoMuestreo, RangoReferencia, Turno, TurnoEvaluado)
from core.repetidas import REVISAR_ENTRADA_REPETIDA

DIA = date(2026, 7, 1)


@pytest.fixture
def sesion():
    """Planta con 3 eBOE: blanco 0, factor 70 (fruta) y 3,5 (emulsión). Con 10 g y N 0,1, la entrada da mL × 0,7
    kg/MT; la salida (N 0,01), mL × 0,07 kg/MT. Límites de eficiencia: LC 85, σ entre 2 (4σ → 77)."""
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="Norte", pais="Argentina", cliente=Cliente(nombre="X", pais="Argentina"), blanco_ml=0.0,
                        factor_aceite_fruta=70.0, factor_aceite_emulsion=3.5, hora_inicio_dia=time(5),
                        turnos_fin_episodio=3, turnos_para_alertar=2, sigma_alerta_inmediata=4)
        planta.turnos = [Turno(nombre="M", hora_inicio=time(5), hora_fin=time(12, 59)),
                         Turno(nombre="T", hora_inicio=time(13), hora_fin=time(20, 59)),
                         Turno(nombre="N", hora_inicio=time(21), hora_fin=time(4, 59))]
        linea = Linea(nombre="Línea 1", planta=planta)
        for numero in (1, 2, 3):
            equipo = Equipo(codigo=f"N-BOE-0{numero}", nombre=f"eBOE {numero}", tipo="BOE", numero=numero, linea=linea)
            for tipo in ("fruta entrada", "fruta salida", "emulsión"):
                PuntoMuestreo(tipo=tipo, nombre=tipo, equipo=equipo)
        sesion.add_all([planta, Proceso(numero=12, temporada=2026, planta=planta),
                        RangoReferencia(planta=planta, variable="Eficiencia eBOE", punto_o_equipo="BOE (todos)",
                                        fruta="Naranja", minimo=80, maximo=90, lado_capacidad="inferior"),
                        RangoReferencia(planta=planta, variable="°Brix emulsión", punto_o_equipo="Emulsión (licor)",
                                        fruta="Naranja", maximo=5, lado_capacidad="superior")])
        sesion.flush()
        for equipo in linea.equipos:
            sesion.add(LimitesControl(variable="eficiencia", equipo_id=equipo.id, fruta="naranja", tipo_carta="I-MR-S",
                                      n_subgrupo=4, base_desde=date(2025, 6, 1), base_hasta=date(2025, 10, 31), n=500,
                                      lc=85, lcs=91, lci=79, sigma=2, sigma_dentro=2, reglas_alerta="1,2,3,rango",
                                      reglas_marca="5,S", sigma_alerta_inmediata=4, congelado_por="prueba"))
        sesion.commit()
        yield sesion


def _planta(sesion):
    return sesion.scalars(select(Planta)).one()


def ronda(sesion, fecha_hora, salida_ml=None, entrada_ml=None, normalidad_salida=0.01, **por_eboe):
    """Ronda de los 3 eBOE con entradas distintas (10,0 / 10,2 / 10,4 mL → 7,0 / 7,14 / 7,28 kg/MT) y salida 10 mL
    (0,7 kg/MT). salida_ml / entrada_ml: {n.º: mL} para cambiar algunos."""
    eboes = {}
    for numero in (1, 2, 3):
        eboes[numero] = {
            "entrada": {"g": 10.0, "ml": (entrada_ml or {}).get(numero, 10.0 + 0.2 * (numero - 1)),
                        "peso_fruta_kg": 8.0, "n_frutos": 40, "calidad": "Firme"},
            "salida": {"g": 10.0, "ml": (salida_ml or {}).get(numero, 10.0)},
            "emulsion": {"g": 10.0, "ml": 25.0, "brix": 4.0, "ph": 5.5},
            "operacion": {"programa": "N3", "tph": 11.0, "caudal_fase_pesada": 9.5}} | por_eboe.get(f"e{numero}", {})
    return {"fecha_hora": fecha_hora, "proceso_id": sesion.scalars(select(Proceso)).one().id, "analista": "Ana",
            "fruta": "N Val S/R", "normalidad": {"entrada": 0.1, "salida": normalidad_salida, "emulsion": 0.1},
            "eboes": eboes}


def _validar(sesion, datos, **contexto):
    return validar_ronda(_planta(sesion), datos, {"normalidades_previas": {}, "percentiles": {}, "cargados": set(),
                                                  "operacion_cargada": set(), "ahora": datetime(2026, 7, 1, 23)}
                         | contexto)


# ---------------------------------------------------------------- hora, cálculo y semáforo

def test_hora_siguiente(sesion):
    planta = _planta(sesion)
    rondas = [datetime(2026, 7, 1, 5, 10), datetime(2026, 7, 1, 7, 10)]
    assert hora_siguiente(planta, rondas, datetime(2026, 7, 1, 8, 3)) == datetime(2026, 7, 1, 9, 10)
    assert hora_siguiente(planta, rondas, datetime(2026, 7, 1, 14, 2, 30)) == datetime(2026, 7, 1, 14, 2)  # otro turno
    assert hora_siguiente(planta, [], datetime(2026, 7, 1, 8, 3)) == datetime(2026, 7, 1, 8, 3)


def test_calcular_ronda(sesion):
    r = calcular_ronda(_planta(sesion), ronda(sesion, datetime(2026, 7, 1, 6, 10)))[1]
    assert r["aceite_entrada"] == pytest.approx(7.0) and r["aceite_salida"] == pytest.approx(0.7)
    assert r["eficiencia"] == pytest.approx(90.0) and r["rendimiento"] == pytest.approx(6.3)
    assert r["aceite_emulsion"] == pytest.approx(0.875)                  # 25 × 0,1 × 3,5 / 10
    assert (r["frutos_10kg"], r["brix"], r["ph"]) == (50, 4.0, 5.5)
    incompleta = ronda(sesion, datetime(2026, 7, 1, 6, 10), e1={"salida": {"g": 10.0, "ml": None}})
    assert calcular_ronda(_planta(sesion), incompleta)[1]["eficiencia"] is None


def test_semaforo_respeta_el_lado(sesion):
    rangos = rangos_resultados(sesion, _planta(sesion).id, "N Val S/R")
    assert semaforo(75, rangos["eficiencia"]) == "rojo"
    assert semaforo(95, rangos["eficiencia"]) == "verde"                 # lado inferior: arriba de 90 no es malo
    assert (semaforo(5.5, rangos["brix"]), semaforo(1.0, rangos["brix"])) == ("rojo", "verde")
    assert semaforo(None, rangos["brix"]) is None and "aceite_salida" not in rangos


# ---------------------------------------------------------------- validación

def test_normalidad_con_factor_10_avisa(sesion):
    datos = ronda(sesion, datetime(2026, 7, 1, 6, 10), normalidad_salida=0.1)
    errores, avisos = _validar(sesion, datos, normalidades_previas={"entrada": 0.1, "salida": 0.01, "emulsion": 0.1})
    assert errores == [] and any("salida 0.1 contra 0.01" in a and "¿normalidad equivocada?" in a for a in avisos)
    _, sin_cambio = _validar(sesion, ronda(sesion, datetime(2026, 7, 1, 6, 10)),
                             normalidades_previas={"salida": 0.0098})
    assert not any("normalidad" in a for a in sin_cambio)


def test_muestra_de_menos_de_1_g_avisa(sesion):
    datos = ronda(sesion, datetime(2026, 7, 1, 6, 10), e2={"salida": {"g": 0.8, "ml": 1.0}})
    errores, avisos = _validar(sesion, datos)
    assert errores == [] and "eBOE 2, salida: muestra muy chica (0.8 g)." in avisos


def test_valor_poco_habitual(sesion):
    datos = ronda(sesion, datetime(2026, 7, 1, 6, 10), entrada_ml={1: 50.0})        # 50 mL: tipeo de 5,0
    _, avisos = _validar(sesion, datos, percentiles={(1, "entrada", "ml"): (5.0, 15.0),
                                                     (2, "entrada", "ml"): (5.0, 15.0)})
    habituales = [a for a in avisos if "poco habitual" in a]
    assert len(habituales) == 1 and habituales[0].startswith("eBOE 1, entrada: mL 50 fuera de lo habitual (5–15)")


def test_errores_y_entrada_copiada(sesion):
    datos = ronda(sesion, datetime(2026, 7, 1, 6, 10), entrada_ml={1: 10.0, 2: 10.0},
                  e3={"salida": {"g": 0.0, "ml": 5.0}, "emulsion": {"g": None, "ml": 25.0}})
    errores, avisos = _validar(sesion, datos | {"analista": " "})
    assert "Falta el analista." in errores
    assert "eBOE 3, salida: los g tienen que ser mayores que 0 (dice 0.0)." in errores
    assert "eBOE 3, emulsión: titulación incompleta (hacen falta los g y los mL)." in errores
    assert "La entrada de los eBOE 1 y 2 tiene los mismos g y mL: ¿copiaste la entrada?" in avisos


def test_percentiles_del_historico(sesion):
    planta = _planta(sesion)
    for hora in range(30):
        guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 6, 1, 5, 10) + (datetime(2026, 6, 1, 7, 10)
                                                                               - datetime(2026, 6, 1, 5, 10)) * hora),
                      "prueba", ahora=datetime(2026, 6, 1, 5, 15))
    percentiles = percentiles_historicos(sesion, planta.id, minimo=20)
    assert percentiles[(1, "entrada", "ml")] == pytest.approx((10.0, 10.0))
    assert (1, "entrada", "ml") not in percentiles_historicos(sesion, planta.id, minimo=50)


# ---------------------------------------------------------------- guardar

def test_guardar_crea_los_datos_crudos_y_la_operacion(sesion):
    planta = _planta(sesion)
    resultado = guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, 6, 10)), "Ramiro",
                              ahora=datetime(2026, 7, 1, 6, 20))
    assert len(resultado["analisis"]) == 9 and len(resultado["lecturas"]) == 3
    entrada = sesion.scalars(select(AnalisisScott).join(AnalisisScott.punto_muestreo).where(
        PuntoMuestreo.tipo == "fruta entrada")).first()
    assert (entrada.peso_muestra_g, entrada.normalidad, entrada.blanco_ml, entrada.n_frutos, entrada.cargado_por,
            entrada.cargado_en, entrada.origen) == (10.0, 0.1, 0.0, 40, "Ramiro", datetime(2026, 7, 1, 6, 20), None)
    lectura = sesion.scalars(select(LecturaOperacion)).first()
    assert (lectura.programa, lectura.tph, lectura.caudal_fase_pesada) == ("N3", 11.0, 9.5)
    with pytest.raises(ValueError, match="ya hay un análisis cargado"):
        guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, 6, 10)), "Ramiro", datetime(2026, 7, 1, 6, 30))
    with pytest.raises(ValueError, match="cargado por"):
        guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, 8, 10)), " ", datetime(2026, 7, 1, 8, 20))
    defecto = valores_por_defecto(sesion, planta, datetime(2026, 7, 1, 7, 0))
    assert defecto["fecha_hora"] == datetime(2026, 7, 1, 8, 10)                # 1 sola ronda: + 2 h por defecto
    assert defecto["normalidad"] == {"entrada": 0.1, "salida": 0.01, "emulsion": 0.1}
    assert (defecto["analista"], defecto["en_marcha"]) == ("Ana", [1, 2, 3])


def test_guardar_marca_la_entrada_repetida(sesion):
    guardar_ronda(sesion, _planta(sesion), ronda(sesion, datetime(2026, 7, 1, 6, 10), entrada_ml={1: 10.0, 2: 10.0}),
                  "Ramiro", ahora=datetime(2026, 7, 1, 6, 20))
    marcadas = sesion.scalars(select(AnalisisScott).where(AnalisisScott.revisar == REVISAR_ENTRADA_REPETIDA)).all()
    assert len(marcadas) == 2


# ---------------------------------------------------------------- alertas

def test_chequeo_inmediato_con_1_y_con_2_rondas(sesion):
    planta = _planta(sesion)
    malo = {1: 30.0}                                                  # salida 2,1 kg/MT → eficiencia 70 % (< 77)
    primera = guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, 6, 10), salida_ml=malo), "Ramiro",
                            ahora=datetime(2026, 7, 1, 6, 20))
    assert primera["alertas"] == []                                    # 1 ronda: todavía no
    segunda = guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, 8, 10), salida_ml=malo), "Ramiro",
                            ahora=datetime(2026, 7, 1, 8, 20))
    (cambio,) = segunda["alertas"]
    alerta = cambio["alerta"]
    assert cambio["cambio"] == "nueva" and (alerta.regla, alerta.inmediata, alerta.estado) == ("1", True, "abierta")
    assert alerta.equipo.numero == 1 and alerta.valor == pytest.approx(70.0)
    assert sorted(d.dato_id for d in alerta.datos) == sorted(
        a.id for a in sesion.scalars(select(AnalisisScott).join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo)
                                     .where(Equipo.numero == 1, PuntoMuestreo.tipo == "fruta entrada")))
    assert segunda["turnos_evaluados"] == []                           # el turno M sigue en curso


def test_turno_cerrado_se_evalua_una_sola_vez(sesion):
    planta = _planta(sesion)
    planta.turnos_para_alertar = 1
    sesion.commit()
    guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, 6, 10), salida_ml={2: 30.0}), "Ramiro",
                  ahora=datetime(2026, 7, 1, 6, 20))
    assert sesion.scalars(select(Alerta)).all() == []                  # en curso: solo el chequeo inmediato
    evaluados = evaluar_turnos_cerrados(sesion, planta, datetime(2026, 7, 1, 13, 5), "Ramiro")
    assert evaluados == [(DIA, "M")]
    alertas = sesion.scalars(select(Alerta)).all()
    assert {(a.regla, a.equipo.numero, a.estado) for a in alertas} == {("1", 2, "abierta"), ("rango", 2, "abierta")}
    assert evaluar_turnos_cerrados(sesion, planta, datetime(2026, 7, 1, 14, 0), "Ramiro") == []
    assert len(sesion.scalars(select(TurnoEvaluado)).all()) == 1 and len(sesion.scalars(select(Alerta)).all()) == 2


def test_inmediata_no_confirmada_al_cierre_del_turno(sesion):
    planta = _planta(sesion)
    malo, bueno = {1: 30.0}, {1: 10.0}                                 # eficiencia 70 % y 90 %
    for hora, salida in ((6, malo), (8, malo), (10, bueno), (12, bueno)):
        guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, hora, 10), salida_ml=salida), "Ramiro",
                      ahora=datetime(2026, 7, 1, hora, 20))
    (alerta,) = sesion.scalars(select(Alerta)).all()                   # inmediata a las 08:10 (promedio 70)
    assert alerta.inmediata and alerta.confirmada is None
    evaluar_turnos_cerrados(sesion, planta, datetime(2026, 7, 1, 13, 5), "Ramiro")
    sesion.refresh(alerta)                                             # turno completo: promedio 80 (no pasa 77)
    assert (alerta.confirmada, alerta.comentario, alerta.estado) == (False, "no confirmada al cierre del turno",
                                                                     "abierta")
    assert len(sesion.scalars(select(Alerta)).all()) == 1              # no se borra ni se crea otra


def test_inmediata_confirmada_al_cierre_del_turno(sesion):
    planta = _planta(sesion)
    for hora in (6, 8):
        guardar_ronda(sesion, planta, ronda(sesion, datetime(2026, 7, 1, hora, 10), salida_ml={1: 30.0}), "Ramiro",
                      ahora=datetime(2026, 7, 1, hora, 20))
    evaluar_turnos_cerrados(sesion, planta, datetime(2026, 7, 1, 13, 5), "Ramiro")
    (alerta,) = [a for a in sesion.scalars(select(Alerta)) if a.regla == "1"]
    assert (alerta.confirmada, alerta.comentario) == (True, None)


def test_contexto_ve_lo_ya_cargado(sesion):
    planta = _planta(sesion)
    datos = ronda(sesion, datetime(2026, 7, 1, 6, 10))
    guardar_ronda(sesion, planta, datos, "Ramiro", ahora=datetime(2026, 7, 1, 6, 20))
    contexto = contexto_validacion(sesion, planta, datos, datetime(2026, 7, 1, 6, 30))
    assert (1, "entrada") in contexto["cargados"] and contexto["operacion_cargada"] == {1, 2, 3}
