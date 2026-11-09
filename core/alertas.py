"""Alertas de control (fase II), por TURNO: los datos nuevos contra los límites X̄-S vigentes y contra los rangos.

Un punto por turno de producción y ámbito (equipo, o la planta en el pH): el promedio de sus muestras (subgrupo de
n variable, core.limites.subgrupos_por_turno).
- violaciones_xbar_s(): reglas de Nelson en la carta X̄ (límites según el n de cada turno: x̿ ± 3σ/√n) y "S" (el
  desvío del turno fuera de los límites de la carta S: mucha dispersión dentro del turno). Los turnos con 1 muestra
  se evalúan en X̄ pero no en S. Mira los turnos anteriores que necesita cada regla (hasta 14, solo de la fase II:
  después del período base, como en las cartas, donde una racha no cruza de la fase I a la II).
- violaciones_rango(): el promedio del turno fuera del rango de RangoReferencia con genera_alerta = sí, respetando
  lado_capacidad (eficiencia: solo por debajo del mínimo).
- evaluar_nuevos(): crea las alertas ("abierta", o "histórico" si se evalúan datos importados). Una alerta por
  origen + regla + variable + ámbito + turno de producción: si se vuelve a evaluar, se le suman los datos nuevos.
  Sin límites X̄-S vigentes, no evalúa Nelson pero sí el rango. Los datos inválidos no entran.
- marcar_comunes(): la misma regla y variable en los 3 eBOE en el mismo turno → comun_eboe = sí y un comentario
  automático (los 3 eBOE comparten la muestra de entrada).
- alertas_de(): las alertas de un análisis o muestra (para mostrarlas y revisarlas si se corrige un dato).
"""

from collections import Counter

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.limites import TIPO_EBOE, VARIABLES_CONTROL, XBAR_S, limites_vigentes, subgrupos_por_turno
from core.models import Alerta, AlertaDato, Equipo, Linea, Planta, RangoReferencia
from core.spc import REGLAS, limites_xbar_s_para, nelson

REGLA_MR, REGLA_S, REGLA_RANGO = "MR", "S", "rango"
ORIGEN_NELSON, ORIGEN_RANGO = "nelson", "rango"
ABIERTA, HISTORICO = "abierta", "histórico"
TURNOS_ANTERIORES = 14            # la regla 4 mira 14 puntos atrás; las demás, menos
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

def violaciones_xbar_s(nuevos: pd.DataFrame, previos: pd.DataFrame | None, lc: float, sigma: float,
                       reglas: str) -> list[dict]:
    """Reglas violadas por los turnos nuevos (subgrupos de un ámbito, con n, media y desvio).

    previos: turnos anteriores del mismo ámbito en la fase II (se usan los últimos TURNOS_ANTERIORES). X̄: Nelson con
    LC = x̿ y σ de cada punto = σ/√n. S (si está en reglas y n ≥ 2): desvío > LCS de S o < LCI de S para ese n.
    Devuelve [{indice (de nuevos), regla, valor, limite}]: regla 1 → LCS o LCI del turno; 5 → x̿ ± 2σ/√n;
    2, 4, 6, 7, 8 → x̿; 3 → sin límite; "S" → el límite de S que cruzó (valor = desvío del turno).
    """
    numeros, otras = leer_reglas(reglas)
    anteriores = previos.sort_values("fecha_hora").tail(TURNOS_ANTERIORES) if previos is not None else nuevos.iloc[0:0]
    serie = pd.concat([anteriores, nuevos.sort_values("fecha_hora")])
    desde = len(anteriores)
    medias = serie["media"].to_numpy(dtype=float)
    n = serie["n"].to_numpy(dtype=int)
    sigma_media = sigma / n ** 0.5
    violadas_por_punto = nelson(medias, lc, sigma_media, numeros) if numeros else [[]] * len(serie)
    resultado = []
    for posicion in range(desde, len(serie)):
        indice, media, s_media = serie.index[posicion], medias[posicion], sigma_media[posicion]
        for regla in violadas_por_punto[posicion]:
            limite = {1: lc + 3 * s_media if media > lc else lc - 3 * s_media,
                      5: lc + 2 * s_media if media > lc else lc - 2 * s_media, 3: None}.get(regla, lc)
            resultado.append({"indice": indice, "regla": str(regla), "valor": media, "limite": limite})
        desvio = serie["desvio"].iloc[posicion]
        if REGLA_S in otras and n[posicion] >= 2 and desvio is not None and not pd.isna(desvio):
            limites = limites_xbar_s_para(lc, sigma, int(n[posicion]))
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
    """Evalúa datos nuevos de una variable (COLUMNAS_SERIE de core.limites, uno o varios ámbitos) por turno y crea
    alertas.

    previos: datos anteriores (misma variable) para las reglas que miran hacia atrás. historico=True: las alertas
    se crean como "histórico" (datos importados) en lugar de "abierta". Devuelve {"creadas", "actualizadas",
    "por_regla": Counter, "sin_limites": [ámbitos sin límites X̄-S vigentes]}.
    """
    if variable not in VARIABLES_CONTROL:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_CONTROL)}.')
    turnos = {t.nombre: t.id for t in planta.turnos}
    resumen = {"creadas": 0, "actualizadas": 0, "por_regla": Counter(), "sin_limites": []}
    previos_validos = previos[previos["invalido"].isna()] if previos is not None and not previos.empty else None
    for ambito, grupo in subgrupos_por_turno(nuevos).groupby("ambito", sort=False):
        grupo = grupo.sort_values("fecha_hora")
        equipo_id = None if pd.isna(grupo["equipo_id"].iloc[0]) else int(grupo["equipo_id"].iloc[0])
        planta_id = None if equipo_id is not None else int(grupo["planta_id"].iloc[0])
        frutas = grupo["fruta"].dropna()
        fruta = frutas.mode().iloc[0] if not frutas.empty else None
        violaciones = []
        limites = limites_vigentes(sesion, variable, fruta, equipo_id, planta_id)
        if limites is not None and limites.tipo_carta == XBAR_S:
            anteriores = None
            if previos_validos is not None:
                propios = previos_validos[(previos_validos["ambito"] == ambito)
                                          & (previos_validos["fecha_produccion"] > pd.Timestamp(limites.base_hasta))]
                anteriores = subgrupos_por_turno(propios)
                anteriores = anteriores[anteriores["fecha_hora"] < grupo["fecha_hora"].min()]
            violaciones += [v | {"origen": ORIGEN_NELSON, "limites_id": limites.id} for v in violaciones_xbar_s(
                grupo, anteriores, limites.lc, limites.sigma, limites.reglas)]
        else:
            resumen["sin_limites"].append(ambito)
        rango = rango_de_alerta(sesion, planta.id, variable, grupo["tipo"].iloc[0], fruta)
        if rango is not None:
            violaciones += [v | {"origen": ORIGEN_RANGO, "rango_id": rango.id} for v in violaciones_rango(
                grupo, rango.minimo, rango.maximo, rango.lado_capacidad)]
        _guardar(sesion, ambito, equipo_id, planta_id, variable, grupo, violaciones, turnos,
                 HISTORICO if historico else ABIERTA, creado_por, resumen)
    sesion.flush()
    if comunes and not nuevos.empty and (nuevos["tipo"] == TIPO_EBOE).all():
        marcar_comunes(sesion, planta.id, variable)
    sesion.commit()
    return resumen


