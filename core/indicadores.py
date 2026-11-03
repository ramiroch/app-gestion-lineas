"""Indicadores del eBOE calculados desde los análisis Scott guardados en la base.

Un par es el análisis de fruta entrada y el de fruta salida del mismo eBOE y la misma
fecha_hora (así los carga el importador). Con cada par se calcula la eficiencia
(1 − salida/entrada) y el rendimiento (entrada − salida, kg/t) con core.calculos.
"""

from collections import defaultdict
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core.calculos import aceite_en_fruta, eficiencia_eboe, rendimiento_eboe
from core.models import AnalisisScott, Equipo, Linea, Planta, PuntoMuestreo

TIPO_EBOE = "BOE"


def resumen_eboe_por_temporada(sesion: Session, planta_id: int, eficiencia_minima: float) -> list[dict]:
    """Resumen por temporada y eBOE de los pares entrada/salida de una planta.

    Promedio simple por par (sin ponderar por toneladas), como la tabla de la sección 7
    de docs/mapeo_ledesma.md. eficiencia_minima es una fracción (0,80 = 80 %): el objetivo
    de la planta, que se pasa desde afuera para no fijar el de LEDESMA en el código.

    Devuelve una lista ordenada por temporada y eBOE; cada elemento tiene:
    temporada, eboe (número), codigo, pares (N), eficiencia_media (fracción),
    rendimiento_medio (kg/t), aceite_entrada_medio (kg/t), fraccion_bajo_minimo
    (fracción de pares con eficiencia < eficiencia_minima) y descartados (pares con
    aceite de entrada 0, en los que no se puede calcular la eficiencia).
    """
    if not 0 < eficiencia_minima <= 1:
        raise ValueError(f"La eficiencia mínima va como fracción entre 0 y 1 (dice {eficiencia_minima}).")
    planta = sesion.get(Planta, planta_id)
    if planta is None:
        raise ValueError(f"No existe la planta con id {planta_id}.")

    analisis = sesion.scalars(
        select(AnalisisScott)
        .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
        .where(Linea.planta_id == planta_id, Equipo.tipo == TIPO_EBOE,
               PuntoMuestreo.tipo.in_(("fruta entrada", "fruta salida")))
        .options(selectinload(AnalisisScott.proceso),
                 selectinload(AnalisisScott.punto_muestreo).selectinload(PuntoMuestreo.equipo))
    ).all()

    # (equipo, fecha_hora) → {"fruta entrada": análisis, "fruta salida": análisis}
    por_hora = defaultdict(dict)
    for a in analisis:
        por_hora[(a.punto_muestreo.equipo_id, a.fecha_hora)][a.punto_muestreo.tipo] = a

    grupos = defaultdict(lambda: {"eficiencias": [], "rendimientos": [], "entradas": [], "descartados": 0})
    for par in por_hora.values():
        entrada, salida = par.get("fruta entrada"), par.get("fruta salida")
        if entrada is None or salida is None:
            continue
        equipo = entrada.punto_muestreo.equipo
        grupo = grupos[(entrada.proceso.temporada, equipo.numero, equipo.codigo)]
        aceite_e = _aceite(planta, entrada)
        aceite_s = _aceite(planta, salida)
        if aceite_e <= 0:
            grupo["descartados"] += 1
            continue
        grupo["eficiencias"].append(eficiencia_eboe(aceite_e, aceite_s))
        grupo["rendimientos"].append(rendimiento_eboe(aceite_e, aceite_s))
        grupo["entradas"].append(aceite_e)

    resumen = []
    for (temporada, numero, codigo), g in sorted(grupos.items()):
        n = len(g["eficiencias"])
        resumen.append({
            "temporada": temporada, "eboe": numero, "codigo": codigo, "pares": n,
            "eficiencia_media": mean(g["eficiencias"]) if n else None,
            "rendimiento_medio": mean(g["rendimientos"]) if n else None,
            "aceite_entrada_medio": mean(g["entradas"]) if n else None,
            "fraccion_bajo_minimo": sum(e < eficiencia_minima for e in g["eficiencias"]) / n if n else None,
            "descartados": g["descartados"],
        })
    return resumen


def _aceite(planta: Planta, analisis: AnalisisScott) -> float:
    return aceite_en_fruta(planta, analisis.vol_ml, analisis.blanco_ml, analisis.normalidad,
                           analisis.peso_muestra_g)
