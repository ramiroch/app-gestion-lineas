"""Alertas de control (fase II): los datos nuevos contra los límites vigentes y contra los rangos de la planta.

- violaciones_nelson(): reglas de Nelson ("1"…"8") y "MR" (rango móvil > LCS) de los datos nuevos, con los
  límites vigentes; mira también los puntos anteriores que necesita cada regla (hasta 14, solo de la fase II:
  después del período base, como las cartas, donde una racha no cruza de la fase I a la II).
- violaciones_rango(): datos fuera del rango de RangoReferencia con genera_alerta = sí, respetando lado_capacidad
  (eficiencia: solo por debajo del mínimo).
- evaluar_nuevos(): crea las alertas ("abierta", o "histórico" si se evalúan datos importados). Una alerta por
  origen + regla + variable + equipo + turno de producción: si vuelve a fallar en el mismo turno, se le suman datos.
  Sin límites vigentes, no evalúa Nelson pero sí el rango. Los datos inválidos (core.limites) no se evalúan.
- marcar_comunes(): la misma regla y variable en los 3 eBOE en el mismo turno → comun_eboe = sí y un comentario
  automático (los 3 eBOE comparten la muestra de entrada).
- alertas_de(): las alertas de un análisis o muestra (para mostrarlas y revisarlas si se corrige un dato).
"""

from collections import Counter

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.limites import TIPO_EBOE, VARIABLES_CONTROL, limites_vigentes
from core.models import Alerta, AlertaDato, Equipo, Linea, Planta, RangoReferencia
from core.spc import REGLAS, nelson

REGLA_MR, REGLA_RANGO = "MR", "rango"
ORIGEN_NELSON, ORIGEN_RANGO = "nelson", "rango"
ABIERTA, HISTORICO = "abierta", "histórico"
PUNTOS_ANTERIORES = 14            # la regla 4 mira 14 puntos atrás; las demás, menos
COMENTARIO_COMUN = "posible causa común: muestra de entrada, laboratorio o fruta"


def leer_reglas(texto: str | None) -> tuple[list[int], bool]:
    """"1,2,3,5,MR" → ([1, 2, 3, 5], True). Da error con una regla desconocida."""
    numeros, mr = [], False
    for parte in (p.strip() for p in (texto or "").split(",") if p.strip()):
        if parte.upper() == REGLA_MR:
            mr = True
        elif parte.isdigit() and int(parte) in REGLAS:
            numeros.append(int(parte))
        else:
            raise ValueError(f'Regla "{parte}" no válida: usá números de Nelson del 1 al 8 y "MR".')
    return sorted(set(numeros)), mr


# ---------------------------------------------------------------- violaciones (funciones puras)

def violaciones_nelson(nuevos: pd.DataFrame, previos: pd.DataFrame | None, lc: float, sigma: float, mr_lcs: float,
                       reglas: str) -> list[dict]:
    """Reglas violadas por los datos nuevos (de un equipo, en orden de tiempo).

    previos: datos anteriores del mismo equipo en la fase II (se usan los últimos PUNTOS_ANTERIORES para las
    rachas y el rango móvil del primer dato nuevo). Devuelve una lista de {indice (de nuevos), regla, limite}:
    regla 1 → LCS o LCI; 5 → LC ± 2σ; 2, 4, 6, 7, 8 → LC; 3 → sin límite; "MR" → LCS del rango móvil.
    """
    numeros, mr = leer_reglas(reglas)
    anteriores = previos.sort_values("fecha_hora").tail(PUNTOS_ANTERIORES) if previos is not None else nuevos.iloc[0:0]
    serie = pd.concat([anteriores, nuevos.sort_values("fecha_hora")])
    desde = len(anteriores)
    valores = serie["valor"].to_numpy(dtype=float)
    resultado = []
    for posicion, violadas in enumerate(nelson(valores, lc, sigma, numeros) if numeros else [[]] * len(valores)):
        if posicion < desde:
            continue
        valor = valores[posicion]
        for regla in violadas:
            limite = {1: lc + 3 * sigma if valor > lc else lc - 3 * sigma,
                      5: lc + 2 * sigma if valor > lc else lc - 2 * sigma, 3: None}.get(regla, lc)
            resultado.append({"indice": serie.index[posicion], "regla": str(regla), "limite": limite})
        if mr and posicion > 0 and abs(valor - valores[posicion - 1]) > mr_lcs:
            resultado.append({"indice": serie.index[posicion], "regla": REGLA_MR, "limite": mr_lcs})
    return resultado


