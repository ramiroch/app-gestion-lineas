"""Alertas de control (fase II), por TURNO y agrupadas en EPISODIOS.

Un punto por turno de producción y ámbito (equipo, o la planta en el pH): el promedio de sus muestras (subgrupo de
n variable, core.limites.subgrupos_por_turno).
- violaciones_por_turno(): reglas de Nelson en la carta de los promedios y "S" (desvío del turno fuera de la carta
  S). Con límites "I-MR-S" (entre/dentro) los límites del promedio salen de la variación entre turnos y no dependen
  del n; con "Xbar-S", de la variación dentro del turno (x̿ ± 3σ/√n). Los turnos con 1 muestra se evalúan en el
  promedio pero no en S. Mira los turnos anteriores que necesita cada regla (hasta 14, solo de la fase II: después
  del período base, como en las cartas).
- violaciones_rango(): el promedio del turno fuera del rango de RangoReferencia con genera_alerta = sí, respetando
  lado_capacidad (eficiencia: solo por debajo del mínimo).
- evaluar_nuevos(): crea las alertas ("abierta", o "histórico" si se evalúan datos importados), agrupadas en
  episodios: los turnos que vuelven a violar la misma regla (origen + regla + variable + ámbito) se suman a la alerta
  del episodio en curso (turnos_afectados, fin). El episodio se cierra solo tras Planta.turnos_fin_episodio turnos
  seguidos sin violarla; si después vuelve a violarse, es una alerta nueva. Una alerta cerrada a mano no se reabre.
  Sin límites vigentes por turno, no evalúa Nelson pero sí el rango. Los datos inválidos no entran.
- marcar_comunes(): episodios de la misma regla y variable que se superponen en el tiempo en los 3 eBOE → comun_eboe
  y un comentario automático (los 3 eBOE comparten la muestra de entrada).
- alertas_de(): las alertas de un análisis o muestra (para mostrarlas y revisarlas si se corrige un dato).
"""

