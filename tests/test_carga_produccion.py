"""Pruebas de core/carga_produccion.py: turno propuesto, rendimientos, errores y avisos, estados (borrador, aprobado,
devolver), correcciones con historial y KPI solo con aprobados."""

from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from core.analisis import Filtros
from core.carga_produccion import (aprobados_del_dia, aprobar, calcular, devolver_a_borrador, editar_registro,
                                   guardar_borrador, lista_registros, rendimientos_del_dia, percentiles_rendimientos, promedio_ultimas_semanas,
                                   turno_recien_terminado, validar, valores_por_defecto)
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Cambio, Cliente, Planta, Proceso, RegistroProduccion, Turno
from core.proceso import turnos_sin_aprobar
from core.produccion import tabla_produccion, toneladas_por_turno, toneladas_turno

DIA = date(2026, 7, 1)
AHORA = datetime(2026, 7, 1, 14, 0)


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="Norte", pais="Argentina", cliente=Cliente(nombre="X", pais="Argentina"),
                        hora_inicio_dia=time(5))
        planta.turnos = [Turno(nombre="M", hora_inicio=time(5), hora_fin=time(12, 59)),
                         Turno(nombre="T", hora_inicio=time(13), hora_fin=time(20, 59)),
                         Turno(nombre="N", hora_inicio=time(21), hora_fin=time(4, 59))]
        sesion.add_all([planta, Proceso(numero=12, temporada=2026, planta=planta)])
        sesion.commit()
        yield sesion


def _planta(sesion):
    return sesion.scalars(select(Planta)).one()


def _datos(sesion, turno="M", dia=DIA, fruta=100.0, aceite=650.0, conc=6000.0, **otros):
    return {"dia": dia, "turno": turno, "proceso_id": sesion.scalars(select(Proceso)).one().id, "fruta_t": fruta,
            "aceite_envasado_kg": aceite, "jugo_conc_kg": conc, "reproceso_kg": 10.0, "comentario": None} | otros


def _turno(sesion, nombre):
    return next(t for t in _planta(sesion).turnos if t.nombre == nombre)


def test_turno_recien_terminado(sesion):
    planta = _planta(sesion)
    assert [(d, t.nombre) for d, t in [turno_recien_terminado(planta, AHORA)]] == [(DIA, "M")]
    assert turno_recien_terminado(planta, datetime(2026, 7, 2, 2, 0))[1].nombre == "T"        # en curso: N del 1/7
    dia, turno = turno_recien_terminado(planta, datetime(2026, 7, 2, 6, 0))
    assert (dia, turno.nombre) == (DIA, "N")
    defecto = valores_por_defecto(sesion, planta, AHORA)
    assert (defecto["dia"], defecto["turno"], defecto["procesos"][0][1]) == (DIA, "M", "2026 · proceso 12")


def test_rendimientos():
    assert calcular({"fruta_t": 100.0, "aceite_envasado_kg": 650.0, "jugo_conc_kg": 6000.0}) == {
        "rendimiento_aceite": 6.5, "rendimiento_concentrado": 60.0}
    assert calcular({"fruta_t": None, "aceite_envasado_kg": 650.0})["rendimiento_aceite"] is None


def test_errores_y_aviso_de_tipeo(sesion):
    errores, _ = validar(_datos(sesion, fruta=None, aceite=-5.0))
    assert errores == ["Aceite envasado (kg) no puede ser negativo (dice -5.0).", "Falta la fruta procesada."]
    percentiles = {"rendimiento_aceite": (4.0, 9.0), "rendimiento_concentrado_dia": (40.0, 80.0)}
    _, avisos = validar(_datos(sesion, aceite=6500.0), percentiles=percentiles)        # 65 kg/MT: un cero de más
    assert avisos == ["Rendimiento de aceite del turno 65.00 kg/MT fuera de lo habitual (4.00–9.00): ¿está bien "
                      "tipeado?"]
    assert validar(_datos(sesion), percentiles=percentiles) == ([], [])


