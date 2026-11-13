"""Pruebas de core/cartas_periodo.py: query params del Inicio, variables nuevas (eBOE, centrífugas y tanques), datos
inválidos y extremos, carta por turno con límites congelados o del propio período, pocos puntos e I-MR."""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from core.cartas_periodo import (AVISO_PERIODO, POR_MUESTRA, carta_periodo, equipos_de_variable,
                                 leer_parametros_control, parametros_control, serie_periodo)
from core.importar_centrifugas import REVISAR_COPIADA, REVISAR_EXTREMO
from core.limites import ENTRE_DENTRO, congelar_limites
from core.models import Equipo
from core.operacion import especie
from tests.test_carga_scott import sesion  # noqa: F401  (fixture)
from tests.test_inicio import DIA, _muestra, periodo, planta  # noqa: F401  (fixtures)

SIGUIENTE = date(2026, 7, 2)


def test_parametros_del_inicio_ida_y_vuelta():
    parametros = parametros_control("eficiencia", "N-BOE-02", DIA, SIGUIENTE, None)
    assert parametros == {"variable": "eficiencia", "equipo": "N-BOE-02", "desde": "2026-07-01",
                          "hasta": "2026-07-02", "turno": "todos"}
    assert leer_parametros_control(parametros) == {"variable": "eficiencia", "equipo": "N-BOE-02", "desde": DIA,
                                                   "hasta": SIGUIENTE, "turno": None, "planta": None}
    con_planta = parametros_control("eficiencia", "N-BOE-02", DIA, SIGUIENTE, "M", planta_id=8)
    assert (con_planta["planta"], leer_parametros_control(con_planta)["planta"]) == ("8", 8)
    tanque = leer_parametros_control(parametros_control("aceite_pesada", None, DIA, DIA, "N"))
    assert (tanque["equipo"], tanque["turno"]) == (None, "N")                  # primarias juntas, solo los N
    assert leer_parametros_control({}) is None                                  # la página se abrió sola
    for malos, error in (({"variable": "bomba"}, "no existe"), ({"variable": "eficiencia", "desde": "1/7"}, "AAAA-MM-DD"),
                         (parametros | {"desde": "2026-07-05"}, "anterior")):
        with pytest.raises(ValueError, match=error):
            leer_parametros_control(malos)


def test_equipos_de_cada_variable(sesion, planta):  # noqa: F811
    assert [c for c, _ in equipos_de_variable(sesion, planta.id, "aceite_entrada")] == ["N-BOE-01", "N-BOE-02",
                                                                                         "N-BOE-03"]
    assert [c for c, _ in equipos_de_variable(sesion, planta.id, "aceite_liviana")] == ["N-CEN-01", "N-CEN-02"]
    assert len(equipos_de_variable(sesion, planta.id, "aceite_descarga")) == 5            # primarias, secundarias, OSD
    assert equipos_de_variable(sesion, planta.id, "aceite_pesada") == [(None, "Primarias")]


def test_variables_nuevas(sesion, planta, periodo):  # noqa: F811
    for fase, otros in (("descarga", {"ml": 10.0, "t_descarga_s": 200.0}), ("fase_liviana", {"aceite_pct_medido": 70.0}),
                        ("fase_pesada", {"ml": 2.0})):                          # muestreo completo de 06:30 (balance)
        _muestra(sesion, "N-CEN-01", datetime(2026, 7, 1, 6, 30), fase, **otros)
    _muestra(sesion, "N-CEN-02", datetime(2026, 7, 2, 6, 30), "fase_pesada", ml=4.0, revisar=REVISAR_COPIADA)
    _muestra(sesion, "N-CSE-02", datetime(2026, 7, 1, 18, 30), "descarga", ml=600.0, revisar=REVISAR_EXTREMO)

    def serie(variable, equipo):
        return serie_periodo(sesion, planta, variable, equipo, DIA, SIGUIENTE)

    assert serie("aceite_entrada", "N-BOE-01")["valor"].round(2).tolist() == [7.0, 7.0, 7.0]
    assert serie("caudal_fp_lab", "N-BOE-01")["valor"].tolist() == [9.5, 9.5, 9.5]
    tanque = serie("aceite_emulsion_primaria", None)                            # las dos primarias juntas
    assert tanque["valor"].round(2).tolist() == [0.70, 0.84, 0.98, 1.40] and set(tanque["equipo"]) == {"Primarias"}
    assert serie("aceite_liviana", "N-CEN-01")["valor"].tolist() == [70.0]
    assert serie("caudal_liviana", "N-CEN-01")["valor"].iloc[0] > 0
    assert serie("caudal_pesada", "N-CEN-01")["valor"].iloc[0] > 0
    pesada = serie("aceite_pesada", None)                                       # la copiada queda inválida
    assert pesada["valor"].round(2).tolist() == [0.07, 0.14]
    assert pesada["invalido"].isna().tolist() == [True, False]
    assert pesada["invalido"].iloc[1] == "dato inválido: " + REVISAR_COPIADA
    descarga = serie("aceite_descarga", "N-CSE-02")                             # el extremo se marca, no se excluye
    assert descarga["extremo"].tolist() == [False, False, True] and descarga["invalido"].isna().all()
    carta = carta_periodo(sesion, descarga, "aceite_descarga", POR_MUESTRA)     # un solo turno: I-MR
    assert (carta["n_muestras"], carta["n_extremos"]) == (3, 1)
    assert serie_periodo(sesion, planta, "aceite_entrada", "N-BOE-01", DIA, SIGUIENTE, ["T"])["turno"].tolist() == ["T"]
    with pytest.raises(ValueError, match="no tiene el equipo"):
        serie("aceite_entrada", "X-BOE-09")


