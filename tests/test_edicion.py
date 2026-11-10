"""Pruebas de core/edicion.py: edición, borrado, restaurar, cambio de hora, reevaluación de alertas al corregir y
cierre automático por "dato corregido"."""

import json
from datetime import date, datetime

import pytest
from sqlalchemy import select

from core.cambios import claves_protegidas, puede_editar
from core.carga_scott import evaluar_turnos_cerrados, guardar_ronda
from core.edicion import (borrar_dato, borrar_ronda, cambiar_hora_ronda, datos_de_ronda, editar_dato, editar_datos,
                          lista_rondas, restaurar)
from core.models import Alerta, AnalisisScott, Cambio, Equipo, LecturaOperacion, PuntoMuestreo
from tests.test_carga_scott import _planta, ronda, sesion  # noqa: F401  (fixture)

R1 = datetime(2026, 7, 1, 6, 10)


def _cargar(sesion, momento=R1, **opciones):  # noqa: F811
    guardar_ronda(sesion, _planta(sesion), ronda(sesion, momento, **opciones), "Ana", ahora=momento)


def _analisis(sesion, eboe, tipo, momento=R1):  # noqa: F811
    return sesion.scalars(select(AnalisisScott).join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).where(
        Equipo.numero == eboe, PuntoMuestreo.tipo == tipo, AnalisisScott.fecha_hora == momento)).one()


def test_editar_registra_cada_campo(sesion):  # noqa: F811
    _cargar(sesion)
    entrada = _analisis(sesion, 1, "fruta entrada")
    resultado = editar_dato(sesion, _planta(sesion), "analisis_scott", entrada.id,
                            {"vol_ml": 10.5, "n_frutos": 40, "calidad": "Blanda"}, "tipeo de la planilla", "Ramiro",
                            datetime(2026, 7, 1, 7))
    assert resultado["cambios"] == 2                                   # n_frutos ya era 40: no cuenta
    assert (entrada.vol_ml, entrada.calidad) == (10.5, "Blanda")
    cambios = {c.campo: c for c in sesion.scalars(select(Cambio))}
    assert (cambios["vol_ml"].valor_anterior, cambios["vol_ml"].valor_nuevo) == ("10.0", "10.5")
    assert (cambios["vol_ml"].motivo, cambios["vol_ml"].cambiado_por, cambios["vol_ml"].accion) == (
        "tipeo de la planilla", "Ramiro", "edición")
    with pytest.raises(ValueError, match="motivo"):
        editar_dato(sesion, _planta(sesion), "analisis_scott", entrada.id, {"vol_ml": 11.0}, " ", "Ramiro", R1)
    with pytest.raises(ValueError, match="No se pueden editar: fecha_hora"):
        editar_dato(sesion, _planta(sesion), "analisis_scott", entrada.id, {"fecha_hora": R1}, "x", "Ramiro", R1)
    with pytest.raises(ValueError, match="mayores que 0"):
        editar_dato(sesion, _planta(sesion), "analisis_scott", entrada.id, {"peso_muestra_g": 0.0}, "x", "Ramiro", R1)
    assert puede_editar("cualquiera", entrada)                          # tarea 22: permisos por rol


def test_borrar_guarda_la_fila_y_restaurar_la_vuelve_a_crear(sesion):  # noqa: F811
    _cargar(sesion)
    planta, salida = _planta(sesion), _analisis(sesion, 1, "fruta salida")
    original = (salida.id, salida.vol_ml, salida.normalidad, salida.cargado_por)
    borrar_dato(sesion, planta, "analisis_scott", salida.id, "muestra contaminada", "Ramiro", datetime(2026, 7, 1, 7))
    assert sesion.get(AnalisisScott, original[0]) is None
    (borrado,) = sesion.scalars(select(Cambio).where(Cambio.accion == "borrado")).all()
    assert json.loads(borrado.valor_anterior)["vol_ml"] == 10.0
    assert ("analisis_scott", (salida.punto_muestreo_id, R1)) in claves_protegidas(sesion)

    restaurar(sesion, planta, [borrado.id], "era válida", "Ramiro", datetime(2026, 7, 1, 8))
    vuelta = sesion.get(AnalisisScott, original[0])                     # el mismo id, los mismos valores
    assert (vuelta.id, vuelta.vol_ml, vuelta.normalidad, vuelta.cargado_por) == original
    with pytest.raises(ValueError, match="ya se restauró"):
        restaurar(sesion, planta, [borrado.id], "otra vez", "Ramiro", datetime(2026, 7, 1, 9))