def _guardar(sesion: Session, ambito: str, equipo_id: int | None, planta_id: int | None, variable: str,
             subgrupos: pd.DataFrame, violaciones: list[dict], turnos: dict, estado: str, creado_por: str,
             resumen: dict) -> None:
    """Crea la alerta de cada violación o le suma los datos del turno a la que ya existe (sin duplicados)."""
    existentes = {(a.origen, a.regla, a.fecha_produccion, a.turno_id): a for a in sesion.scalars(
        select(Alerta).where(Alerta.variable == variable, Alerta.ambito == ambito))}
    for violacion in violaciones:
        turno = subgrupos.loc[violacion["indice"]]
        fecha = pd.Timestamp(turno["fecha_produccion"]).date()
        clave = (violacion["origen"], violacion["regla"], fecha, turnos[turno["turno"]])
        alerta = existentes.get(clave)
        if alerta is None:
            alerta = Alerta(origen=violacion["origen"], regla=violacion["regla"], variable=variable, ambito=ambito,
                            equipo_id=equipo_id, planta_id=planta_id, fecha_produccion=fecha, turno_id=clave[3],
                            fecha_hora=pd.Timestamp(turno["fecha_hora"]).to_pydatetime(),
                            valor=float(violacion["valor"]), limite=violacion["limite"],
                            limites_id=violacion.get("limites_id"), rango_id=violacion.get("rango_id"),
                            estado=estado, creada_por=creado_por)
            sesion.add(alerta)
            existentes[clave] = alerta
            resumen["creadas"] += 1
            resumen["por_regla"][violacion["regla"]] += 1
        ya = {(d.tabla, d.dato_id) for d in alerta.datos}
        nuevos = [(tabla, dato_id) for tabla, dato_id in turno["datos"] if (tabla, dato_id) not in ya]
        if nuevos and ya:
            resumen["actualizadas"] += 1
        for tabla, dato_id in nuevos:
            alerta.datos.append(AlertaDato(tabla=tabla, dato_id=dato_id))


def marcar_comunes(sesion: Session, planta_id: int, variable: str) -> int:
    """Marca comun_eboe en las alertas de la variable que saltaron con la misma regla en todos los eBOE de la
    planta en el mismo turno, y les agrega COMENTARIO_COMUN. Devuelve cuántas alertas quedaron marcadas."""
    eboes = sesion.scalars(select(Equipo.id).join(Linea).where(Linea.planta_id == planta_id,
                                                                Equipo.tipo == TIPO_EBOE)).all()
    if len(eboes) < 2:
        return 0
    grupos = sesion.execute(
        select(Alerta.origen, Alerta.regla, Alerta.fecha_produccion, Alerta.turno_id)
        .where(Alerta.variable == variable, Alerta.equipo_id.in_(eboes))
        .group_by(Alerta.origen, Alerta.regla, Alerta.fecha_produccion, Alerta.turno_id)
        .having(func.count(func.distinct(Alerta.equipo_id)) == len(eboes))).all()
    marcadas = 0
    for origen, regla, fecha, turno_id in grupos:
        for alerta in sesion.scalars(select(Alerta).where(
                Alerta.variable == variable, Alerta.equipo_id.in_(eboes), Alerta.origen == origen,
                Alerta.regla == regla, Alerta.fecha_produccion == fecha, Alerta.turno_id == turno_id)):
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