def test_sin_limites_congelados_y_pocos_puntos(sesion, planta, periodo):  # noqa: F811
    serie = serie_periodo(sesion, planta, "aceite_salida", "N-BOE-01", DIA, SIGUIENTE)
    carta = carta_periodo(sesion, serie, "aceite_salida")                       # turnos: 0,70, 2,10 y 1,40 kg/MT
    assert (carta["origen"], carta["tipo"], carta["n_turnos"]) == ("periodo", ENTRE_DENTRO, 3)
    assert carta["limites"]["lc"] == pytest.approx(1.40)
    assert carta["avisos"][0] == AVISO_PERIODO.capitalize() + "."
    assert any("Pocos puntos para una carta de control: 3 turnos" in a for a in carta["avisos"])
    imr = carta_periodo(sesion, serie, "aceite_salida", POR_MUESTRA)            # una muestra por turno
    assert (imr["tipo"], len(imr["tabla"])) == ("I-MR", 3) and imr["limites"]["lc"] == pytest.approx(1.40)
    assert imr["tabla"]["mr"].round(2).tolist()[1:] == [1.40, 0.70]
    un_turno = serie_periodo(sesion, planta, "aceite_salida", "N-BOE-01", DIA, DIA, ["M"])
    with pytest.raises(ValueError, match="Pocos puntos para una carta de control: 1 turno.*I-MR"):
        carta_periodo(sesion, un_turno, "aceite_salida")


def test_con_limites_congelados(sesion, planta, periodo):  # noqa: F811
    # la planta de prueba tiene límites congelados de eficiencia en los 3 eBOE: LC 85, LCS 91, LCI 79 (fase I 2025)
    carta = carta_periodo(sesion, serie_periodo(sesion, planta, "eficiencia", "N-BOE-01", DIA, SIGUIENTE),
                          "eficiencia")
    assert (carta["origen"], carta["limites"]["lc"], carta["limites"]["lci"]) == ("congelados", 85, 79)
    assert "fase I 01/06/2025–31/10/2025 (N 500)" == carta["limites"]["base"]
    assert AVISO_PERIODO.capitalize() + "." not in carta["avisos"]
    tabla = carta["tabla"].set_index("turno")
    assert 1 in tabla.loc["T", "reglas"]                                        # 70 % < LCI 79: regla 1
    assert carta["reglas"] == "1,2,3,5,S"                                       # alerta "1,2,3,rango" + marca "5,S"
    eboe1 = sesion.scalars(select(Equipo).where(Equipo.codigo == "N-BOE-01")).one()
    calculo = {"tipo_carta": ENTRE_DENTRO, "n_subgrupo": 1, "base_desde": date(2025, 6, 1),
               "base_hasta": date(2025, 9, 30), "n": 300, "lc": 1.0, "lcs": 2.0, "lci": 0.0, "sigma": 1 / 3,
               "sigma_dentro": None, "mr_lc": 0.4, "mr_lcs": 1.2, "excluidos": []}
    congelar_limites(sesion, "aceite_salida", calculo, "prueba", especie("N Val S/R"), equipo_id=eboe1.id,
                     sigma_alerta_inmediata=None)
    salida = carta_periodo(sesion, serie_periodo(sesion, planta, "aceite_salida", "N-BOE-01", DIA, SIGUIENTE),
                           "aceite_salida")
    assert (salida["origen"], salida["limites"]["lcs"]) == ("congelados", 2.0)   # 2,10 > 2,0: regla 1
    assert 1 in salida["tabla"].set_index("turno").loc["T", "reglas"]
