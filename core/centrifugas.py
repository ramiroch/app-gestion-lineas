"""Cálculos de las centrífugas de aceite: aceite %, densidad, balance de la centrífuga y pérdidas.

Fuente: planilla de control de centrífugas de la planta (hoja Datos), con dos correcciones físicas
(ver docs/mapeo_ledesma.md, sección 10):
- Pérdida en descarga: la planilla hace x × ρ × t (t = segundos entre descargas, como si fuera un
  caudal). Acá se usa el caudal de descarga = volumen por descarga × 3600 / t. Con t = 500 s y 16 L
  la planilla da unas 4 veces más pérdida.
- Caudal de fase pesada (balance de masa total): la planilla multiplica el término de la descarga
  por (x descarga − x pesada), un factor que solo va en el balance de aceite. Acá no se multiplica:
  la fase pesada da ~0,6 % menos.
- El volumen por descarga es del equipo (Equipo.volumen_descarga_l), no 16 L fijo. Si falta, el
  balance y la pérdida en descarga quedan "a definir" (None): no se usa ningún valor por defecto.

Convención: x = aceite en fracción (0,0063 = 0,63 %); los caudales en L/h; las densidades en kg/L.
"""

from typing import NamedTuple

DENSIDAD_ACEITE = 0.852        # kg/L, aceite esencial
DENSIDAD_ACUOSA = 0.997        # kg/L, fase acuosa
SEGUNDOS_POR_HORA = 3600
TIPOS_CENTRIFUGA = ("Centrífuga primaria", "Centrífuga secundaria", "Pulidora")
A_DEFINIR = "a definir"


class Eficiencia(NamedTuple):
    """Eficiencia de una centrífuga en %, y si está fuera del rango físico (menor que 0 o mayor que 100)."""

    pct: float
    fuera_de_rango_fisico: bool


def aceite_pct(ml: float, blanco: float, normalidad: float, gramos: float, factor: float) -> float:
    """Aceite en la muestra (%) por titulación Scott.

    Fórmula: (ml − blanco) × normalidad × factor / gramos. El factor y el blanco son de la planta
    (factor_aceite_emulsion y blanco_centrifugas_ml): no tienen valor por defecto.
    """
    if gramos is None or gramos <= 0:
        raise ValueError(f"El peso de la muestra tiene que ser mayor que 0 g (dice {gramos}).")
    if normalidad is None or normalidad <= 0:
        raise ValueError(f"La normalidad tiene que ser mayor que 0 (dice {normalidad}).")
    if ml is None or ml < 0:
        raise ValueError(f"El volumen gastado no puede faltar ni ser negativo (dice {ml}).")
    if blanco is None or blanco < 0:
        raise ValueError(f"El blanco no puede faltar ni ser negativo (dice {blanco}).")
    if factor is None or factor <= 0:
        raise ValueError(f"El factor tiene que ser mayor que 0 (dice {factor}).")
    return (ml - blanco) * normalidad * factor / gramos


def densidad(x: float) -> float:
    """Densidad de una mezcla de aceite y fase acuosa (kg/L): 0,852 · x + 0,997 · (1 − x), x en fracción."""
    _validar_fraccion(x)
    return DENSIDAD_ACEITE * x + DENSIDAD_ACUOSA * (1 - x)


def caudal_descarga_lh(volumen_descarga_l: float | None, t_descarga_s: float) -> float | None:
    """Caudal medio de las descargas (L/h) = volumen por descarga × 3600 / t.

    t_descarga_s: segundos entre descargas. Devuelve None ("a definir") si el equipo no tiene volumen.
    """
    if volumen_descarga_l is None:
        return None
    if t_descarga_s is None or t_descarga_s <= 0:
        raise ValueError(f"El tiempo entre descargas tiene que ser mayor que 0 s (dice {t_descarga_s}).")
    if volumen_descarga_l <= 0:
        raise ValueError(f"El volumen por descarga tiene que ser mayor que 0 L (dice {volumen_descarga_l}).")
    return volumen_descarga_l * SEGUNDOS_POR_HORA / t_descarga_s


def caudal_fase_liviana(caudal_emulsion: float, x_emulsion: float, x_liviana: float, x_pesada: float,
                        x_descarga: float, caudal_descarga: float | None) -> float | None:
    """Caudal de fase liviana (L/h), despejado del balance de aceite y del de masa.

    M4 = [M3·ρ3·(x3 − x5) − Qd·ρ6·(x6 − x5)] / [ρ4·(x4 − x5)]
    3 = emulsión (alimentación), 4 = fase liviana, 5 = fase pesada, 6 = descarga, Qd = caudal de
    descarga. Igual que la planilla, pero con el Qd del equipo. None si Qd está "a definir".
    """
    if caudal_descarga is None:
        return None
    _validar_caudal(caudal_emulsion)
    if x_liviana == x_pesada:
        raise ValueError("La fase liviana y la pesada tienen el mismo aceite: el balance no se puede resolver.")
    return ((caudal_emulsion * densidad(x_emulsion) * (x_emulsion - x_pesada)
             - caudal_descarga * densidad(x_descarga) * (x_descarga - x_pesada))
            / (densidad(x_liviana) * (x_liviana - x_pesada)))


