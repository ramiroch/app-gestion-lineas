"""Pruebas de core/inicio.py: configuración del flowsheet, fecha de referencia, turnos, marcadores (valor, semáforo,
gris sin datos, secundaria con su nombre, alertas), pendientes y tendencia semanal."""

import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from core.carga_scott import guardar_ronda
from core.inicio import (AMARILLO, EN_MARCHA, NEUTRO, ROJO, SIN_DATOS, VERDE, _color_fp, cargar_flowsheet, carpeta_planta, color_de, datos_flowsheet,
                         fecha_referencia, objetivo_eficiencia, pendientes, rondas_esperadas, tendencia_semanal, turnos_con_datos)
from core.models import (Alerta, Equipo, Linea, MuestraCentrifuga, Planta, RangoReferencia, RegistroProduccion,
                         Turno)
from tests.test_carga_scott import _planta, ronda, sesion  # noqa: F401  (fixture)

DIA = date(2026, 7, 1)
CONFIG = {"imagen": "flowsheet.jpg", "ancho": 1034, "alto": 775, "marcadores": [
    {"tipo": "nombre_eboe", "equipo": "N-BOE-01", "x": 0.07, "y": 0.15},
    {"tipo": "retorno_fp_eboe", "equipo": "N-BOE-01", "x": 0.275, "y": 0.40},
    {"tipo": "entrada_eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.05},
    {"tipo": "eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.18},
    {"tipo": "eboe", "equipo": "N-BOE-02", "x": 0.42, "y": 0.18},
    {"tipo": "salida_eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.29},
    {"tipo": "emulsion_eboe", "equipo": "N-BOE-01", "x": 0.14, "y": 0.33},
    {"tipo": "emulsion_eboe", "equipo": "N-BOE-02", "x": 0.36, "y": 0.33},
    {"tipo": "tanque_emulsion", "x": 0.10, "y": 0.78},
    {"tipo": "retorno_pesada", "x": 0.85, "y": 0.45},
    {"tipo": "primaria", "equipo": "N-CEN-01", "x": 0.39, "y": 0.62},
    {"tipo": "primaria_caudal", "equipo": "N-CEN-01", "x": 0.375, "y": 0.545},
    {"tipo": "primaria_descarga", "equipo": "N-CEN-01", "x": 0.335, "y": 0.675},
    {"tipo": "primaria_liviana", "equipo": "N-CEN-01", "x": 0.45, "y": 0.575},
    {"tipo": "primaria_pesada", "equipo": "N-CEN-01", "x": 0.33, "y": 0.575},
    {"tipo": "primaria", "equipo": "N-CEN-02", "x": 0.39, "y": 0.83},
    {"tipo": "primaria_descarga", "equipo": "N-CEN-02", "x": 0.335, "y": 0.885},
    {"tipo": "secundaria", "equipos": ["N-CSE-01", "N-CSE-02"], "x": 0.57, "y": 0.78},
    {"tipo": "pulidora", "equipo": "N-PUL-01", "x": 0.75, "y": 0.79}]}


@pytest.fixture
def planta(sesion):  # noqa: F811
    """La planta de la carga Scott con centrífugas, rangos de descarga y de emulsión (sin alerta) y horas de rondas."""
    planta = _planta(sesion)
    planta.horas_rondas = "05:10, 07:10, 09:10, 11:10, 13:10, 15:10, 17:10, 19:10, 21:10, 23:10, 01:10, 03:10"
    planta.umbral_paralelo_lh = 17000
    planta.tolerancia_fp_verde_pct, planta.tolerancia_fp_amarillo_pct = 10, 20
    linea = sesion.scalars(select(Linea)).one()
    for codigo, nombre, tipo in (("N-CEN-01", "ESE 230 (1)", "Centrífuga primaria"),
                                 ("N-CEN-02", "ESE 230 (2)", "Centrífuga primaria"),
                                 ("N-CSE-01", "ESB 130", "Centrífuga secundaria"),
                                 ("N-CSE-02", "MSD 50", "Centrífuga secundaria"), ("N-PUL-01", "OSD 60", "Pulidora")):
        sesion.add(Equipo(codigo=codigo, nombre=nombre, tipo=tipo, numero=1, linea=linea, volumen_descarga_l=16))
    sesion.add_all([
        RangoReferencia(planta=planta, variable="Aceite en descarga", punto_o_equipo="Centrífuga primaria", maximo=0.5),
        RangoReferencia(planta=planta, variable="Aceite en descarga", punto_o_equipo="Centrífuga secundaria",
                        maximo=0.75),
        RangoReferencia(planta=planta, variable="Aceite en emulsión", punto_o_equipo="Emulsión (licor)",
                        fruta="Naranja", minimo=0.8, maximo=1.1, genera_alerta=False)])
    sesion.commit()
    return planta


def _muestra(sesion, codigo, fecha_hora, fase, ml=None, **otros):
    """Muestra de centrífuga titulada (10 g, N 0,1, blanco 0, factor 3,5 → aceite = mL × 0,035 %)."""
    equipo = sesion.scalars(select(Equipo).where(Equipo.codigo == codigo)).one()
    titulacion = {"gramos": 10.0, "ml": ml, "normalidad": 0.1, "blanco_ml": 0.0} if ml is not None else {}
    sesion.add(MuestraCentrifuga(equipo_id=equipo.id, fecha_hora=fecha_hora, fase=fase, cargado_por="prueba",
                                 **titulacion, **otros))
    sesion.commit()


def _ronda(sesion, momento, **opciones):  # noqa: F811
    guardar_ronda(sesion, _planta(sesion), ronda(sesion, momento, **opciones), "Ana", ahora=momento)


# ---------------------------------------------------------------- configuración y semáforo

def test_cargar_flowsheet(tmp_path):
    assert carpeta_planta("Planta Ñandú", tmp_path) == tmp_path / "planta_nandu"
    assert cargar_flowsheet("Norte", tmp_path) is None                         # sin configuración: no aparece
    carpeta = tmp_path / "norte"
    carpeta.mkdir()
    (carpeta / "flowsheet.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    with pytest.raises(ValueError, match="no encuentra su imagen"):
        cargar_flowsheet("Norte", tmp_path)
    (carpeta / "flowsheet.jpg").write_bytes(b"imagen")
    config = cargar_flowsheet("Norte", tmp_path)
    assert len(config["marcadores"]) == len(CONFIG["marcadores"]) and config["ruta_imagen"].endswith("flowsheet.jpg")
    for cambio, error in (({"x": 1.2}, "entre 0 y 1"), ({"tipo": "bomba"}, "no válido"), ({"equipo": None}, "falta")):
        malo = CONFIG | {"marcadores": [CONFIG["marcadores"][1] | cambio]}
        (carpeta / "flowsheet.json").write_text(json.dumps(malo), encoding="utf-8")
        with pytest.raises(ValueError, match=error):
            cargar_flowsheet("Norte", tmp_path)


def test_color_del_semaforo():
    inferior = {"minimo": 80, "maximo": 90, "lado": "inferior", "genera_alerta": True}
    assert color_de(None, inferior)[0] == SIN_DATOS
    assert color_de(7.5, None) == (NEUTRO, "sin rango de referencia")
    assert (color_de(75, inferior)[0], color_de(95, inferior)[0]) == (ROJO, VERDE)      # arriba de 90 no es malo
    en_revision = {"minimo": 0.8, "maximo": 1.1, "lado": "ambos", "genera_alerta": False}
    assert color_de(1.4, en_revision) == (NEUTRO, "rango en revisión (0,80–1,10)")


# ---------------------------------------------------------------- fecha, turno y marcadores

def test_fecha_de_referencia_y_turnos(sesion, planta):  # noqa: F811
    assert fecha_referencia(sesion, planta, DIA) is None
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "descarga", ml=10.0)
    assert fecha_referencia(sesion, planta, DIA) == DIA                        # hoy tiene datos
    assert fecha_referencia(sesion, planta, date(2026, 11, 11)) == DIA         # fuera de temporada: el último
    assert turnos_con_datos(sesion, planta, DIA) == ["M", "T"]                 # T solo con centrífugas


def test_marcadores_del_turno(sesion, planta, tmp_path):  # noqa: F811
    _ronda(sesion, datetime(2026, 7, 1, 14, 10), salida_ml={2: 30.0})          # eBOE 2: eficiencia 70 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "descarga", ml=10.0, t_descarga_s=200.0)   # 0,35 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "emulsion", ml=20.0, caudal_lh=15000.0)  # 0,70 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "fase_liviana", aceite_pct_medido=70.0)
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "fase_pesada", ml=2.0)        # 0,07 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 16, 30), "emulsion", ml=24.0, caudal_lh=16000.0)  # 0,84 %
    _muestra(sesion, "N-CSE-02", datetime(2026, 7, 1, 14, 30), "descarga", ml=40.0)          # MSD 50: 1,40 %
    _muestra(sesion, "N-PUL-01", datetime(2026, 7, 1, 14, 30), "descarga", t_descarga_s=2700.0)   # 45 min
    sesion.add(Alerta(origen="nelson", regla="1", variable="eficiencia", ambito="equipo:2",
                      equipo_id=sesion.scalars(select(Equipo).where(Equipo.codigo == "N-BOE-02")).one().id,
                      fecha_produccion=DIA, turno_id=sesion.scalars(select(Turno).where(Turno.nombre == "T")).one().id,
                      fecha_hora=datetime(2026, 7, 1, 14, 10), valor=70.0, estado="abierta", creada_por="prueba"))
    sesion.add(Alerta(origen="rango", regla="rango", variable="ph", ambito=f"planta:{planta.id}", planta_id=planta.id,
                      fecha_produccion=DIA, turno_id=sesion.scalars(select(Turno).where(Turno.nombre == "T")).one().id,
                      fecha_hora=datetime(2026, 7, 1, 14, 10), valor=7.5, estado="en análisis", creada_por="prueba"))
    sesion.commit()
    marcadores = {(m["tipo"], m["nombre"]): m for m in datos_flowsheet(sesion, planta, CONFIG, DIA, "T")}

    entrada = marcadores[("entrada_eboe", "Entrada eBOE 1")]
    assert (entrada["texto"], entrada["color"]) == ("7,00 kg/MT", NEUTRO) and "Frutos en 10 kg: 50" in entrada["hover"]
    eboe1, eboe2 = marcadores[("eboe", "eBOE 1")], marcadores[("eboe", "eBOE 2")]
    assert (eboe1["texto"], eboe1["color"], eboe1["alertas"]) == ("ef. 90,0 %", VERDE, 0)       # solo la eficiencia
    assert (eboe2["color"], eboe2["alertas"], eboe2["ambitos"], eboe2["destino"]) == (
        ROJO, 1, ["eBOE 2"], {"pagina": "analisis", "eboe": 2})
    salida = marcadores[("salida_eboe", "Salida eBOE 1")]
    assert (salida["texto"], salida["color"], salida["alertas"]) == ("0,70 kg/MT", NEUTRO, 0) and "N = 1" in salida["hover"]
    emulsion = marcadores[("emulsion_eboe", "Emulsión eBOE 1")]                  # rebalse del eBOE (laboratorio)
    assert (emulsion["texto"], emulsion["color"]) == ("0,88 %", NEUTRO) and "rango en revisión" in emulsion["hover"]
    assert (emulsion["alertas"], emulsion["ambitos"]) == (1, ["Planta"])        # el pH es de la planta
    tanque = marcadores[("tanque_emulsion", "Tanque de emulsión")]               # primarias (centrífugas), mediana
    assert (tanque["texto"], tanque["destino"]) == ("0,77 %", {"pagina": "centrifugas"})
    assert "15.500 L/h" in tanque["hover"] and "N = 2" in tanque["hover"]
    nombre = marcadores[("nombre_eboe", "eBOE 1")]                              # sin círculo: nombre y MTPH
    assert (nombre["texto"], nombre["color"]) == ("<b>eBOE 1</b><br>11,0 MTPH", None)
    fase_pesada = marcadores[("retorno_fp_eboe", "Fase pesada a eBOE 1")]
    assert fase_pesada["texto"].startswith("9,5 m³/h<br>") and "calc." in fase_pesada["texto"]
    assert "Calculado (agua por eBOE" in fase_pesada["hover"]
    primaria = marcadores[("primaria", "ESE 230 (1)")]                           # el cuerpo: solo el estado
    assert (primaria["texto"], primaria["color"]) == ("ESE 230 (1)", EN_MARCHA)
    assert marcadores[("primaria_caudal", "Caudal ESE 230 (1)")]["texto"] == "15.500 L/h"
    descarga = marcadores[("primaria_descarga", "Descarga ESE 230 (1)")]
    assert (descarga["texto"], descarga["color"]) == ("0,35 %", VERDE)
    liviana = marcadores[("primaria_liviana", "Fase liviana ESE 230 (1)")]
    assert liviana["texto"].endswith(" · 70 %") and "balance" in liviana["hover"]
    assert marcadores[("primaria_pesada", "Fase pesada ESE 230 (1)")]["texto"].endswith(" L/h")
    parada = marcadores[("primaria", "ESE 230 (2)")]
    assert (parada["texto"], parada["color"]) == ("ESE 230 (2) · sin datos", SIN_DATOS)    # no anduvo: gris
    linea_parada = marcadores[("primaria_descarga", "ESE 230 (2)")]
    assert (linea_parada["texto"], linea_parada["color"]) == ("", SIN_DATOS)           # sus datos de línea: vacíos
    retorno = marcadores[("retorno_pesada", "Retorno de fase pesada")]               # aceite en fase pesada
    assert retorno["texto"] == "0,07 %" and "Caudal total de fase pesada" in retorno["hover"]
    secundaria = marcadores[("secundaria", "MSD 50")]                            # la que anduvo en el turno
    assert (secundaria["texto"], secundaria["color"]) == ("MSD 50 · 1,40 %", ROJO)
    assert marcadores[("pulidora", "OSD 60")]["texto"] == "45 min"


# ---------------------------------------------------------------- pendientes y tendencia

def test_rondas_que_faltan_en_el_turno_en_curso(sesion, planta):  # noqa: F811
    turno_m = sesion.scalars(select(Turno).where(Turno.nombre == "M")).one()
    assert [m.time() for m in rondas_esperadas(planta, DIA, turno_m)] == [time(5, 10), time(7, 10), time(9, 10),
                                                                          time(11, 10)]
    _ronda(sesion, datetime(2026, 7, 1, 5, 15))                                 # la de 05:10, atrasada
    _ronda(sesion, datetime(2026, 7, 1, 9, 40), entrada_ml={1: 9.0, 2: 9.2, 3: 9.4})   # la de 09:10
    rondas = pendientes(sesion, planta, datetime(2026, 7, 1, 11, 0), DIA)["rondas"]
    assert (rondas["turno"], rondas["hechas"], rondas["esperadas"]) == ("M", 2, 4)
    assert rondas["faltan"] == [datetime(2026, 7, 1, 7, 10)]                    # la de 11:10 todavía no venció
    assert pendientes(sesion, planta, datetime(2026, 7, 5, 11, 0), DIA)["rondas"] is None   # no es hoy


def test_turnos_sin_produccion_y_borradores(sesion, planta):  # noqa: F811
    _ronda(sesion, datetime(2026, 7, 1, 6, 10))
    _ronda(sesion, datetime(2026, 7, 1, 14, 10), entrada_ml={1: 9.0, 2: 9.2, 3: 9.4})
    turno_t = sesion.scalars(select(Turno).where(Turno.nombre == "T")).one()
    sesion.add(RegistroProduccion(planta_id=planta.id, fecha_produccion=DIA, turno_id=turno_t.id, fruta_t=300.0,
                                  estado="borrador", origen_carga="app", cargado_por="Ana"))
    sesion.commit()
    resultado = pendientes(sesion, planta, datetime(2026, 7, 1, 22, 0), DIA)
    assert resultado["sin_produccion"] == [(DIA, "M")] and resultado["borradores"] == 1
    assert pendientes(sesion, planta, datetime(2026, 7, 1, 12, 0), DIA)["sin_produccion"] == []   # M sigue en curso


def test_tendencia_semanal_ponderada(sesion, planta):  # noqa: F811
    turno_m = sesion.scalars(select(Turno).where(Turno.nombre == "M")).one()
    for dias_atras, salida in ((0, 10.0), (1, 30.0), (8, 20.0)):                # eficiencia 90 %, 70 % y 80 %
        dia = DIA - timedelta(days=dias_atras)
        _ronda(sesion, datetime.combine(dia, time(6, 10)), salida_ml={1: salida})
        sesion.add(RegistroProduccion(planta_id=planta.id, fecha_produccion=dia, turno_id=turno_m.id,
                                      fruta_t=100.0 if dias_atras == 0 else 300.0, estado="aprobado",
                                      origen_carga="app", cargado_por="Ana"))
    sesion.commit()
    tabla = tendencia_semanal(sesion, planta, DIA, semanas=2)
    eficiencia = tabla[(tabla["eboe"] == 1) & (tabla["variable"] == "eficiencia")].set_index("semana")
    assert eficiencia.loc[0, "valor"] == pytest.approx((90 * 100 + 70 * 300) / 400)   # 75, ponderado
    assert (eficiencia.loc[0, "n"], eficiencia.loc[0, "etiqueta"]) == (2, "25/06–01/07")
    assert eficiencia.loc[1, "valor"] == pytest.approx(80.0) and eficiencia.loc[1, "ponderada"]

    objetivo = objetivo_eficiencia(sesion, planta, DIA)                          # fruta "N Val S/R" → naranja
    assert (objetivo["minimo"], objetivo["maximo"], objetivo["lado"]) == (80, 90, "inferior")



def test_semaforo_del_caudal_de_fase_pesada(planta):
    # |lab − calc| / calc: 9,5 contra 10 → 5 % verde; contra 8,5 → 11,8 % amarillo; contra 7 → 35,7 % rojo
    assert _color_fp(planta, 9.5, 10.0)[0] == VERDE
    assert _color_fp(planta, 9.5, 8.5)[0] == AMARILLO
    assert _color_fp(planta, 9.5, 7.0) == (ROJO, "diferencia 36 % (verde ≤ 10 %, amarillo ≤ 20 %)")
    assert _color_fp(planta, 9.5, None)[0] == NEUTRO and _color_fp(planta, None, 10.0)[0] == SIN_DATOS
