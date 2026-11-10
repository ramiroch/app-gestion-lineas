"""Entradas repetidas en varios eBOE (posible copia).

Cada eBOE tiene su propia muestra de entrada (fruta, peso, n.º de frutos, g y mL). Si en una ronda (misma fecha_hora)
la entrada de dos o tres eBOE tiene exactamente los mismos g y mL, lo más probable es que se haya copiado de un eBOE
a otro. Esas entradas se marcan en AnalisisScott.revisar con REVISAR_ENTRADA_REPETIDA: no se excluyen de ningún
cálculo, pero sí de las comparaciones entre eBOE (salvo que se pida incluirlas, como las copiadas de centrífugas).
- entradas_repetidas(): ids de las entradas repetidas (función pura).
- con_motivo(): agrega o saca un motivo del texto de revisar ("motivo 1; motivo 2").
- marcar_entradas_repetidas(): pone o saca la marca en la base (se puede correr muchas veces).
- sin_entradas_repetidas(): saca de una tabla las filas marcadas (para comparar eBOE).
"""

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import AnalisisScott, Equipo, Linea, PuntoMuestreo

REVISAR_ENTRADA_REPETIDA = "entrada repetida en varios eBOE (posible copia)"
TIPO_ENTRADA = "fruta entrada"           # PuntoMuestreo.tipo de la entrada (core.analisis.LUGARES)
SEPARADOR = "; "


def entradas_repetidas(entradas: pd.DataFrame) -> set[int]:
    """Ids de las entradas con los mismos g y mL que la entrada de otro eBOE en la misma fecha_hora.

    entradas: una fila por análisis de entrada, con analisis_id, fecha_hora, eboe, peso_muestra_g y vol_ml.
    """
    datos = entradas.dropna(subset=["peso_muestra_g", "vol_ml"])
    if datos.empty:
        return set()
    eboes = datos.groupby(["fecha_hora", "peso_muestra_g", "vol_ml"])["eboe"].transform("nunique")
    return set(datos.loc[eboes > 1, "analisis_id"].astype(int))


def con_motivo(revisar: str | None, motivo: str, poner: bool) -> str | None:
    """El texto de revisar con el motivo agregado (poner=True) o sacado (False); None si queda vacío."""
    motivos = [m for m in (revisar or "").split(SEPARADOR) if m and m != motivo]
    if poner:
        motivos.append(motivo)
    return SEPARADOR.join(motivos) or None


def marcar_entradas_repetidas(sesion: Session, planta_id: int, guardar: bool = True) -> pd.DataFrame:
    """Pone REVISAR_ENTRADA_REPETIDA en las entradas repetidas de los eBOE de la planta y la saca de las que ya no
    lo están (por ejemplo, si se corrigió un dato).

    Devuelve una fila por temporada: rondas (fechas y horas con entrada), rondas_repetidas (con al menos dos eBOE
    iguales), rondas_3 (los 3 eBOE iguales) y entradas (análisis marcados).
    """
    filas = sesion.execute(
        select(AnalisisScott, Equipo.numero)
        .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
        .where(Linea.planta_id == planta_id, Equipo.tipo == "BOE", PuntoMuestreo.tipo == TIPO_ENTRADA)).all()
    tabla = pd.DataFrame([{"analisis_id": a.id, "fecha_hora": a.fecha_hora, "eboe": numero,
                           "peso_muestra_g": a.peso_muestra_g, "vol_ml": a.vol_ml,
                           "temporada": a.proceso.temporada} for a, numero in filas],
                         columns=["analisis_id", "fecha_hora", "eboe", "peso_muestra_g", "vol_ml", "temporada"])
    repetidas = entradas_repetidas(tabla)
    for analisis, _ in filas:
        nuevo = con_motivo(analisis.revisar, REVISAR_ENTRADA_REPETIDA, analisis.id in repetidas)
        if nuevo != analisis.revisar:
            analisis.revisar = nuevo
    if guardar:
        sesion.commit()
    tabla["repetida"] = tabla["analisis_id"].isin(repetidas)
    resumen = []
    for temporada, sub in tabla.groupby("temporada"):
        por_ronda = sub.groupby("fecha_hora")
        marcadas = sub[sub["repetida"]]
        resumen.append({"temporada": temporada, "rondas": por_ronda.ngroups,
                        "rondas_repetidas": marcadas["fecha_hora"].nunique(),
                        "rondas_3": int((marcadas.groupby("fecha_hora")["eboe"].nunique() >= 3).sum()),
                        "entradas": len(marcadas)})
    return pd.DataFrame(resumen, columns=["temporada", "rondas", "rondas_repetidas", "rondas_3", "entradas"])


def sin_entradas_repetidas(datos: pd.DataFrame, incluir: bool = False) -> pd.DataFrame:
    """La tabla sin las filas con entrada_repetida (para comparar eBOE); incluir=True la deja entera."""
    if incluir or "entrada_repetida" not in datos:
        return datos
    return datos[~datos["entrada_repetida"].fillna(False).astype(bool)]
