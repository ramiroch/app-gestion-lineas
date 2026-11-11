"""Precios para pasar a USD el aceite y el jugo concentrado.

Se guardan en data/precios.json (no se sube a GitHub) con la fecha de la última modificación. Cada
precio se carga en USD/kg o en USD/MT; internamente todo se pasa a USD/kg (USD/MT ÷ 1000).
El jugo concentrado ya viene a 65 °Brix (es el dato que carga la planta): se valoriza sin conversión.
"""

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

RUTA_PRECIOS = Path(__file__).resolve().parent.parent / "data" / "precios.json"
UNIDADES = {"USD/kg": 1.0, "USD/MT": 1 / 1000}      # unidad → factor para pasar a USD/kg
SIN_PRECIOS = "Cargá los precios para ver los valores en USD."


@dataclass(frozen=True)
class Precios:
    """Precios cargados por el usuario. aceite y jugo (concentrado a 65 °Brix) en su unidad; vacíos si no se cargaron."""
    aceite: float | None = None
    unidad_aceite: str = "USD/kg"
    jugo: float | None = None
    unidad_jugo: str = "USD/kg"
    modificado: str | None = None          # fecha y hora de la última modificación (texto ISO)

    @property
    def aceite_usd_kg(self) -> float | None:
        return a_usd_kg(self.aceite, self.unidad_aceite)

    @property
    def jugo_usd_kg(self) -> float | None:
        return a_usd_kg(self.jugo, self.unidad_jugo)

    @property
    def cargados(self) -> bool:
        """True si están los dos precios."""
        return self.aceite is not None and self.jugo is not None


def a_usd_kg(valor: float | None, unidad: str) -> float | None:
    """Precio en USD/kg: USD/kg queda igual; USD/MT ÷ 1000. Vacío si no hay precio."""
    if unidad not in UNIDADES:
        raise ValueError(f"Unidad de precio desconocida: {unidad}. Las válidas son: {', '.join(UNIDADES)}.")
    return None if valor is None else valor * UNIDADES[unidad]


def validar(precios: Precios) -> None:
    """Error claro si un precio no es positivo o una unidad no existe."""
    for nombre, valor, unidad in (("del aceite", precios.aceite, precios.unidad_aceite),
                                  ("del jugo concentrado", precios.jugo, precios.unidad_jugo)):
        a_usd_kg(valor, unidad)
        if valor is not None and valor <= 0:
            raise ValueError(f"El precio {nombre} tiene que ser mayor que 0 (dice {valor}).")


def leer_precios(ruta: Path | None = None) -> Precios:
    """Los precios guardados (en RUTA_PRECIOS si no se da otra ruta); Precios() vacío si todavía no hay archivo.
    Los campos que ya no existen (como el viejo brix_registrado) se ignoran."""
    ruta = ruta or RUTA_PRECIOS
    if not ruta.exists():
        return Precios()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return Precios(**{campo: datos[campo] for campo in Precios.__dataclass_fields__ if campo in datos})


def guardar_precios(precios: Precios, ruta: Path | None = None, ahora: datetime | None = None) -> Precios:
    """Valida y guarda los precios (en RUTA_PRECIOS si no se da otra ruta) con la fecha de modificación (ahora).
    Devuelve lo que guardó."""
    ruta = ruta or RUTA_PRECIOS
    validar(precios)
    guardados = replace(precios, modificado=(ahora or datetime.now()).isoformat(timespec="minutes"))
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_text(json.dumps(asdict(guardados), ensure_ascii=False, indent=2), encoding="utf-8")
    return guardados


def usd(kg, precio_usd_kg: float | None):
    """Valor en USD = kg × precio (USD/kg); vacío si no hay precio."""
    return None if precio_usd_kg is None else kg * precio_usd_kg
