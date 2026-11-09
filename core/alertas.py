"""Alertas de control (fase II), por TURNO y agrupadas en EPISODIOS.

Un punto por turno de producción y ámbito (equipo, o la planta en el pH): el promedio de sus muestras (subgrupo de
n variable, core.limites.subgrupos_por_turno).
- violaciones_por_turno(): reglas de Nelson en la carta de los promedios y "S" (desvío del turno fuera de la carta
  S). Con límites "I-MR-S" (entre/dentro) los límites del promedio salen de la variación entre turnos y no dependen
  del n; con "Xbar-S", de la variación dentro del turno (x̿ ± 3σ/√n). Los turnos con 1 muestra se evalúan en el
  promedio pero no en S. Mira los turnos anteriores que necesita cada regla (hasta 14, solo de la fase II: después
  del período base, como en las cartas). Cada violación sale marcada con alerta = sí o no: solo alertan las
  reglas_alerta de los límites y, en las reglas 1, 2, 3, 5 y 6, del lado malo de la variable (lado_alerta de
  VARIABLES_CONTROL); las reglas_marca y las del lado bueno solo se marcan en la carta. En la regla 1 marca
  además "inmediata" si el promedio pasa los ±LimitesControl.sigma_alerta_inmediata σ (vacío: nunca).
- violaciones_rango(): el promedio del turno fuera del rango de RangoReferencia con genera_alerta = sí, respetando
  lado_capacidad (eficiencia: solo por debajo del mínimo).
- evaluar_nuevos(): crea las alertas ("abierta", o "histórico" si se evalúan datos importados), agrupadas en
  episodios. Persistencia: un episodio se crea recién cuando la misma regla (origen + regla + variable + ámbito) se
  viola en Planta.turnos_para_alertar turnos seguidos del ámbito; empieza en el primero de esos turnos. Excepción:
  la regla 1 más allá de ±sigma_alerta_inmediata σ de sus límites (del lado malo) alerta en el acto (inmediata). Los turnos que
  vuelven a violar la regla se suman al episodio en curso (turnos_afectados, fin). El episodio se cierra solo tras
  Planta.turnos_fin_episodio turnos seguidos sin violarla; si después vuelve a violarse, es otro. Una alerta cerrada
  a mano no se reabre. Sin límites vigentes por turno, no evalúa Nelson pero sí el rango; con límites, el rango
  alerta solo si "rango" está en sus reglas_alerta (el pH: solo "rango"). Los datos inválidos no entran.
- separar_comunes(): si la misma regla y variable salta en TODOS los eBOE de la planta en el mismo turno, se crea
  una sola alerta de ámbito planta (comun_eboe y un comentario automático: los eBOE comparten la muestra de entrada)
  en lugar de una por equipo. Las de equipo quedan solo para los turnos que no son comunes. Se detecta entre los
  datos que se evalúan juntos (por eso conviene evaluar los eBOE de un turno en la misma llamada).
- alertas_de(): las alertas de un análisis o muestra (para mostrarlas y revisarlas si se corrige un dato).
"""

from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.limites import (ENTRE_DENTRO, TIPO_EBOE, VARIABLES_CONTROL, XBAR_S, ambito_de, limites_vigentes,
                          subgrupos_por_turno)
from core.models import Alerta, AlertaDato, Equipo, Linea, Planta, RangoReferencia
from core.parametros import validar_parametros
from core.spc import REGLAS, limites_xbar_s_para, nelson

REGLA_MR, REGLA_S, REGLA_RANGO = "MR", "S", "rango"
ORIGEN_NELSON, ORIGEN_RANGO = "nelson", "rango"
ABIERTA, HISTORICO, CERRADA = "abierta", "histórico", "cerrada"
TURNOS_ANTERIORES = 14            # la regla 4 mira 14 puntos atrás; las demás, menos
POR_TURNO = (ENTRE_DENTRO, XBAR_S)
COMENTARIO_COMUN = "posible causa común: muestra de entrada, laboratorio o fruta"
LADOS_ALERTA = ("abajo", "arriba", "ambos")
REGLAS_CON_LADO = (1, 2, 3, 5, 6)   # 1, 2, 5, 6: lado del punto respecto de la LC; 3: sentido de la tendencia