def violaciones_rango(nuevos: pd.DataFrame, minimo: float | None, maximo: float | None,
                      lado: str | None = "ambos") -> list[dict]:
    """Datos fuera del rango [mínimo, máximo]. lado "inferior": solo por debajo del mínimo; "superior": solo por
    encima del máximo. Devuelve {indice, regla: "rango", limite}."""
    lado = lado or "ambos"
    resultado = []
    for indice, valor in nuevos["valor"].items():
        if lado != "superior" and minimo is not None and valor < minimo:
            resultado.append({"indice": indice, "regla": REGLA_RANGO, "limite": minimo})
        elif lado != "inferior" and maximo is not None and valor > maximo:
            resultado.append({"indice": indice, "regla": REGLA_RANGO, "limite": maximo})
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
    """Evalúa datos nuevos de una variable (COLUMNAS_SERIE de core.limites, uno o varios equipos) y crea alertas.

    previos: datos anteriores (misma variable) para las reglas que miran hacia atrás. historico=True: las alertas
    se crean como "histórico" (datos importados) en lugar de "abierta". Devuelve {"creadas", "actualizadas",
    "por_regla": Counter, "sin_limites": [equipos sin límites vigentes]}.
    """
    if variable not in VARIABLES_CONTROL:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_CONTROL)}.')
    turnos = {t.nombre: t.id for t in planta.turnos}
    resumen = {"creadas": 0, "actualizadas": 0, "por_regla": Counter(), "sin_limites": []}
    validos = nuevos[nuevos["invalido"].isna()] if "invalido" in nuevos else nuevos
    for equipo_id, grupo in validos.groupby("equipo_id"):
        grupo = grupo.sort_values("fecha_hora")
        frutas = grupo["fruta"].dropna()
        fruta = frutas.mode().iloc[0] if not frutas.empty else None
        violaciones = []
        limites = limites_vigentes(sesion, variable, int(equipo_id), fruta)
        if limites is not None:
            anteriores = None
            if previos is not None and not previos.empty:
                anteriores = previos[(previos["equipo_id"] == equipo_id) & previos["invalido"].isna()
                                     & (previos["fecha_hora"] < grupo["fecha_hora"].min())
                                     & (previos["fecha_produccion"] > pd.Timestamp(limites.base_hasta))]
            violaciones += [v | {"origen": ORIGEN_NELSON, "limites_id": limites.id} for v in violaciones_nelson(
                grupo, anteriores, limites.lc, limites.sigma, limites.mr_lcs, limites.reglas)]
        else:
            resumen["sin_limites"].append(int(equipo_id))
        rango = rango_de_alerta(sesion, planta.id, variable, grupo["tipo"].iloc[0], fruta)
        if rango is not None:
            violaciones += [v | {"origen": ORIGEN_RANGO, "rango_id": rango.id} for v in violaciones_rango(
                grupo, rango.minimo, rango.maximo, rango.lado_capacidad)]
        _guardar(sesion, int(equipo_id), variable, grupo, violaciones, turnos,
                 HISTORICO if historico else ABIERTA, creado_por, resumen)
    sesion.flush()
    if comunes and variable in VARIABLES_CONTROL and not nuevos.empty and (nuevos["tipo"] == TIPO_EBOE).all():
        marcar_comunes(sesion, planta.id, variable)
    sesion.commit()
    return resumen


def _guardar(sesion: Session, equipo_id: int, variable: str, grupo: pd.DataFrame, violaciones: list[dict],
             turnos: dict, estado: str, creado_por: str, resumen: dict) -> None:
    """Crea la alerta de cada violación o le suma el dato a la del mismo turno (sin duplicados)."""
    existentes = {(a.origen, a.regla, a.fecha_produccion, a.turno_id): a for a in sesion.scalars(
        select(Alerta).where(Alerta.variable == variable, Alerta.equipo_id == equipo_id))}
    for violacion in violaciones:
        dato = grupo.loc[violacion["indice"]]
        fecha = pd.Timestamp(dato["fecha_produccion"]).date()
        clave = (violacion["origen"], violacion["regla"], fecha, turnos[dato["turno"]])
        alerta = existentes.get(clave)
        if alerta is None:
            alerta = Alerta(origen=violacion["origen"], regla=violacion["regla"], variable=variable,
                            equipo_id=equipo_id, fecha_produccion=fecha, turno_id=clave[3],
                            fecha_hora=dato["fecha_hora"].to_pydatetime(), valor=float(dato["valor"]),
                            limite=violacion["limite"], limites_id=violacion.get("limites_id"),
                            rango_id=violacion.get("rango_id"), estado=estado, creada_por=creado_por)
            sesion.add(alerta)
            existentes[clave] = alerta
            resumen["creadas"] += 1
            resumen["por_regla"][violacion["regla"]] += 1
        elif not any(d.tabla == dato["tabla"] and d.dato_id == int(dato["dato_id"]) for d in alerta.datos):
            resumen["actualizadas"] += 1
        if not any(d.tabla == dato["tabla"] and d.dato_id == int(dato["dato_id"]) for d in alerta.datos):
            alerta.datos.append(AlertaDato(tabla=dato["tabla"], dato_id=int(dato["dato_id"])))


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
