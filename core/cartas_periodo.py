"""Cartas de control de un período (Control › Cartas de control; el clic en el flowsheet del Inicio). Sin Streamlit.

- VARIABLES_PERIODO: las variables que se pueden graficar (las de los eBOE, las de las centrífugas y las de los
  tanques), con su fuente y, si la tiene, la variable de VARIABLES_CONTROL de los límites congelados. Las que no
  tienen variable de control (aceite en fruta de entrada, aceite en emulsión de alimentación, % y caudal de las
  fases, caudal de fase pesada del laboratorio) no tienen límites congelados ni generan alertas.
- serie_periodo(): un dato por fila (COLUMNAS_SERIE de core.limites, más "extremo") de un equipo, o de las primarias
  juntas (los tanques), en los días de producción desde–hasta y los turnos elegidos. Copiadas y demás datos
  inválidos (core.limites.motivo_invalido) quedan afuera de la carta en las variables de g y mL; los valores
  extremos se marcan, no se excluyen.
- carta_periodo(): carta por turno "entre/dentro" (la misma de las alertas) con los límites congelados vigentes; si
  no hay, límites calculados con el propio período (con aviso). O la I-MR por muestra, con límites del período.
- parametros_control() y leer_parametros_control(): los query params con los que el Inicio abre la página.
"""

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.alertas import REGLA_S, leer_reglas
from core.analisis_centrifugas import PRIMARIA, PULIDORA, SECUNDARIA, balance_por_muestreo, por_muestreo
from core.importar_centrifugas import REVISAR_EXTREMO
from core.gestion_alertas import _con_limites
from core.limites import (COLUMNAS_SERIE, ENTRE_DENTRO, TABLA_ANALISIS, TABLA_CENTRIFUGA, XBAR_S, ambito_de,
                          limites_vigentes, motivo_invalido, subgrupos_por_turno)
from core.models import Equipo, Linea, Planta
from core.operacion import especie
from core.spc import limites_entre_dentro, limites_imr, limites_xbar_s_para, nelson

POR_TURNO, POR_MUESTRA = "turno", "muestra"
EBOE, CENTRIFUGA, UNA_PRIMARIA, PRIMARIAS = "eboe", "centrifuga", "primaria", "primarias"
TODOS = "todos"
TURNOS_MINIMOS = 8               # menos turnos: "pocos puntos para una carta de control"
REGLAS_PERIODO = "1,2,3,5,S"     # límites del propio período: las reglas que se marcan (no alertan)
AVISO_PERIODO = "límites del propio período (no hay límites de referencia congelados)"
AVISO_POCOS = "pocos puntos para una carta de control"
TABLA_BALANCE = "balance"

# Variable → nombre, unidad, decimales, fuente (EBOE: un eBOE; CENTRIFUGA: una centrífuga; UNA_PRIMARIA: una
# primaria; PRIMARIAS: las primarias juntas, como en los tanques), control (clave de VARIABLES_CONTROL de los
# límites congelados, o None) y titulacion (sale de g y mL: la copia y los demás motivos de dato inválido la afectan).
VARIABLES_PERIODO = {
    "aceite_entrada": {"nombre": "Aceite en fruta de entrada", "unidad": "kg/MT", "decimales": 2, "fuente": EBOE,
                       "control": None, "titulacion": True},
    "eficiencia": {"nombre": "Eficiencia eBOE", "unidad": "%", "decimales": 1, "fuente": EBOE,
                   "control": "eficiencia", "titulacion": True},
    "aceite_salida": {"nombre": "Aceite en fruta de salida", "unidad": "kg/MT", "decimales": 2, "fuente": EBOE,
                      "control": "aceite_salida", "titulacion": True},
    "aceite_emulsion": {"nombre": "Aceite en emulsión del eBOE", "unidad": "%", "decimales": 2, "fuente": EBOE,
                        "control": "aceite_emulsion", "titulacion": True},
    "caudal_fp_lab": {"nombre": "Caudal de fase pesada (laboratorio del BOE)", "unidad": "m³/h", "decimales": 1,
                      "fuente": EBOE, "control": None, "titulacion": False},
    "aceite_emulsion_primaria": {"nombre": "Aceite en emulsión de alimentación a la primaria", "unidad": "%",
                                 "decimales": 2, "fuente": PRIMARIAS, "control": None, "titulacion": True},
    "aceite_descarga": {"nombre": "Aceite en descarga", "unidad": "%", "decimales": 2, "fuente": CENTRIFUGA,
                        "control": "aceite_descarga", "titulacion": True},
    "aceite_liviana": {"nombre": "Aceite en fase liviana", "unidad": "%", "decimales": 1, "fuente": UNA_PRIMARIA,
                       "control": None, "titulacion": True},
    "caudal_liviana": {"nombre": "Caudal de fase liviana (balance)", "unidad": "L/h", "decimales": 0,
                       "fuente": UNA_PRIMARIA, "control": None, "titulacion": False},
    "caudal_pesada": {"nombre": "Caudal de fase pesada (balance)", "unidad": "L/h", "decimales": 0,
                      "fuente": UNA_PRIMARIA, "control": None, "titulacion": False},
    "aceite_pesada": {"nombre": "Aceite en fase pesada", "unidad": "%", "decimales": 3, "fuente": PRIMARIAS,
                      "control": None, "titulacion": True},
}
ALTERNATIVAS = {"aceite_liviana": "caudal_liviana", "caudal_liviana": "aceite_liviana"}   # selector en la carta
FASE = {"aceite_emulsion_primaria": "emulsion", "aceite_descarga": "descarga", "aceite_liviana": "fase_liviana",
        "aceite_pesada": "fase_pesada"}
