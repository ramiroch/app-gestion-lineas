"""Cálculos del análisis Scott y del eBOE, a partir de los datos crudos.

Fuente: técnica Scott y manual BOE de Brown; factores de cada planta
(LEDESMA: 68,8416 para fruta y 3,45 para emulsión, ver docs/mapeo_ledesma.md).
Los factores se leen de la planta con validar_parametros(): si faltan, error claro.
"""

from core.models import Planta
from core.parametros import validar_parametros


def _validar_titulacion(vol_ml: float, blanco_ml: float, normalidad: float, peso_muestra_g: float) -> None:
    """Controla los datos crudos de una titulación Scott."""
    if peso_muestra_g is None or peso_muestra_g <= 0:
        raise ValueError(f"El peso de la muestra tiene que ser mayor que 0 g (dice {peso_muestra_g}).")
    if normalidad is None or normalidad <= 0:
        raise ValueError(f"La normalidad tiene que ser mayor que 0 (dice {normalidad}).")
    if vol_ml is None or vol_ml < 0:
        raise ValueError(f"El volumen gastado no puede faltar ni ser negativo (dice {vol_ml}).")
    if blanco_ml is None or blanco_ml < 0:
        raise ValueError(f"El blanco no puede faltar ni ser negativo (dice {blanco_ml}).")


def aceite_en_fruta(planta: Planta, vol_ml: float, blanco_ml: float, normalidad: float,
                    peso_muestra_g: float) -> float:
    """Aceite en fruta en kg/MT.

    Fórmula: (vol_ml − blanco_ml) × normalidad × factor_aceite_fruta / peso_muestra_g.
    vol_ml y blanco_ml en mL de titulante; normalidad del BrO3; peso de la muestra en g.
    """
    _validar_titulacion(vol_ml, blanco_ml, normalidad, peso_muestra_g)
    factor = validar_parametros(planta, ["factor_aceite_fruta"])["factor_aceite_fruta"]
    return (vol_ml - blanco_ml) * normalidad * factor / peso_muestra_g


def aceite_en_emulsion(planta: Planta, vol_ml: float, blanco_ml: float, normalidad: float,
                       peso_muestra_g: float) -> float:
    """Aceite en emulsión (licor) en %.

    Fórmula: (vol_ml − blanco_ml) × normalidad × factor_aceite_emulsion / peso_muestra_g.
    """
    _validar_titulacion(vol_ml, blanco_ml, normalidad, peso_muestra_g)
    factor = validar_parametros(planta, ["factor_aceite_emulsion"])["factor_aceite_emulsion"]
    return (vol_ml - blanco_ml) * normalidad * factor / peso_muestra_g


def eficiencia_eboe(aceite_entrada: float, aceite_salida: float) -> float:
    """Eficiencia del eBOE como fracción (0,85 = 85 %).

    Fórmula: 1 − aceite_salida / aceite_entrada, con aceite en fruta (kg/MT) de la
    entrada y la salida del mismo eBOE y la misma hora. Es la remoción del BOE del manual Brown.
    """
    if aceite_entrada is None or aceite_entrada <= 0:
        raise ValueError(f"El aceite en fruta de entrada tiene que ser mayor que 0 (dice {aceite_entrada}).")
    if aceite_salida is None:
        raise ValueError("Falta el aceite en fruta de salida.")
    return 1 - aceite_salida / aceite_entrada


def rendimiento_eboe(aceite_entrada: float, aceite_salida: float) -> float:
    """Rendimiento del eBOE en kg/MT: aceite_entrada − aceite_salida (aceite en fruta, kg/MT)."""
    if aceite_entrada is None or aceite_salida is None:
        raise ValueError("Faltan el aceite en fruta de entrada o el de salida.")
    return aceite_entrada - aceite_salida