def test_restaurar_no_choca_con_otro_dato(sesion):  # noqa: F811
    _cargar(sesion)
    planta, salida = _planta(sesion), _analisis(sesion, 1, "fruta salida")
    punto_id = salida.punto_muestreo_id
    borrar_dato(sesion, planta, "analisis_scott", salida.id, "error", "Ramiro", datetime(2026, 7, 1, 7))
    sesion.add(AnalisisScott(punto_muestreo_id=punto_id, proceso_id=salida.proceso_id, fecha_hora=R1, analista="Ana",
                             peso_muestra_g=10.0, vol_ml=11.0, blanco_ml=0.0, normalidad=0.01, cargado_por="Ana"))
    sesion.commit()
    (borrado,) = sesion.scalars(select(Cambio).where(Cambio.accion == "borrado")).all()
    with pytest.raises(ValueError, match="ya hay otro dato"):
        restaurar(sesion, planta, [borrado.id], "era válida", "Ramiro", datetime(2026, 7, 1, 8))


def test_borrar_y_restaurar_la_ronda_completa(sesion):  # noqa: F811
    _cargar(sesion)
    planta = _planta(sesion)
    resultado = borrar_ronda(sesion, planta, R1, "ronda duplicada", "Ramiro", datetime(2026, 7, 1, 7))
    assert resultado["borrados"] == 12 and datos_de_ronda(sesion, planta, R1).empty   # 9 análisis y 3 lecturas
    ids = [c.id for c in sesion.scalars(select(Cambio).where(Cambio.grupo == resultado["grupo"]))]
    assert restaurar(sesion, planta, ids, "no era duplicada", "Ramiro", datetime(2026, 7, 1, 8))["restaurados"] == 12
    assert len(datos_de_ronda(sesion, planta, R1)) == 12


def test_cambiar_la_hora_de_la_ronda(sesion):  # noqa: F811
    _cargar(sesion)
    _cargar(sesion, datetime(2026, 7, 1, 8, 10), entrada_ml={1: 9.0, 2: 9.2, 3: 9.4})
    planta = _planta(sesion)
    with pytest.raises(ValueError, match="Ya hay una ronda"):
        cambiar_hora_ronda(sesion, planta, R1, datetime(2026, 7, 1, 8, 10), "hora mal", "Ramiro", R1)
    resultado = cambiar_hora_ronda(sesion, planta, R1, datetime(2026, 7, 1, 7, 10), "hora mal anotada", "Ramiro",
                                   datetime(2026, 7, 1, 9))
    assert resultado["datos"] == 12 and datos_de_ronda(sesion, planta, R1).empty
    assert len(datos_de_ronda(sesion, planta, datetime(2026, 7, 1, 7, 10))) == 12
    assert {c.valor_anterior for c in sesion.scalars(select(Cambio).where(Cambio.accion == "hora"))} == {
        "2026-07-01T06:10:00"}


def test_lista_rondas_y_filtros(sesion):  # noqa: F811
    _cargar(sesion, entrada_ml={1: 10.0, 2: 10.0})                       # entradas repetidas en los eBOE 1 y 2
    planta = _planta(sesion)
    editar_dato(sesion, planta, "lectura_operacion", sesion.scalars(select(LecturaOperacion)).first().id,
                {"tph": 12.0}, "tipeo", "Ramiro", datetime(2026, 7, 1, 7))
    tabla = lista_rondas(sesion, planta, desde=date(2026, 7, 1), hasta=date(2026, 7, 1))
    assert len(tabla) == 3 and tabla["eficiencia"].round(1).tolist() == [90.0, 90.0, 90.4]
    assert len(lista_rondas(sesion, planta, solo_revisar=True)) == 2
    assert len(lista_rondas(sesion, planta, solo_editados=True)) == 1
    assert lista_rondas(sesion, planta, eboes=[3], turnos=["T"]).empty


# ---------------------------------------------------------------- reevaluación de alertas

def _alerta_cerrada_el_turno(sesion):  # noqa: F811
    """Turno M con la eficiencia del eBOE 1 al 70 % (alertas de regla 1 y rango, evaluado al cerrar)."""
    planta = _planta(sesion)
    planta.turnos_para_alertar = 1
    sesion.commit()
    _cargar(sesion, salida_ml={1: 30.0})
    evaluar_turnos_cerrados(sesion, planta, datetime(2026, 7, 1, 13, 5), "Ramiro")
    return planta