def leer_reglas(texto: str | None) -> tuple[list[int], set[str]]:
    """"1,2,3,5,S" → ([1, 2, 3, 5], {"S"}). Además de los números de Nelson: "S" (carta S), "MR" (carta MR) y
    "rango" (fuera del rango de referencia)."""
    numeros, otras = [], set()
    for parte in (p.strip() for p in (texto or "").split(",") if p.strip()):
        if parte.upper() in (REGLA_MR, REGLA_S):
            otras.add(parte.upper())
        elif parte.lower() == REGLA_RANGO:
            otras.add(REGLA_RANGO)
        elif parte.isdigit() and int(parte) in REGLAS:
            numeros.append(int(parte))
        else:
            raise ValueError(f'Regla "{parte}" no válida: usá números de Nelson del 1 al 8, "S", "MR" y "rango".')
    return sorted(set(numeros)), otras


# ---------------------------------------------------------------- violaciones (funciones puras)

def violaciones_por_turno(nuevos: pd.DataFrame, previos: pd.DataFrame | None, lc: float, sigma: float,
                          reglas_alerta: str, tipo_carta: str = ENTRE_DENTRO, sigma_dentro: float | None = None,
                          reglas_marca: str | None = None, lado: str = "ambos",
                          sigma_inmediata: float | None = None) -> list[dict]:
    """Reglas violadas por los turnos nuevos (subgrupos de un ámbito, con n, media y desvio).

    Carta de los promedios: Nelson con LC = lc y σ de cada punto = sigma ("I-MR-S": σ entre turnos, igual para todo
    n) o sigma/√n ("Xbar-S"). Carta S (si "S" está en las reglas y n ≥ 2): desvío del turno > LCS de S o < LCI de S
    para su n, con σ dentro (sigma_dentro en "I-MR-S"; sigma en "Xbar-S").
    reglas_alerta: las que alertan; reglas_marca: se evalúan y se devuelven, pero no alertan. lado: el lado malo de
    la variable ("abajo", "arriba", "ambos"); en las reglas 1, 2, 5 y 6 cuenta el lado del punto respecto de la LC y
    en la 3 el sentido de la tendencia: del lado bueno, la violación se marca pero no alerta. sigma_inmediata: en la
    regla 1, inmediata = |promedio − lc| > sigma_inmediata × σ del punto (None: nunca).
    previos: turnos anteriores del mismo ámbito en la fase II (se usan los últimos TURNOS_ANTERIORES).
    Devuelve [{indice (de nuevos), regla, valor, limite, lado ("arriba"/"abajo", o None si la regla no tiene lado),
    alerta (bool), inmediata (bool)}]: regla 1 → LCS o LCI; 5 → lc ± 2σ; 2, 4, 6, 7, 8 → lc; 3 → sin límite; "S" → el límite de S que
    cruzó (valor = desvío del turno).
    """
    if tipo_carta not in POR_TURNO:
        raise ValueError(f'Las alertas por turno usan límites "{ENTRE_DENTRO}" o "{XBAR_S}" (llegó "{tipo_carta}").')
    if lado not in LADOS_ALERTA:
        raise ValueError(f'Lado de alerta "{lado}" no válido. Usá uno de: {", ".join(LADOS_ALERTA)}.')
    numeros_alerta, otras_alerta = leer_reglas(reglas_alerta)
    numeros_marca, otras_marca = leer_reglas(reglas_marca)
    numeros, otras = sorted(set(numeros_alerta) | set(numeros_marca)), otras_alerta | otras_marca
    anteriores = previos.sort_values("fecha_hora").tail(TURNOS_ANTERIORES) if previos is not None else nuevos.iloc[0:0]
    serie = pd.concat([anteriores, nuevos.sort_values("fecha_hora")])
    desde = len(anteriores)
    medias = serie["media"].to_numpy(dtype=float)
    n = serie["n"].to_numpy(dtype=int)
    sigma_punto = sigma / np.sqrt(n) if tipo_carta == XBAR_S else np.full(len(serie), float(sigma))
    sigma_s = sigma if tipo_carta == XBAR_S else sigma_dentro
    violadas_por_punto = nelson(medias, lc, sigma_punto, numeros) if numeros else [[]] * len(serie)
    resultado = []
    for posicion in range(desde, len(serie)):
        indice, media, s_punto = serie.index[posicion], medias[posicion], sigma_punto[posicion]
        for regla in violadas_por_punto[posicion]:
            limite = {1: lc + 3 * s_punto if media > lc else lc - 3 * s_punto,
                      5: lc + 2 * s_punto if media > lc else lc - 2 * s_punto, 3: None}.get(regla, lc)
            if regla == 3:
                lado_violacion = "arriba" if medias[posicion] > medias[posicion - 1] else "abajo"
            else:
                lado_violacion = ("arriba" if media > lc else "abajo") if regla in REGLAS_CON_LADO else None
            alerta = regla in numeros_alerta and (lado == "ambos" or lado_violacion in (None, lado))
            inmediata = regla == 1 and sigma_inmediata is not None and abs(media - lc) > sigma_inmediata * s_punto
            resultado.append({"indice": indice, "regla": str(regla), "valor": media, "limite": limite,
                              "lado": lado_violacion, "alerta": alerta, "inmediata": bool(inmediata)})
        desvio = serie["desvio"].iloc[posicion]
        if REGLA_S in otras and sigma_s and n[posicion] >= 2 and desvio is not None and not pd.isna(desvio):
            limites = limites_xbar_s_para(lc, sigma_s, int(n[posicion]))
            limite = limites["s_lcs"] if desvio > limites["s_lcs"] else (
                limites["s_lci"] if limites["s_lci"] > 0 and desvio < limites["s_lci"] else None)
            if limite is not None:
                resultado.append({"indice": indice, "regla": REGLA_S, "valor": float(desvio), "limite": limite,
                                  "lado": None, "alerta": REGLA_S in otras_alerta, "inmediata": False})
    return resultado


