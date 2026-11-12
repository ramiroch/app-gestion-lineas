"""Pruebas de core/inicio.py: configuración del flowsheet, fecha de referencia, turnos, marcadores (valor, semáforo,
gris sin datos, secundaria con su nombre, alertas), pendientes y tendencia semanal."""

import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from core.carga_scott import guardar_ronda
from core.inicio import (NEUTRO, ROJO, SIN_DATOS, VERDE, cargar_flowsheet, carpeta_planta, color_de, datos_flowsheet,
                         fecha_referencia, objetivo_eficiencia, pendientes, rondas_esperadas, tendencia_semanal, turnos_con_datos)
from core.models import (Alerta, Equipo, Linea, MuestraCentrifuga, Planta, RangoReferencia, RegistroProduccion,
                         Turno)
from tests.test_carga_scott import _planta, ronda, sesion  # noqa: F401  (fixture)

DIA = date(2026, 7, 1)
CONFIG = {"imagen": "flowsheet.jpg", "ancho": 1034, "alto": 775, "marcadores": [
    {"tipo": "entrada_eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.05},
    {"tipo": "eboe", "equipo": "N-BOE-01", "x": 0.19, "y": 0.18},
    {"tipo": "eboe", "equipo": "N-BOE-02", "x": 0.42, "y": 0.18},
    {"tipo": "tanque_emulsion", "x": 0.10, "y": 0.78},
    {"tipo": "primaria", "equipo": "N-CEN-01", "x": 0.39, "y": 0.62},
    {"tipo": "primaria", "equipo": "N-CEN-02", "x": 0.39, "y": 0.84},
    {"tipo": "secundaria", "equipos": ["N-CSE-01", "N-CSE-02"], "x": 0.57, "y": 0.78},
    {"tipo": "pulidora", "equipo": "N-PUL-01", "x": 0.75, "y": 0.79}]}


@pytest.fixture
def planta(sesion):  # noqa: F811
    """La planta de la carga Scott con centrífugas, rangos de descarga y de emulsión (sin alerta) y horas de rondas."""
    planta = _planta(sesion)
    planta.horas_rondas = "05:10, 07:10, 09:10, 11:10, 13:10, 15:10, 17:10, 19:10, 21:10, 23:10, 01:10, 03:10"
    planta.umbral_paralelo_lh = 17000
    linea = sesion.scalars(select(Linea)).one()
    for codigo, nombre, tipo in (("N-CEN-01", "ESE 230 (1)", "Centrífuga primaria"),
                                 ("N-CEN-02", "ESE 230 (2)", "Centrífuga primaria"),
                                 ("N-CSE-01", "ESB 130", "Centrífuga secundaria"),
                                 ("N-CSE-02", "MSD 50", "Centrífuga secundaria"), ("N-PUL-01", "OSD 60", "Pulidora")):
        sesion.add(Equipo(codigo=codigo, nombre=nombre, tipo=tipo, numero=1, linea=linea))
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
    assert len(config["marcadores"]) == 8 and config["ruta_imagen"].endswith("flowsheet.jpg")
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
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "descarga", ml=10.0)          # 0,35 %
    _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 14, 30), "emulsion", caudal_lh=15000.0)
    _muestra(sesion, "N-CSE-02", datetime(2026, 7, 1, 14, 30), "descarga", ml=40.0)          # MSD 50: 1,40 %
    _muestra(sesion, "N-PUL-01", datetime(2026, 7, 1, 14, 30), "descarga", t_descarga_s=2700.0)   # 45 min
    sesion.add(Alerta(origen="nelson", regla="1", variable="eficiencia", ambito="equipo:2",
                      equipo_id=sesion.scalars(select(Equipo).where(Equipo.codigo == "N-BOE-02")).one().id,
                      fecha_produccion=DIA, turno_id=sesion.scalars(select(Turno).where(Turno.nombre == "T")).one().id,
                      fecha_hora=datetime(2026, 7, 1, 14, 10), valor=70.0, estado="abierta", creada_por="prueba"))
    sesion.commit()
    marcadores = {(m["tipo"], m["nombre"]): m for m in datos_flowsheet(sesion, planta, CONFIG, DIA, "T")}

    entrada = marcadores[("entrada_eboe", "Entrada eBOE 1")]
    assert (entrada["texto"], entrada["color"]) == ("7,00 kg/MT", NEUTRO) and "Frutos en 10 kg: 50" in entrada["hover"]
    eboe1, eboe2 = marcadores[("eboe", "eBOE 1")], marcadores[("eboe", "eBOE 2")]
    assert (eboe1["texto"], eboe1["color"], eboe1["alertas"]) == ("ef. 90,0 % · sal. 0,70", VERDE, 0)
    assert (eboe2["color"], eboe2["alertas"], eboe2["destino"]) == (ROJO, 1, {"pagina": "analisis", "eboe": 2})
    tanque = marcadores[("tanque_emulsion", "Tanque de emulsión")]
    assert tanque["color"] == NEUTRO and "rango en revisión" in tanque["hover"]
    primaria = marcadores[("primaria", "ESE 230 (1)")]
    assert (primaria["texto"], primaria["color"]) == ("0,35 %", VERDE) and "15.000 L/h" in primaria["hover"]
    assert marcadores[("primaria", "ESE 230 (2)")]["color"] == SIN_DATOS          # parada: gris
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