TIPOS_CENTRIFUGA = (PRIMARIA, SECUNDARIA, PULIDORA)


# ---------------------------------------------------------------- query params del Inicio

def parametros_control(variable: str, equipo: str | None, desde: date, hasta: date, turno: str | None,
                       planta_id: int | None = None) -> dict:
    """Query params para abrir Control › Cartas de control: variable, equipo (código; "primarias" para las primarias
    juntas), desde y hasta (AAAA-MM-DD), turno ("todos" o el nombre) y, si se pasa, planta (id)."""
    if variable not in VARIABLES_PERIODO:
        raise ValueError(f'Variable "{variable}" no válida para la carta.')
    parametros = {"variable": variable, "equipo": equipo or PRIMARIAS, "desde": desde.isoformat(),
                  "hasta": hasta.isoformat(), "turno": turno or TODOS}
    return parametros | ({"planta": str(planta_id)} if planta_id is not None else {})


def leer_parametros_control(parametros) -> dict | None:
    """Lee los query params de parametros_control(): {"variable", "equipo" (código, o None = primarias juntas),
    "desde", "hasta" (fechas), "turno" (None = todos), "planta" (id o None)}. None si no vienen (la página se abrió
    sola). Error claro si están mal."""
    parametros = dict(parametros)
    if not parametros.get("variable"):
        return None
    variable = parametros["variable"]
    if variable not in VARIABLES_PERIODO:
        raise ValueError(f'La variable "{variable}" no existe en las cartas de control.')
    try:
        desde, hasta = date.fromisoformat(parametros["desde"]), date.fromisoformat(parametros["hasta"])
    except (KeyError, ValueError):
        raise ValueError("Las fechas del período tienen que venir como AAAA-MM-DD (desde y hasta).") from None
    if hasta < desde:
        raise ValueError("La fecha hasta no puede ser anterior a la fecha desde.")
    equipo = parametros.get("equipo") or PRIMARIAS
    turno = parametros.get("turno") or TODOS
    planta = parametros.get("planta")
    if planta is not None and not str(planta).isdigit():
        raise ValueError(f'La planta tiene que ser un número (llegó "{planta}").')
    return {"variable": variable, "equipo": None if equipo == PRIMARIAS else equipo, "desde": desde, "hasta": hasta,
            "turno": None if turno == TODOS else turno, "planta": None if planta is None else int(planta)}


# ---------------------------------------------------------------- equipos y serie

def equipos_de_variable(sesion, planta_id: int, variable: str) -> list[tuple[str | None, str]]:
    """Equipos que se pueden elegir para la variable: [(código, nombre)]; [(None, "Primarias")] si es de las primarias
    juntas."""
    fuente = VARIABLES_PERIODO[variable]["fuente"]
    if fuente == PRIMARIAS:
        return [(None, "Primarias")]
    tipos = {EBOE: ("BOE",), CENTRIFUGA: TIPOS_CENTRIFUGA, UNA_PRIMARIA: (PRIMARIA,)}[fuente]
    equipos = sesion.scalars(select(Equipo).join(Linea).where(Linea.planta_id == planta_id, Equipo.tipo.in_(tipos))
                             .order_by(Equipo.codigo)).all()
    return [(e.codigo, e.nombre) for e in equipos]