def test_borrador_no_cuenta_hasta_aprobarlo(sesion):
    planta = _planta(sesion)
    registro = guardar_borrador(sesion, planta, _datos(sesion), "Ana", AHORA)
    assert (registro.estado, registro.origen_carga, registro.cargado_por) == ("borrador", "app", "Ana")
    assert tabla_produccion(sesion, planta.id).empty and toneladas_por_turno(sesion, planta.id).empty
    assert toneladas_turno(sesion, planta.id, DIA, _turno(sesion, "M").id) is None
    todas = tabla_produccion(sesion, planta.id, solo_aprobados=False)
    assert turnos_sin_aprobar(todas, Filtros(desde=DIA, hasta=DIA)) == 1

    assert aprobar(sesion, [registro.id], "Ramiro", AHORA) == 1
    assert (registro.estado, registro.aprobado_por, registro.aprobado_en) == ("aprobado", "Ramiro", AHORA)
    assert tabla_produccion(sesion, planta.id)["fruta_t"].tolist() == [100.0]
    assert toneladas_turno(sesion, planta.id, DIA, _turno(sesion, "M").id) == 100.0
    assert turnos_sin_aprobar(tabla_produccion(sesion, planta.id, solo_aprobados=False), Filtros()) == 0
    with pytest.raises(ValueError, match="no está en borrador"):
        aprobar(sesion, [registro.id], "Ramiro", AHORA)


def test_turno_ya_cargado(sesion):
    planta = _planta(sesion)
    registro = guardar_borrador(sesion, planta, _datos(sesion), "Ana", AHORA)
    with pytest.raises(ValueError, match="ya están cargados: editá el registro"):
        guardar_borrador(sesion, planta, _datos(sesion, fruta=120.0), "Ana", AHORA)
    assert validar(_datos(sesion), existente_id=registro.id, editando_id=registro.id)[0] == []
    guardar_borrador(sesion, planta, _datos(sesion, turno="T"), "Ana", AHORA)            # otro turno: sí


def test_editar_borrador_sin_historial_y_aprobado_con_motivo(sesion):
    planta = _planta(sesion)
    registro = guardar_borrador(sesion, planta, _datos(sesion), "Ana", AHORA)
    assert editar_registro(sesion, registro.id, {"fruta_t": 110.0}, "Ana", AHORA) == 1
    assert sesion.scalars(select(Cambio)).all() == []                                    # borrador: sin Cambio
    aprobar(sesion, [registro.id], "Ramiro", AHORA)
    with pytest.raises(ValueError, match="con motivo"):
        editar_registro(sesion, registro.id, {"aceite_envasado_kg": 700.0}, "Ramiro", AHORA)
    editar_registro(sesion, registro.id, {"aceite_envasado_kg": 700.0}, "Ramiro", AHORA, "faltaba un tambor")
    assert (registro.estado, registro.aceite_envasado_kg) == ("aprobado", 700.0)        # sigue aprobado
    edicion = sesion.scalars(select(Cambio).where(Cambio.accion == "edición")).one()
    assert (edicion.campo, edicion.valor_anterior, edicion.valor_nuevo, edicion.motivo) == (
        "aceite_envasado_kg", "650.0", "700.0", "faltaba un tambor")


def test_devolver_a_borrador(sesion):
    planta = _planta(sesion)
    registro = guardar_borrador(sesion, planta, _datos(sesion), "Ana", AHORA)
    aprobar(sesion, [registro.id], "Ramiro", AHORA)
    with pytest.raises(ValueError, match="motivo"):
        devolver_a_borrador(sesion, registro.id, " ", "Ramiro", AHORA)
    devolver_a_borrador(sesion, registro.id, "la fruta es de otro proceso", "Ramiro", AHORA)
    assert (registro.estado, registro.aprobado_por) == ("borrador", None)
    assert tabla_produccion(sesion, planta.id).empty                                    # deja de contar
    estados = [(c.valor_anterior, c.valor_nuevo) for c in sesion.scalars(select(Cambio).order_by(Cambio.id))]
    assert estados == [("borrador", "aprobado"), ("aprobado", "borrador")]
    assert lista_registros(sesion, planta.id, estados=["borrador"])["id"].tolist() == [registro.id]