def violaciones_rango(subgrupos: pd.DataFrame, minimo: float | None, maximo: float | None,
                      lado: str | None = "ambos") -> list[dict]:
    """Turnos con el promedio fuera del rango [mínimo, máximo]. lado "inferior": solo por debajo del mínimo;
    "superior": solo por encima del máximo. Devuelve [{indice, regla: "rango", valor, limite}]."""
    lado = lado or "ambos"
    resultado = []
    for indice, media in subgrupos["media"].items():
        if lado != "superior" and minimo is not None and media < minimo:
            resultado.append({"indice": indice, "regla": REGLA_RANGO, "valor": media, "limite": minimo})
        elif lado != "inferior" and maximo is not None and media > maximo:
            resultado.append({"indice": indice, "regla": REGLA_RANGO, "valor": media, "limite": maximo})
    return resultado


def turnos_entre(tiempos, desde, hasta) -> int:
    """Cuántos turnos de la línea de tiempo (fechas de la primera muestra, ordenadas) hay estrictamente entre dos
    momentos."""
    tiempos = np.asarray(tiempos, dtype="datetime64[ns]")
    return int(np.sum((tiempos > np.datetime64(desde)) & (tiempos < np.datetime64(hasta))))


# ---------------------------------------------------------------- con la base

def rango_de_alerta(sesion: Session, planta_id: int, variable: str, tipo_equipo: str,
                    fruta: str | None) -> RangoReferencia | None:
    """Rango de RangoReferencia que genera alertas para la variable (genera_alerta = sí). El punto o equipo es el
    de VARIABLES_CONTROL o, si no tiene, el tipo de equipo. Si hay uno de la fruta y uno general, gana el de la
    fruta."""
    definicion = VARIABLES_CONTROL[variable]["rango"]
    if definicion is None:
        return None
    nombre, punto = definicion
    rangos = sesion.scalars(select(RangoReferencia).where(
        RangoReferencia.planta_id == planta_id, RangoReferencia.variable == nombre,
        RangoReferencia.punto_o_equipo == (punto or tipo_equipo))).all()
    rangos = [r for r in rangos if r.genera_alerta is not False]
    de_la_fruta = [r for r in rangos if fruta and r.fruta and r.fruta.lower() == fruta.lower()]
    generales = [r for r in rangos if r.fruta is None]
    candidatos = de_la_fruta or generales
    return candidatos[0] if candidatos else None