def serie_periodo(sesion, planta: Planta, variable: str, equipo: str | None, desde: date, hasta: date,
                  turnos: list[str] | None = None) -> pd.DataFrame:
    """Serie de la variable en los días de producción desde–hasta (turnos: solo esos; None o vacío, todos), de un
    equipo (código) o, si la variable es de las primarias juntas, de todas las primarias (ámbito planta).

    Columnas: COLUMNAS_SERIE de core.limites (invalido: "dato inválido: …" en las variables de g y mL; los caudales y
    el laboratorio del BOE nunca se invalidan) y extremo (la muestra tiene la marca de valor extremo: se marca, no se
    excluye). Fuentes: análisis Scott (core.analisis), muestras de centrífugas y su balance (core.analisis_centrifugas).
    """
    from core.inicio import datos_periodo     # acá, para no importar core.inicio al cargar el módulo
    if variable not in VARIABLES_PERIODO:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_PERIODO)}.')
    definicion = VARIABLES_PERIODO[variable]
    fuente = definicion["fuente"]
    objeto = None
    if fuente != PRIMARIAS:
        objeto = sesion.scalars(select(Equipo).join(Linea).where(Linea.planta_id == planta.id,
                                                                  Equipo.codigo == equipo)).first()
        if objeto is None:
            raise ValueError(f'La planta no tiene el equipo "{equipo}".')
    datos = datos_periodo(sesion, planta, desde, hasta)
    if fuente == EBOE:
        tabla = _de_eboe(datos, variable, objeto)
    else:
        tabla = _de_centrifugas(datos["muestras"], variable, objeto, planta.id)
    if turnos:
        tabla = tabla[tabla["turno"].isin(turnos)]
    return tabla.reset_index(drop=True)


def _armar(datos: pd.DataFrame, tabla: str, ids, valor, equipo: Equipo | None, planta_id: int, invalido,
           extremo) -> pd.DataFrame:
    """La serie con COLUMNAS_SERIE + extremo, en orden de tiempo. Ámbito: el equipo, o la planta (primarias)."""
    serie = pd.DataFrame({
        "tabla": tabla, "dato_id": list(ids), "equipo_id": equipo.id if equipo else None,
        "planta_id": None if equipo else planta_id, "equipo": equipo.nombre if equipo else "Primarias",
        "tipo": equipo.tipo if equipo else PRIMARIA, "fecha_hora": datos["fecha_hora"],
        "fecha_produccion": datos["fecha_produccion"], "turno": datos["turno"], "temporada": datos["temporada"],
        "fruta": datos["fruta"].map(especie) if "fruta" in datos else None, "valor": valor, "invalido": invalido,
        "extremo": extremo}, index=datos.index)
    serie = serie.dropna(subset=["valor"]).astype({"valor": float, "extremo": bool})
    serie["ambito"] = ambito_de(equipo.id if equipo else None, None if equipo else planta_id)
    return serie[COLUMNAS_SERIE + ["extremo"]].sort_values("fecha_hora", ignore_index=True)


def _de_eboe(datos: dict, variable: str, equipo: Equipo) -> pd.DataFrame:
    analisis = datos["analisis"][datos["analisis"]["eboe"] == equipo.numero]
    entradas = analisis[analisis["lugar"] == "Entrada"]
    if variable == "aceite_emulsion":
        fuente = datos["emulsion"][datos["emulsion"]["eboe"] == equipo.numero]
        frutas = entradas.set_index("fecha_hora")["fruta"]
        fuente = fuente.assign(fruta=fuente["fecha_hora"].map(lambda f: frutas.get(f)), valor=fuente["aceite_emulsion"])
    elif variable == "aceite_salida":
        fuente = analisis[analisis["lugar"] == "Salida"].assign(valor=lambda d: d["aceite_fruta"])
    else:
        columna = {"aceite_entrada": entradas["aceite_fruta"], "eficiencia": entradas["eficiencia_par"] * 100,
                   "caudal_fp_lab": entradas["caudal_fase_pesada"]}[variable]
        fuente = entradas.assign(valor=columna)
    fuente = fuente.assign(valor=pd.to_numeric(fuente["valor"], errors="coerce")).dropna(subset=["valor"])
    return _armar(fuente, TABLA_ANALISIS, fuente["analisis_id"], fuente["valor"], equipo, None, None, False)