def test_promedio_de_dos_semanas_y_percentiles(sesion):
    planta = _planta(sesion)
    for i, (fruta, aceite) in enumerate([(100.0, 600.0), (300.0, 2100.0)]):              # ponderado: 2700 / 400
        dia = DIA - timedelta(days=i + 1)
        r = guardar_borrador(sesion, planta, _datos(sesion, dia=dia, fruta=fruta, aceite=aceite, conc=None), "Ana",
                             AHORA)
        aprobar(sesion, [r.id], "Ramiro", AHORA)
    guardar_borrador(sesion, planta, _datos(sesion, dia=DIA - timedelta(days=3), aceite=9000.0), "Ana", AHORA)
    promedio = promedio_ultimas_semanas(sesion, planta.id, DIA)                          # el borrador no cuenta
    assert promedio["rendimiento_aceite"] == pytest.approx(6.75) and promedio["n_rendimiento_aceite"] == 2
    assert promedio["rendimiento_concentrado_dia"] is None and promedio["n_rendimiento_concentrado_dia"] == 0
    assert percentiles_rendimientos(sesion, planta.id, minimo=2)["rendimiento_aceite"] == pytest.approx(
        (6.005, 6.995))
    assert percentiles_rendimientos(sesion, planta.id) == {}                             # menos de 50



# ---------------------------------------------------------------- concentrado por día (se envasa por tanda)

def _aprobado(sesion, turno, **opciones):
    registro = guardar_borrador(sesion, _planta(sesion), _datos(sesion, turno=turno, **opciones), "Ana", AHORA)
    aprobar(sesion, [registro.id], "Ramiro", AHORA)
    return registro


def test_rendimientos_del_dia():
    del_dia = pd.DataFrame({"fruta_t": [100.0, 300.0], "aceite_envasado_kg": [650.0, None],
                            "jugo_conc_kg": [None, 24000.0]})
    dia = rendimientos_del_dia({"fruta_t": 100.0, "aceite_envasado_kg": 700.0, "jugo_conc_kg": None}, del_dia)
    assert dia["rendimiento_aceite_dia"] == pytest.approx(1350 / 200)               # solo turnos con aceite
    assert dia["rendimiento_concentrado_dia"] == pytest.approx(24000 / 500)         # sin concentrado cuenta 0
    assert dia["turnos"] == 3
    assert rendimientos_del_dia(None, del_dia.assign(jugo_conc_kg=None))["rendimiento_concentrado_dia"] is None


def test_aviso_de_concentrado_con_el_dia_completo(sesion):
    planta = _planta(sesion)
    percentiles = {"rendimiento_concentrado_dia": (40.0, 80.0)}
    turno_t = _datos(sesion, turno="T", fruta=100.0, conc=2000.0)                     # el turno solo: 20 kg/MT
    assert any("concentrado 65 °Bx del día 20.00" in a for a in validar(turno_t, percentiles=percentiles)[1])
    _aprobado(sesion, "M", fruta=300.0, conc=22000.0)                                  # con el turno M: 60 kg/MT
    del_dia = aprobados_del_dia(sesion, planta.id, DIA)
    assert validar(turno_t, percentiles=percentiles, del_dia=del_dia)[1] == []


def test_percentiles_y_promedio_de_concentrado_por_dia(sesion):
    planta = _planta(sesion)
    for i in range(3):                                                                 # 3 días: M con tanda, T sin
        dia = DIA - timedelta(days=i + 1)
        _aprobado(sesion, "M", dia=dia, fruta=100.0, conc=12000.0 + 1000 * i)
        _aprobado(sesion, "T", dia=dia, fruta=100.0, conc=None)
    promedio = promedio_ultimas_semanas(sesion, planta.id, DIA)
    assert promedio["rendimiento_concentrado_dia"] == pytest.approx((12000 + 13000 + 14000) / 600)   # 65
    assert promedio["n_rendimiento_concentrado_dia"] == 3
    assert percentiles_rendimientos(sesion, planta.id, minimo=3)["rendimiento_concentrado_dia"] == pytest.approx(
        (60.05, 69.95))                                                               # días 60, 65 y 70
    lista = lista_registros(sesion, planta.id).set_index(["fecha_produccion", "turno"])
    fila = lista.loc[(DIA - timedelta(days=1), "T")]
    assert (fila["rendimiento_aceite"], fila["rendimiento_concentrado_dia"]) == (6.5, 60.0)
    assert fila["rendimiento_aceite_dia"] == pytest.approx(6.5)