from collections import Counter, defaultdict

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.limites import (ENTRE_DENTRO, TIPO_EBOE, VARIABLES_CONTROL, XBAR_S, limites_vigentes,
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


def leer_reglas(texto: str | None) -> tuple[list[int], set[str]]:
    """"1,2,3,5,S" → ([1, 2, 3, 5], {"S"}). Además de los números de Nelson: "S" (carta S) y "MR" (carta MR)."""
    numeros, otras = [], set()
    for parte in (p.strip() for p in (texto or "").split(",") if p.strip()):
        if parte.upper() in (REGLA_MR, REGLA_S):
            otras.add(parte.upper())
        elif parte.isdigit() and int(parte) in REGLAS:
            numeros.append(int(parte))
        else:
            raise ValueError(f'Regla "{parte}" no válida: usá números de Nelson del 1 al 8, "S" y "MR".')
    return sorted(set(numeros)), otras


# ---------------------------------------------------------------- violaciones (funciones puras)

def violaciones_por_turno(nuevos: pd.DataFrame, previos: pd.DataFrame | None, lc: float, sigma: float, reglas: str,
                          tipo_carta: str = ENTRE_DENTRO, sigma_dentro: float | None = None) -> list[dict]:
    """Reglas violadas por los turnos nuevos (subgrupos de un ámbito, con n, media y desvio).

    Carta de los promedios: Nelson con LC = lc y σ de cada punto = sigma ("I-MR-S": σ entre turnos, igual para todo
    n) o sigma/√n ("Xbar-S"). Carta S (si "S" está en reglas y n ≥ 2): desvío del turno > LCS de S o < LCI de S
    para su n, con σ dentro (sigma_dentro en "I-MR-S"; sigma en "Xbar-S").
    previos: turnos anteriores del mismo ámbito en la fase II (se usan los últimos TURNOS_ANTERIORES).
    Devuelve [{indice (de nuevos), regla, valor, limite}]: regla 1 → LCS o LCI; 5 → lc ± 2σ; 2, 4, 6, 7, 8 → lc;
    3 → sin límite; "S" → el límite de S que cruzó (valor = desvío del turno).
    """
    if tipo_carta not in POR_TURNO:
        raise ValueError(f'Las alertas por turno usan límites "{ENTRE_DENTRO}" o "{XBAR_S}" (llegó "{tipo_carta}").')
    numeros, otras = leer_reglas(reglas)
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
            resultado.append({"indice": indice, "regla": str(regla), "valor": media, "limite": limite})
        desvio = serie["desvio"].iloc[posicion]
        if REGLA_S in otras and sigma_s and n[posicion] >= 2 and desvio is not None and not pd.isna(desvio):
            limites = limites_xbar_s_para(lc, sigma_s, int(n[posicion]))
            if desvio > limites["s_lcs"]:
                resultado.append({"indice": indice, "regla": REGLA_S, "valor": float(desvio), "limite": limites["s_lcs"]})
            elif limites["s_lci"] > 0 and desvio < limites["s_lci"]:
                resultado.append({"indice": indice, "regla": REGLA_S, "valor": float(desvio), "limite": limites["s_lci"]})
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

    previos: datos anteriores (misma variable) para las reglas que miran hacia atrás y para contar los turnos sin
    alerta de los episodios. historico=True: las alertas nuevas son "histórico" (datos importados). Usa el parámetro
    de la planta turnos_fin_episodio. Devuelve {"creadas" (episodios nuevos), "turnos" (turnos con violación nuevos),
    "por_regla": Counter de episodios nuevos, "sin_limites": [ámbitos sin límites por turno vigentes]}.
    """
    if variable not in VARIABLES_CONTROL:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_CONTROL)}.')
    fin_episodio = validar_parametros(planta, ["turnos_fin_episodio"])["turnos_fin_episodio"]
    turnos = {t.nombre: t.id for t in planta.turnos}
    resumen = {"creadas": 0, "turnos": 0, "por_regla": Counter(), "sin_limites": []}
    previos_validos = previos[previos["invalido"].isna()] if previos is not None and not previos.empty else None
    for ambito, grupo in subgrupos_por_turno(nuevos).groupby("ambito", sort=False):
        grupo = grupo.sort_values("fecha_hora")
        equipo_id = None if pd.isna(grupo["equipo_id"].iloc[0]) else int(grupo["equipo_id"].iloc[0])
        planta_id = None if equipo_id is not None else int(grupo["planta_id"].iloc[0])
        frutas = grupo["fruta"].dropna()
        fruta = frutas.mode().iloc[0] if not frutas.empty else None
        anteriores = subgrupos_por_turno(previos_validos[previos_validos["ambito"] == ambito]) \
            if previos_validos is not None else grupo.iloc[0:0]
        anteriores = anteriores[anteriores["fecha_hora"] < grupo["fecha_hora"].min()]
        violaciones = []
        limites = limites_vigentes(sesion, variable, fruta, equipo_id, planta_id)
        if limites is not None and limites.tipo_carta in POR_TURNO:
            fase2 = anteriores[anteriores["fecha_produccion"] > pd.Timestamp(limites.base_hasta)]
            violaciones += [v | {"origen": ORIGEN_NELSON, "limites_id": limites.id} for v in violaciones_por_turno(
                grupo, fase2, limites.lc, limites.sigma, limites.reglas, limites.tipo_carta, limites.sigma_dentro)]
        else:
            resumen["sin_limites"].append(ambito)
        rango = rango_de_alerta(sesion, planta.id, variable, grupo["tipo"].iloc[0], fruta)
        if rango is not None:
            violaciones += [v | {"origen": ORIGEN_RANGO, "rango_id": rango.id} for v in violaciones_rango(
                grupo, rango.minimo, rango.maximo, rango.lado_capacidad)]
        tiempos = sorted(set(pd.concat([anteriores["fecha_hora"], grupo["fecha_hora"]])))
        _guardar(sesion, ambito, equipo_id, planta_id, variable, grupo, violaciones, tiempos, fin_episodio, turnos,
                 HISTORICO if historico else ABIERTA, creado_por, resumen)
    sesion.flush()
    if comunes and not nuevos.empty and (nuevos["tipo"] == TIPO_EBOE).all():
        marcar_comunes(sesion, planta.id, variable)
    sesion.commit()
    return resumen


def _guardar(sesion: Session, ambito: str, equipo_id: int | None, planta_id: int | None, variable: str,
             subgrupos: pd.DataFrame, violaciones: list[dict], tiempos: list, fin_episodio: int, turnos: dict,
             estado: str, creado_por: str, resumen: dict) -> None:
    """Suma cada turno con violación al episodio en curso de su regla, o abre un episodio nuevo."""
    episodios = defaultdict(list)
    for alerta in sesion.scalars(select(Alerta).where(Alerta.variable == variable, Alerta.ambito == ambito)):
        episodios[(alerta.origen, alerta.regla)].append(alerta)
    violaciones = sorted(violaciones, key=lambda v: subgrupos.loc[v["indice"], "fecha_hora"])
    for violacion in violaciones:
        turno = subgrupos.loc[violacion["indice"]]
        momento = pd.Timestamp(turno["fecha_hora"]).to_pydatetime()
        propios = episodios[(violacion["origen"], violacion["regla"])]
        alerta = next((a for a in propios if a.fecha_hora <= momento <= (a.fin or a.fecha_hora)), None)
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
                alerta = Alerta(origen=violacion["origen"], regla=violacion["regla"], variable=variable,
                                ambito=ambito, equipo_id=equipo_id, planta_id=planta_id,
                                fecha_produccion=pd.Timestamp(turno["fecha_produccion"]).date(),
                                turno_id=turnos[turno["turno"]], fecha_hora=momento, fin=momento, turnos_afectados=1,
                                episodio_abierto=True, valor=float(violacion["valor"]), limite=violacion["limite"],
                                limites_id=violacion.get("limites_id"), rango_id=violacion.get("rango_id"),
                                estado=estado, creada_por=creado_por)
                sesion.add(alerta)
                propios.append(alerta)
                resumen["creadas"] += 1
                resumen["por_regla"][violacion["regla"]] += 1
            resumen["turnos"] += 1
        ya = {(d.tabla, d.dato_id) for d in alerta.datos}
        for tabla, dato_id in turno["datos"]:
            if (tabla, dato_id) not in ya:
                alerta.datos.append(AlertaDato(tabla=tabla, dato_id=dato_id))
    for propios in episodios.values():                       # fin de episodio: N turnos sin violar la regla
        for alerta in propios:
            if alerta.episodio_abierto is not False and \
                    turnos_entre(tiempos, alerta.fin or alerta.fecha_hora, pd.Timestamp.max) >= fin_episodio:
                alerta.episodio_abierto = False


def marcar_comunes(sesion: Session, planta_id: int, variable: str) -> int:
    """Marca comun_eboe en los episodios de la variable que, con la misma regla, se superponen en el tiempo con un
    episodio de cada uno de los otros eBOE de la planta, y les agrega COMENTARIO_COMUN. Devuelve cuántos quedaron
    marcados."""
    eboes = sesion.scalars(select(Equipo.id).join(Linea).where(Linea.planta_id == planta_id,
                                                                Equipo.tipo == TIPO_EBOE)).all()
    if len(eboes) < 2:
        return 0
    grupos = defaultdict(lambda: defaultdict(list))
    for alerta in sesion.scalars(select(Alerta).where(Alerta.variable == variable, Alerta.equipo_id.in_(eboes))):
        grupos[(alerta.origen, alerta.regla)][alerta.equipo_id].append(alerta)
    marcadas = 0
    for por_equipo in grupos.values():
        if len(por_equipo) < len(eboes):
            continue
        for equipo_id, propias in por_equipo.items():
            for alerta in propias:
                fin = alerta.fin or alerta.fecha_hora
                if all(any(o.fecha_hora <= fin and (o.fin or o.fecha_hora) >= alerta.fecha_hora for o in otras)
                       for otro, otras in por_equipo.items() if otro != equipo_id):
                    if not alerta.comun_eboe:
                        alerta.comun_eboe = True
                        alerta.comentario = COMENTARIO_COMUN if not alerta.comentario else (
                            f"{alerta.comentario}; {COMENTARIO_COMUN}")
                    marcadas += 1
    return marcadas


def alertas_de(sesion: Session, tabla: str, dato_id: int) -> list[Alerta]:
    """Las alertas en las que participa un dato (por ejemplo, un análisis de laboratorio)."""
    return list(sesion.scalars(select(Alerta).join(AlertaDato).where(AlertaDato.tabla == tabla,
                                                                     AlertaDato.dato_id == dato_id)
                               .order_by(Alerta.fecha_hora)))