def evaluar_nuevos(sesion: Session, planta: Planta, variable: str, nuevos: pd.DataFrame,
                   previos: pd.DataFrame | None = None, historico: bool = False,
                   creado_por: str = "evaluar_nuevos", comunes: bool = True) -> dict:
    """Evalúa datos nuevos de una variable (COLUMNAS_SERIE de core.limites, uno o varios ámbitos) por turno y crea o
    extiende alertas (episodios).

    previos: datos anteriores (misma variable) para las reglas que miran hacia atrás, para la persistencia (los
    últimos turnos_para_alertar − 1 turnos de cada ámbito se vuelven a mirar) y para contar los turnos sin alerta de
    los episodios. historico=True: las alertas nuevas son "histórico" (datos importados). Usa los parámetros de la
    planta turnos_fin_episodio y turnos_para_alertar, y el sigma_alerta_inmediata de cada límite. Solo crean alertas las violaciones con
    alerta = sí (reglas_alerta del lado malo) y los rangos con genera_alerta. comunes=True: las violaciones de la
    misma regla en todos los eBOE en el mismo turno van a una alerta de planta (separar_comunes).
    Devuelve {"creadas" (episodios nuevos), "inmediatas" (de esos, cuántos sin esperar la persistencia), "turnos"
    (turnos sumados a alertas), "por_regla": Counter de episodios nuevos, "sin_limites": [ámbitos sin límites por
    turno vigentes]}.
    """
    if variable not in VARIABLES_CONTROL:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_CONTROL)}.')
    parametros = validar_parametros(planta, ["turnos_fin_episodio", "turnos_para_alertar"])
    turnos = {t.nombre: t.id for t in planta.turnos}
    resumen = {"creadas": 0, "inmediatas": 0, "turnos": 0, "por_regla": Counter(), "sin_limites": []}
    previos_validos = previos[previos["invalido"].isna()] if previos is not None and not previos.empty else None
    lado = VARIABLES_CONTROL[variable]["lado_alerta"]
    evaluados = []
    for ambito, grupo in subgrupos_por_turno(nuevos).groupby("ambito", sort=False):
        grupo = grupo.sort_values("fecha_hora")
        equipo_id = None if pd.isna(grupo["equipo_id"].iloc[0]) else int(grupo["equipo_id"].iloc[0])
        planta_id = None if equipo_id is not None else int(grupo["planta_id"].iloc[0])
        frutas = grupo["fruta"].dropna()
        fruta = frutas.mode().iloc[0] if not frutas.empty else None
        anteriores = subgrupos_por_turno(previos_validos[previos_validos["ambito"] == ambito]) \
            if previos_validos is not None else grupo.iloc[0:0]
        anteriores = anteriores[anteriores["fecha_hora"] < grupo["fecha_hora"].min()]
        contexto = anteriores.tail(parametros["turnos_para_alertar"] - 1)    # para ver la persistencia
        evaluar = pd.concat([contexto, grupo], ignore_index=True)
        violaciones, rango_alerta = [], True
        limites = limites_vigentes(sesion, variable, fruta, equipo_id, planta_id)
        if limites is not None and limites.tipo_carta in POR_TURNO:
            base_hasta = pd.Timestamp(limites.base_hasta)
            fase2 = anteriores[anteriores["fecha_produccion"] > base_hasta]
            contexto_fase2 = int((contexto["fecha_produccion"] > base_hasta).sum())
            a_evaluar = evaluar[(evaluar.index >= len(contexto)) | (evaluar["fecha_produccion"] > base_hasta)]
            violaciones += [v | {"origen": ORIGEN_NELSON, "limites_id": limites.id} for v in violaciones_por_turno(
                a_evaluar, fase2.iloc[:len(fase2) - contexto_fase2], limites.lc, limites.sigma, limites.reglas_alerta,
                limites.tipo_carta, limites.sigma_dentro, limites.reglas_marca, lado,
                limites.sigma_alerta_inmediata) if v["alerta"]]
            rango_alerta = REGLA_RANGO in leer_reglas(limites.reglas_alerta)[1]
        else:
            resumen["sin_limites"].append(ambito)
        rango = rango_de_alerta(sesion, planta.id, variable, grupo["tipo"].iloc[0], fruta) if rango_alerta else None
        if rango is not None:
            violaciones += [v | {"origen": ORIGEN_RANGO, "rango_id": rango.id} for v in violaciones_rango(
                evaluar, rango.minimo, rango.maximo, rango.lado_capacidad)]
        evaluados.append({"ambito": ambito, "equipo_id": equipo_id, "planta_id": planta_id, "subgrupos": evaluar,
                          "violaciones": violaciones,
                          "linea": pd.concat([anteriores, grupo])[["fecha_produccion", "turno", "fecha_hora"]]})
    for e in evaluados:                     # antes de separar las comunes: cuentan para la persistencia del equipo
        e["violados"] = _violados(e)
    if comunes and VARIABLES_CONTROL[variable]["nivel"] == "equipo" and not nuevos.empty \
            and (nuevos["tipo"] == TIPO_EBOE).all():
        eboes = sesion.scalars(select(Equipo.id).join(Linea).where(Linea.planta_id == planta.id,
                                                                    Equipo.tipo == TIPO_EBOE)).all()
        comun = separar_comunes(evaluados, [ambito_de(equipo_id=e) for e in eboes], planta.id)
        if comun is not None:
            comun["violados"] = _violados(comun)
            evaluados.append(comun)
    estado = HISTORICO if historico else ABIERTA
    for e in evaluados:
        _guardar(sesion, e, variable, parametros, turnos, estado, creado_por, resumen)
    sesion.commit()
    return resumen