def test_corregir_el_dato_cierra_la_alerta_que_queda_sin_datos(sesion):  # noqa: F811
    planta = _alerta_cerrada_el_turno(sesion)
    assert {(a.regla, a.estado) for a in sesion.scalars(select(Alerta))} == {("1", "abierta"), ("rango", "abierta")}
    salida = _analisis(sesion, 1, "fruta salida")
    resultado = editar_dato(sesion, planta, "analisis_scott", salida.id, {"vol_ml": 10.0}, "era 10,0 y no 30,0",
                            "Ramiro", datetime(2026, 7, 1, 14))
    alertas = sesion.scalars(select(Alerta)).all()
    assert len(alertas) == 2                                            # no se borran
    assert all((a.estado, a.cerrada_por, a.comentario, a.datos) == ("cerrada", "sistema", "dato corregido", [])
               for a in alertas)
    assert {c["cambio"] for c in resultado["alertas"]} == {"actualizada"}
    assert len(sesion.scalars(select(Cambio).where(Cambio.tabla == "alerta", Cambio.campo == "estado")).all()) == 2


def test_corregir_sin_cambiar_la_violacion_la_mantiene(sesion):  # noqa: F811
    planta = _alerta_cerrada_el_turno(sesion)
    salida = _analisis(sesion, 1, "fruta salida")
    editar_dato(sesion, planta, "analisis_scott", salida.id, {"vol_ml": 29.0}, "redondeo", "Ramiro",
                datetime(2026, 7, 1, 14))
    alertas = sesion.scalars(select(Alerta)).all()
    assert len(alertas) == 2 and all(a.estado == "abierta" and a.datos and a.comentario is None for a in alertas)


def test_episodio_de_dos_turnos_pierde_solo_el_corregido(sesion):  # noqa: F811
    planta = _alerta_cerrada_el_turno(sesion)
    _cargar(sesion, datetime(2026, 7, 1, 14, 10), salida_ml={1: 30.0}, entrada_ml={1: 9.0, 2: 9.2, 3: 9.4})
    evaluar_turnos_cerrados(sesion, planta, datetime(2026, 7, 1, 21, 5), "Ramiro")
    regla_1 = next(a for a in sesion.scalars(select(Alerta)) if a.regla == "1")
    assert regla_1.turnos_afectados == 2
    salida_t = _analisis(sesion, 1, "fruta salida", datetime(2026, 7, 1, 14, 10))
    editar_dato(sesion, planta, "analisis_scott", salida_t.id, {"vol_ml": 9.0}, "tipeo", "Ramiro",
                datetime(2026, 7, 1, 22))
    sesion.refresh(regla_1)
    assert (regla_1.estado, regla_1.turnos_afectados, regla_1.fin) == ("abierta", 1, R1)
    assert regla_1.comentario == "dato corregido en el turno 01/07 T"


def test_editar_varios_datos_de_una_vez(sesion):  # noqa: F811
    _cargar(sesion)
    planta = _planta(sesion)
    entrada, salida = _analisis(sesion, 1, "fruta entrada"), _analisis(sesion, 1, "fruta salida")
    resultado = editar_datos(sesion, planta, [("analisis_scott", entrada.id, {"vol_ml": 10.5}),
                                              ("analisis_scott", salida.id, {"vol_ml": 11.0, "rallado": "Ok"})],
                             "planilla mal pasada", "Ramiro", datetime(2026, 7, 1, 7))
    assert resultado["cambios"] == 3
    assert {c.grupo for c in sesion.scalars(select(Cambio))} == {resultado["grupo"]}
    with pytest.raises(ValueError, match="No se puede guardar el dato"):                # si algo falla, nada cambia
        editar_datos(sesion, planta, [("analisis_scott", entrada.id, {"vol_ml": 12.0}),
                                      ("analisis_scott", salida.id, {"peso_muestra_g": -1.0})], "x", "Ramiro", R1)
    assert entrada.vol_ml == 10.5


def test_alerta_historico_corregida_no_cambia_de_estado(sesion):  # noqa: F811
    planta = _alerta_cerrada_el_turno(sesion)
    for alerta in sesion.scalars(select(Alerta)):
        alerta.estado, alerta.confirmada = "histórico", None
    sesion.commit()
    salida = _analisis(sesion, 1, "fruta salida")
    editar_dato(sesion, planta, "analisis_scott", salida.id, {"vol_ml": 10.0}, "tipeo", "Ramiro",
                datetime(2026, 7, 1, 14))
    for alerta in sesion.scalars(select(Alerta)):
        assert (alerta.estado, alerta.comentario, alerta.episodio_abierto, alerta.datos) == (
            "histórico", "dato corregido", False, [])
