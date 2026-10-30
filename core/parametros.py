"""Parámetros de cálculo de una planta (blanco, factores y hora de inicio del día).

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
}


class ParametrosIncompletos(ValueError):
    """La planta no tiene definidos (o tiene mal) sus parámetros de cálculo."""


def parametros_planta(sesion: Session, planta_id: int) -> dict:
    """Devuelve {blanco_ml, factor_aceite_fruta, factor_aceite_emulsion, hora_inicio_dia}.

    Unidades: blanco en mL; factores sin unidad (fórmula de la técnica Scott:
    resultado = (mL − blanco) × N × factor / g); hora como datetime.time.
    Da ParametrosIncompletos si falta alguno, si el blanco es negativo o si un
    factor no es mayor que 0.
    """
    planta = sesion.get(Planta, planta_id)
    if planta is None:
        raise ValueError(f"No existe la planta con id {planta_id}.")
    valores = {parametro: getattr(planta, parametro) for parametro in DESCRIPCION_PARAMETROS}

    faltan = [DESCRIPCION_PARAMETROS[p] for p, valor in valores.items() if valor is None]
    if faltan:
        definido = "definido" if len(faltan) == 1 else "definidos"
        completar = "Completalo" if len(faltan) == 1 else "Completalos"
        raise ParametrosIncompletos(
            f'La planta "{planta.nombre}" no tiene {definido} {_enumerar(faltan)}. {completar} '
            "en la hoja Planta de data/maestros_clientes.xlsx y volvé a importar.")

    if valores["blanco_ml"] < 0:
        raise ParametrosIncompletos(
            f'La planta "{planta.nombre}" tiene el blanco negativo ({valores["blanco_ml"]} mL).')
    for factor in ("factor_aceite_fruta", "factor_aceite_emulsion"):
        if valores[factor] <= 0:
            raise ParametrosIncompletos(f'La planta "{planta.nombre}" tiene '
                                        f"{DESCRIPCION_PARAMETROS[factor]} en {valores[factor]}: "
                                        "tiene que ser mayor que 0.")
    return valores


def _enumerar(elementos: list[str]) -> str:
    """["a", "b", "c"] → "a, b y c"."""
    if len(elementos) == 1:
        return elementos[0]
    return ", ".join(elementos[:-1]) + " y " + elementos[-1]