def caudal_fase_pesada(caudal_emulsion: float, x_emulsion: float, caudal_liviana: float | None, x_liviana: float,
                       x_pesada: float, x_descarga: float, caudal_descarga: float | None) -> float | None:
    """Caudal de fase pesada (L/h), del balance de masa total.

    M5 = (M3·ρ3 − M4·ρ4 − Qd·ρ6) / ρ5. A diferencia de la planilla, el término de la descarga no se
    multiplica por (x6 − x5). None si Qd (o el caudal de fase liviana) está "a definir".
    """
    if caudal_descarga is None or caudal_liviana is None:
        return None
    _validar_caudal(caudal_emulsion)
    return ((caudal_emulsion * densidad(x_emulsion) - caudal_liviana * densidad(x_liviana)
             - caudal_descarga * densidad(x_descarga)) / densidad(x_pesada))


def eficiencia_primaria(caudal_emulsion: float, x_emulsion: float, caudal_liviana: float | None,
                        x_liviana: float) -> Eficiencia | None:
    """Eficiencia de recuperación de aceite (%): aceite que sale en la fase liviana / aceite que entra.

    Eficiencia = 100 · M4·ρ4·x4 / (M3·ρ3·x3). Sirve también para la secundaria (su alimentación es la
    fase liviana de las primarias). Si da menor que 0 o mayor que 100 se devuelve igual, marcada como
    fuera del rango físico. None si el caudal de fase liviana está "a definir".
    """
    if caudal_liviana is None:
        return None
    _validar_caudal(caudal_emulsion)
    if x_emulsion <= 0:
        raise ValueError("La emulsión no tiene aceite: la eficiencia no se puede calcular.")
    pct = 100 * caudal_liviana * densidad(x_liviana) * x_liviana / (caudal_emulsion * densidad(x_emulsion) * x_emulsion)
    return Eficiencia(pct, not 0 <= pct <= 100)


def aceite_perdido_kgh(caudal_lh: float | None, x: float) -> float | None:
    """Aceite que se va con una corriente (kg/h) = x · ρ(x) · caudal.

    Se usa con la fase pesada y con la descarga (caudal = caudal_descarga_lh). None si el caudal está
    "a definir".
    """
    if caudal_lh is None:
        return None
    return x * densidad(x) * caudal_lh


def alimentacion_secundaria(caudales_liviana: list[float], aceites_pct: list[float]) -> tuple[float, float]:
    """Alimentación de la secundaria: la suma de las fases livianas de las primarias en marcha.

    Devuelve (caudal L/h, aceite %), con el aceite ponderado por caudal: Σ(M·x) / ΣM. Igual que la
    planilla (columnas Y y AB de la fila de emulsión).
    """
    if not caudales_liviana or len(caudales_liviana) != len(aceites_pct):
        raise ValueError("Hace falta un aceite % por cada caudal de fase liviana.")
    total = sum(caudales_liviana)
    if total <= 0:
        raise ValueError(f"La suma de los caudales de fase liviana tiene que ser mayor que 0 (da {total}).")
    return total, sum(m * x for m, x in zip(caudales_liviana, aceites_pct)) / total


def balance_centrifuga(*, caudal_emulsion: float, aceite_emulsion_pct: float, aceite_liviana_pct: float,
                       aceite_pesada_pct: float, aceite_descarga_pct: float, volumen_descarga_l: float | None,
                       t_descarga_s: float) -> dict:
    """Balance completo de una centrífuga a partir de un muestreo (aceites en %, como en la planilla).

    El * obliga a pasar todo con nombre (balance_centrifuga(caudal_emulsion=..., ...)): son 5 números
    parecidos y el orden se confunde fácil.

    Devuelve caudal_descarga, caudal_liviana y caudal_pesada (L/h), eficiencia (Eficiencia) y
    perdida_pesada_kgh y perdida_descarga_kgh. Sin volumen por descarga, todo queda en None ("a definir").
    """
    x3, x4, x5, x6 = (p / 100 for p in (aceite_emulsion_pct, aceite_liviana_pct, aceite_pesada_pct,
                                        aceite_descarga_pct))
    descarga = caudal_descarga_lh(volumen_descarga_l, t_descarga_s)
    liviana = caudal_fase_liviana(caudal_emulsion, x3, x4, x5, x6, descarga)
    pesada = caudal_fase_pesada(caudal_emulsion, x3, liviana, x4, x5, x6, descarga)
    return {"caudal_descarga": descarga, "caudal_liviana": liviana, "caudal_pesada": pesada,
            "eficiencia": eficiencia_primaria(caudal_emulsion, x3, liviana, x4),
            "perdida_pesada_kgh": aceite_perdido_kgh(pesada, x5),
            "perdida_descarga_kgh": aceite_perdido_kgh(descarga, x6)}


def texto_volumen(tipo: str, volumen_descarga_l: float | None) -> str:
    """Volumen por descarga para mostrar: "16 L", "a definir" en una centrífuga sin dato, "" en otros equipos."""
    if volumen_descarga_l is not None:
        return f"{volumen_descarga_l:g} L".replace(".", ",")
    return A_DEFINIR if tipo in TIPOS_CENTRIFUGA else ""


def _validar_fraccion(x: float) -> None:
    if x is None or not 0 <= x <= 1:
        raise ValueError(f"El aceite tiene que ser una fracción entre 0 y 1 (dice {x}).")


def _validar_caudal(caudal: float) -> None:
    if caudal is None or caudal <= 0:
        raise ValueError(f"El caudal de emulsión tiene que ser mayor que 0 L/h (dice {caudal}).")