def _violados(evaluado: dict) -> dict:
    """(origen, regla) → turnos (fecha_hora) que violan esa regla en el ámbito."""
    violados = defaultdict(set)
    for v in evaluado["violaciones"]:
        violados[(v["origen"], v["regla"])].add(pd.Timestamp(evaluado["subgrupos"].loc[v["indice"], "fecha_hora"]))
    return violados


def separar_comunes(evaluados: list[dict], ambitos_eboe: list[str], planta_id: int) -> dict | None:
    """Saca de los eBOE las violaciones comunes (misma regla y origen en TODOS los eBOE de la planta en el mismo
    turno de producción) y las junta en un ámbito de planta.

    evaluados: [{ambito, equipo_id, planta_id, subgrupos, violaciones, linea (fecha_produccion, turno y fecha_hora
    de los turnos del ámbito)}] (se modifican: quedan solo las violaciones no comunes). Devuelve el evaluado de
    planta (comun = True; un subgrupo por turno común con la primera fecha_hora y los datos de todos los eBOE; valor
    = promedio de los eBOE; límite si es el mismo en todos; inmediata si lo es en alguno; su línea de tiempo es un
    punto por turno de producción) o None si no hay comunes o la planta tiene menos de 2 eBOE.
    """
    if len(ambitos_eboe) < 2:
        return None
    por_clave = defaultdict(list)
    for e in evaluados:
        if e["ambito"] not in ambitos_eboe:
            continue
        for v in e["violaciones"]:
            turno = e["subgrupos"].loc[v["indice"]]
            por_clave[(turno["fecha_produccion"], turno["turno"], v["origen"], v["regla"])].append((e, v, turno))
    comunes = {clave: lista for clave, lista in por_clave.items()
               if {e["ambito"] for e, _, _ in lista} >= set(ambitos_eboe)}
    if not comunes:
        return None
    filas, violaciones = {}, []
    for (dia, turno_nombre, origen, regla), lista in comunes.items():
        for e, v, _ in lista:
            e["violaciones"] = [x for x in e["violaciones"] if x is not v]
        if (dia, turno_nombre) not in filas:
            turnos_eboe = [t for _, _, t in lista]
            filas[(dia, turno_nombre)] = {
                "fecha_produccion": dia, "turno": turno_nombre, "fecha_hora": min(t["fecha_hora"] for t in turnos_eboe),
                "datos": sorted({d for t in turnos_eboe for d in t["datos"]})}
        limites = {v["limite"] for _, v, _ in lista}
        rangos = {v.get("rango_id") for _, v, _ in lista}
        violaciones.append({"indice": list(filas).index((dia, turno_nombre)), "origen": origen, "regla": regla,
                            "valor": float(np.mean([v["valor"] for _, v, _ in lista])),
                            "limite": limites.pop() if len(limites) == 1 else None,
                            "rango_id": rangos.pop() if len(rangos) == 1 else None,
                            "inmediata": any(v.get("inmediata") for _, v, _ in lista)})
    linea = pd.concat([e["linea"] for e in evaluados if e["ambito"] in ambitos_eboe])
    linea = linea.groupby(["fecha_produccion", "turno"], as_index=False)["fecha_hora"].min()
    return {"ambito": ambito_de(planta_id=planta_id), "equipo_id": None, "planta_id": planta_id,
            "subgrupos": pd.DataFrame(list(filas.values())), "violaciones": violaciones, "linea": linea,
            "comun": True}