def _de_centrifugas(muestras: pd.DataFrame, variable: str, equipo: Equipo | None, planta_id: int) -> pd.DataFrame:
    del_equipo = muestras[muestras["codigo"] == equipo.codigo] if equipo else muestras[muestras["tipo"] == PRIMARIA]
    if variable in ("caudal_liviana", "caudal_pesada"):
        balance = balance_por_muestreo(por_muestreo(del_equipo)) if not del_equipo.empty else pd.DataFrame(
            columns=["fecha_hora", "fecha_produccion", "turno", "temporada", variable])
        balance = balance.assign(valor=pd.to_numeric(balance[variable], errors="coerce")).dropna(subset=["valor"])
        return _armar(balance, TABLA_BALANCE, range(len(balance)), balance["valor"], equipo, planta_id, None, False)
    fase = del_equipo[del_equipo["fase"] == FASE[variable]]
    fase = fase.assign(valor=pd.to_numeric(fase["aceite_pct"], errors="coerce")).dropna(subset=["valor"])
    titulacion = VARIABLES_PERIODO[variable]["titulacion"]
    revisar = fase["revisar"].fillna("")
    return _armar(fase, TABLA_CENTRIFUGA, fase["id"], fase["valor"], equipo, planta_id,
                  revisar.map(lambda r: motivo_invalido(r, titulacion)),
                  revisar.str.contains(REVISAR_EXTREMO, regex=False))


# ---------------------------------------------------------------- carta

def carta_periodo(sesion, serie: pd.DataFrame, variable: str, por: str = POR_TURNO) -> dict:
    """Carta de control de la serie de un período (serie_periodo).

    POR_TURNO (por defecto): carta "entre/dentro" de las alertas (I-MR-S de Minitab): un punto por turno (promedio de
    sus muestras válidas) con LC, LCS y LCI de la variación ENTRE turnos y carta S con la de DENTRO. Límites: los
    congelados vigentes de la variable, el equipo y la fruta (core.limites.limites_vigentes) si la variable tiene
    límites de control (VARIABLES_PERIODO[...]["control"]) y existen; reglas: las de alerta y de marca de esos
    límites. Si no: límites con los datos del propio período (core.spc.limites_entre_dentro), reglas
    REGLAS_PERIODO y el aviso AVISO_PERIODO. POR_MUESTRA: I-MR por muestra con límites del propio período
    (core.spc.limites_imr) y las reglas 1, 2, 3 y 5.
    Los datos inválidos no entran; los extremos sí (se cuentan). Con menos de TURNOS_MINIMOS turnos, AVISO_POCOS.
    Devuelve titulo, unidad, decimales, por, tipo, origen ("congelados" o "periodo"), limites (lc, lcs, lci, sigma,
    sigma_dentro, base), reglas (texto), n_turnos, n_muestras, n_invalidos, n_extremos, avisos y tabla: por turno,
    fecha_produccion, turno, fecha_hora, n, media, desvio, lc, lcs, lci, s_lc, s_lcs, s_lci, reglas (lista de las
    violadas) y fuera_s; por muestra, fecha_hora, fecha_produccion, turno, valor, extremo, mr, lc, lcs, lci, mr_lcs,
    reglas y fuera_mr.
    """
    if por not in (POR_TURNO, POR_MUESTRA):
        raise ValueError(f'Tipo de carta "{por}" no válido: "{POR_TURNO}" o "{POR_MUESTRA}".')
    definicion = VARIABLES_PERIODO[variable]
    validos = serie[serie["invalido"].isna()]
    if validos.empty:
        raise ValueError("No hay datos válidos de esta variable en el período.")
    subgrupos = subgrupos_por_turno(serie)
    avisos = []
    if len(subgrupos) < TURNOS_MINIMOS:
        avisos.append(f"{AVISO_POCOS.capitalize()}: {len(subgrupos)} turno{'' if len(subgrupos) == 1 else 's'} "
                      f"(se recomiendan al menos {TURNOS_MINIMOS}).")
    resultado = {"titulo": f"{definicion['nombre']} · {validos['equipo'].iloc[0]}", "unidad": definicion["unidad"],
                 "decimales": definicion["decimales"], "por": por, "n_turnos": len(subgrupos),
                 "n_muestras": len(validos), "n_invalidos": int(serie["invalido"].notna().sum()),
                 "n_extremos": int(serie["extremo"].sum()), "avisos": avisos}
    if por == POR_MUESTRA:
        return resultado | _carta_imr(validos, avisos)
    limites = _congelados(sesion, validos, definicion["control"])
    if limites is None:
        if len(subgrupos) < 2:
            raise ValueError(f"{AVISO_POCOS.capitalize()}: 1 turno. Sin límites congelados, hacen falta al menos 2 "
                             "turnos para calcular límites con el propio período; probá la I-MR por muestra.")
        calculo = limites_entre_dentro(subgrupos["media"], subgrupos["desvio"].astype(float), subgrupos["n"])
        limites = SimpleNamespace(tipo_carta=ENTRE_DENTRO, reglas_alerta="", reglas_marca=REGLAS_PERIODO,
                                  **{k: calculo[k] for k in ("lc", "lcs", "lci", "sigma", "sigma_dentro")})
        origen, base = "periodo", f"{len(subgrupos)} turnos del período"
        avisos.insert(0, AVISO_PERIODO.capitalize() + ".")
    else:
        origen = "congelados"
        base = f"fase I {limites.base_desde:%d/%m/%Y}–{limites.base_hasta:%d/%m/%Y} (N {limites.n})"
    tabla = _con_limites(subgrupos, limites)
    numeros_a, otras_a = leer_reglas(limites.reglas_alerta)
    numeros_m, otras_m = leer_reglas(limites.reglas_marca)
    numeros, otras = sorted(set(numeros_a) | set(numeros_m)), otras_a | otras_m
    n = tabla["n"].to_numpy(dtype=int)
    sigma_punto = limites.sigma / np.sqrt(n) if limites.tipo_carta == XBAR_S else np.full(len(tabla), limites.sigma)
    tabla["reglas"] = (nelson(tabla["media"], limites.lc, sigma_punto, numeros) if numeros and limites.sigma > 0
                       else [[] for _ in range(len(tabla))])
    desvio = tabla["desvio"].astype(float)
    tabla["fuera_s"] = (REGLA_S in otras) & ((desvio > tabla["s_lcs"].astype(float))
                                             | ((tabla["s_lci"].astype(float) > 0) & (desvio < tabla["s_lci"].astype(float))))
    reglas = ",".join(map(str, numeros)) + (",S" if REGLA_S in otras else "")
    return resultado | {"tipo": limites.tipo_carta, "origen": origen, "reglas": reglas, "tabla": tabla,
                        "limites": {"lc": limites.lc, "lcs": limites.lcs, "lci": limites.lci, "sigma": limites.sigma,
                                    "sigma_dentro": limites.sigma_dentro, "base": base}}


