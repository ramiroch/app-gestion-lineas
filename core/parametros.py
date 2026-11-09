"""Parámetros de cálculo de una planta (blancos, factores y hora de inicio del día).

Los cálculos los piden con parametros_planta(): si falta alguno, se corta con un
error claro en lugar de calcular con un valor inventado.
"""

from sqlalchemy.orm import Session

from core.models import Planta

DESCRIPCION_PARAMETROS = {
    "blanco_ml": "el blanco (mL)",
    "factor_aceite_fruta": "el factor de aceite en fruta",
    "factor_aceite_emulsion": "el factor de aceite en emulsión",
    "hora_inicio_dia": "la hora de inicio del día de producción",
    "blanco_centrifugas_ml": "el blanco de centrífugas (mL)",
    "umbral_paralelo_lh": "el umbral de primarias en paralelo (L/h)",
    "turnos_fin_episodio": "los turnos sin alerta que cierran un episodio",
    "turnos_para_alertar": "los turnos seguidos que crean una alerta",
    "sigma_alerta_inmediata": "los σ de alerta inmediata",
}


class ParametrosIncompletos(ValueError):
    """La planta no tiene definidos (o tiene mal) sus parámetros de cálculo."""


def parametros_planta(sesion: Session, planta_id: int, requeridos: list[str] | None = None) -> dict:
    """Devuelve los parámetros de cálculo de la planta (todos, o solo los `requeridos`).

    Unidades: blancos en mL; umbral en L/h; factores sin unidad (fórmula de la técnica Scott:
    resultado = (mL − blanco) × N × factor / g); hora como datetime.time.
    Da ParametrosIncompletos si falta alguno, si un blanco es negativo o si un
    factor o el umbral no es mayor que 0.
    """
    planta = sesion.get(Planta, planta_id)
    if planta is None:
        raise ValueError(f"No existe la planta con id {planta_id}.")
    return validar_parametros(planta, requeridos)


def validar_parametros(planta: Planta, requeridos: list[str] | None = None) -> dict:
    """Igual que parametros_planta(), pero con la planta ya leída de la base.

    requeridos: nombres de los parámetros que necesita el cálculo (por ejemplo
    ["hora_inicio_dia"]). Si es None, se exigen todos.
    """
    requeridos = list(DESCRIPCION_PARAMETROS) if requeridos is None else requeridos
    desconocidos = [p for p in requeridos if p not in DESCRIPCION_PARAMETROS]
    if desconocidos:
        raise ValueError(f"Parámetro desconocido: {', '.join(desconocidos)}.")
    valores = {parametro: getattr(planta, parametro) for parametro in requeridos}

    faltan = [DESCRIPCION_PARAMETROS[p] for p, valor in valores.items() if valor is None]
    if faltan:
        definido = "definido" if len(faltan) == 1 else "definidos"
        completar = "Completalo" if len(faltan) == 1 else "Completalos"
        raise ParametrosIncompletos(
            f'La planta "{planta.nombre}" no tiene {definido} {_enumerar(faltan)}. {completar} '
            "en la hoja Planta de data/maestros_clientes.xlsx y volvé a importar.")

    for blanco in ("blanco_ml", "blanco_centrifugas_ml"):
        if valores.get(blanco, 0) < 0:
            raise ParametrosIncompletos(f'La planta "{planta.nombre}" tiene {DESCRIPCION_PARAMETROS[blanco]} '
                                        f"negativo ({valores[blanco]} mL).")
    for factor in ("factor_aceite_fruta", "factor_aceite_emulsion", "umbral_paralelo_lh", "turnos_fin_episodio",
                   "turnos_para_alertar", "sigma_alerta_inmediata"):
        if factor in valores and valores[factor] <= 0:
            raise ParametrosIncompletos(f'La planta "{planta.nombre}" tiene '
                                        f"{DESCRIPCION_PARAMETROS[factor]} en {valores[factor]}: "
                                        "tiene que ser mayor que 0.")
    return valores


def _enumerar(elementos: list[str]) -> str:
    """["a", "b", "c"] → "a, b y c"."""
    if len(elementos) == 1:
        return elementos[0]
    return ", ".join(elementos[:-1]) + " y " + elementos[-1]