def _guardar(sesion: Session, evaluado: dict, variable: str, parametros: dict, turnos: dict, estado: str,
             creado_por: str, resumen: dict) -> None:
    """Suma cada turno con violación al episodio en curso de su regla, o abre un episodio nuevo si la violación es
    persistente (los turnos_para_alertar − 1 turnos anteriores del ámbito también la violan) o inmediata. Las
    violaciones que todavía no son persistentes quedan pendientes: si el turno siguiente la confirma, el episodio
    empieza en ellas. Una alerta de planta por causa común lleva comun_eboe y COMENTARIO_COMUN."""
    ambito, subgrupos, violados = evaluado["ambito"], evaluado["subgrupos"], evaluado["violados"]
    comun = evaluado.get("comun", False)
    fin_episodio, para_alertar = parametros["turnos_fin_episodio"], parametros["turnos_para_alertar"]
    tiempos = sorted(set(pd.to_datetime(evaluado["linea"]["fecha_hora"])))
    episodios, pendientes = defaultdict(list), defaultdict(list)
    for alerta in sesion.scalars(select(Alerta).where(Alerta.variable == variable, Alerta.ambito == ambito)):
        episodios[(alerta.origen, alerta.regla)].append(alerta)
    violaciones = sorted(evaluado["violaciones"], key=lambda v: subgrupos.loc[v["indice"], "fecha_hora"])
    for violacion in violaciones:
        clave = (violacion["origen"], violacion["regla"])
        turno = subgrupos.loc[violacion["indice"]]
        instante = pd.Timestamp(turno["fecha_hora"])
        momento = instante.to_pydatetime()
        propios = episodios[clave]
        alerta = next((a for a in propios if a.fecha_hora <= momento <= (a.fin or a.fecha_hora)), None)
        nuevos_turnos = [turno]
        if alerta is None:                                   # no es un turno ya contado: ¿sigue el episodio en curso?
            en_curso = [a for a in propios if a.estado != CERRADA and a.episodio_abierto is not False
                        and (a.fin or a.fecha_hora) < momento]
            actual = max(en_curso, key=lambda a: a.fin or a.fecha_hora, default=None)
            if actual is not None and turnos_entre(tiempos, actual.fin or actual.fecha_hora, momento) < fin_episodio:
                alerta = actual
                alerta.fin = momento
                alerta.turnos_afectados = (alerta.turnos_afectados or 1) + 1
            else:
                if actual is not None:
                    actual.episodio_abierto = False
                previos = [t for t in tiempos if t < instante][-(para_alertar - 1):] if para_alertar > 1 else []
                persistente = len(previos) == para_alertar - 1 and all(t in violados[clave] for t in previos)
                inmediata = not persistente and violacion.get("inmediata", False)
                if not (persistente or inmediata):
                    pendientes[clave].append((violacion, turno))
                    continue
                corrida = [(v, t) for v, t in pendientes[clave] if pd.Timestamp(t["fecha_hora"]) in previos]
                pendientes[clave] = []
                primera, primer_turno = corrida[0] if corrida else (violacion, turno)
                alerta = Alerta(origen=violacion["origen"], regla=violacion["regla"], variable=variable,
                                ambito=ambito, equipo_id=evaluado["equipo_id"], planta_id=evaluado["planta_id"],
                                fecha_produccion=pd.Timestamp(primer_turno["fecha_produccion"]).date(),
                                turno_id=turnos[primer_turno["turno"]],
                                fecha_hora=pd.Timestamp(primer_turno["fecha_hora"]).to_pydatetime(), fin=momento,
                                turnos_afectados=len(corrida) + 1, episodio_abierto=True,
                                valor=float(primera["valor"]), limite=primera["limite"],
                                limites_id=primera.get("limites_id"), rango_id=primera.get("rango_id"),
                                estado=estado, creada_por=creado_por, comun_eboe=comun, inmediata=bool(inmediata),
                                comentario=COMENTARIO_COMUN if comun else None)
                sesion.add(alerta)
                propios.append(alerta)
                nuevos_turnos = [t for _, t in corrida] + [turno]
                resumen["creadas"] += 1
                resumen["inmediatas"] += bool(inmediata)
                resumen["por_regla"][violacion["regla"]] += 1
            resumen["turnos"] += len(nuevos_turnos)
        ya = {(d.tabla, d.dato_id) for d in alerta.datos}
        for un_turno in nuevos_turnos:
            for tabla, dato_id in un_turno["datos"]:
                if (tabla, dato_id) not in ya:
                    alerta.datos.append(AlertaDato(tabla=tabla, dato_id=dato_id))
                    ya.add((tabla, dato_id))
    for propios in episodios.values():                       # fin de episodio: N turnos sin violar la regla
        for alerta in propios:
            if alerta.episodio_abierto is not False and \
                    turnos_entre(tiempos, alerta.fin or alerta.fecha_hora, pd.Timestamp.max) >= fin_episodio:
                alerta.episodio_abierto = False


def alertas_de(sesion: Session, tabla: str, dato_id: int) -> list[Alerta]:
    """Las alertas en las que participa un dato (por ejemplo, un análisis de laboratorio)."""
    return list(sesion.scalars(select(Alerta).join(AlertaDato).where(AlertaDato.tabla == tabla,
                                                                     AlertaDato.dato_id == dato_id)
                               .order_by(Alerta.fecha_hora)))