def _congelados(sesion, validos: pd.DataFrame, control: str | None):
    """Límites congelados vigentes de la variable de control, el equipo y la fruta más frecuente (por turno)."""
    if control is None or pd.isna(validos["equipo_id"].iloc[0]):
        return None
    frutas = validos["fruta"].dropna()
    limites = limites_vigentes(sesion, control, frutas.mode().iloc[0] if not frutas.empty else None,
                               int(validos["equipo_id"].iloc[0]))
    return limites if limites is not None and limites.tipo_carta in (ENTRE_DENTRO, XBAR_S) else None


def _carta_imr(validos: pd.DataFrame, avisos: list) -> dict:
    """I-MR por muestra con límites del propio período y las reglas 1, 2, 3 y 5."""
    if len(validos) < 2:
        raise ValueError("Hacen falta al menos 2 muestras para la carta I-MR.")
    tabla = validos[["fecha_hora", "fecha_produccion", "turno", "valor", "extremo"]].sort_values(
        "fecha_hora", ignore_index=True)
    calculo = limites_imr(tabla["valor"])
    tabla["mr"] = tabla["valor"].diff().abs()
    tabla = tabla.assign(lc=calculo["lc"], lcs=calculo["lcs"], lci=calculo["lci"], mr_lcs=calculo["mr_lcs"])
    tabla["reglas"] = (nelson(tabla["valor"], calculo["lc"], calculo["sigma"], [1, 2, 3, 5]) if calculo["sigma"] > 0
                       else [[] for _ in range(len(tabla))])
    tabla["fuera_mr"] = tabla["mr"] > calculo["mr_lcs"]
    avisos.insert(0, "I-MR por muestra: límites del propio período (los congelados son por turno).")
    return {"tipo": "I-MR", "origen": "periodo", "reglas": "1,2,3,5,MR", "tabla": tabla,
            "limites": {"lc": calculo["lc"], "lcs": calculo["lcs"], "lci": calculo["lci"], "sigma": calculo["sigma"],
                        "sigma_dentro": None, "base": f"{len(tabla)} muestras del período"}}
