"""Importa los datos del PLC de los eBOE de LEDESMA: todos los CSV de una carpeta (por defecto data/plc/).

Formato de cada CSV (sin encabezado, separado por comas, un registro cada 5 minutos):
Fecha/Hora y, para cada eBOE del 1 al 3, Horas Maquina, Minutos, Elevador, Programa, TON Seteadas y
TON Calculadas (19 columnas).
- Fecha/Hora en día/mes/año, con o sin cero adelante, y hora de 1 o 2 dígitos ("6/7/2026 9:05:01").
  La fecha sale de la fila, nunca del nombre del archivo.
- Programa: se sacan los espacios y el texto "Programa" y se guarda como viene ("N3" o "9").
- Elevador: 0 o 1. Horas, minutos y toneladas: números no negativos.
Reglas:
- Una fila con un error se rechaza entera (los tres eBOE) y va al reporte data/reporte_plc.csv.
- Si dos filas traen el mismo eBOE y la misma fecha y hora con datos distintos, no se carga ninguna; si
  son idénticas, se carga una vez.
- Se puede correr muchas veces: lo que ya existe (eBOE + fecha y hora) se actualiza, no se duplica.

Uso (desde la carpeta del proyecto):  python -m core.importar_plc [carpeta]
"""

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.models import Equipo, LecturaPLC

CARPETA_DATOS = Path(__file__).resolve().parent.parent / "data"
CARPETA_PLC = CARPETA_DATOS / "plc"
RUTA_REPORTE = CARPETA_DATOS / "reporte_plc.csv"
CARGADO_POR = "importar_plc"
EQUIPOS = {1: "LED-BOE-01", 2: "LED-BOE-02", 3: "LED-BOE-03"}      # posición en el CSV → código del eBOE
CAMPOS = ["horas_maquina", "minutos", "elevador", "programa", "ton_seteadas", "ton_calculadas"]
N_COLUMNAS = 1 + len(CAMPOS) * len(EQUIPOS)
PATRON_FECHA = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$")


# ---------------------------------------------------------------- lectura

def leer_fecha(texto: str) -> datetime:
    """"6/7/2026 9:05:01" → 6 de julio de 2026, 09:05:01 (día/mes/año; segundos opcionales)."""
    encontrado = PATRON_FECHA.match(texto.strip())
    if not encontrado:
        raise ValueError(f'Fecha/Hora con formato desconocido: "{texto}" (se espera día/mes/año hora:minutos).')
    dia, mes, anio, hora, minuto, segundo = (int(v) if v else 0 for v in encontrado.groups())
    try:
        return datetime(anio, mes, dia, hora, minuto, segundo)
    except ValueError:
        raise ValueError(f'Fecha/Hora imposible: "{texto}".') from None


def limpiar_programa(texto: str) -> str | None:
    """"   Programa N3" → "N3"; "Programa 9" → "9"; vacío → None."""
    limpio = re.sub(r"programa", "", re.sub(r"\s+", "", texto), flags=re.IGNORECASE)
    return limpio or None


def _numero(texto: str, campo: str) -> float | None:
    texto = texto.strip()
    if not texto:
        return None
    try:
        valor = float(texto)
    except ValueError:
        raise ValueError(f'{campo} no es un número: "{texto}".') from None
    if valor < 0:
        raise ValueError(f"{campo} no puede ser negativo (dice {texto}).")
    return valor


def leer_fila(fila: list[str]) -> tuple[datetime, dict[int, dict]]:
    """Una fila del CSV → (fecha y hora, {n.º de eBOE: {campo: valor}}). ValueError con el motivo si falla."""
    if len(fila) != N_COLUMNAS:
        raise ValueError(f"La fila tiene {len(fila)} columnas y se esperan {N_COLUMNAS}.")
    fecha_hora = leer_fecha(fila[0])
    eboes = {}
    for posicion, eboe in enumerate(EQUIPOS):
        valores = fila[1 + posicion * len(CAMPOS): 1 + (posicion + 1) * len(CAMPOS)]
        datos = {}
        for campo, texto in zip(CAMPOS, valores):
            nombre = f"eBOE {eboe}: {campo}"
            datos[campo] = limpiar_programa(texto) if campo == "programa" else _numero(texto, nombre)
        if datos["elevador"] is not None:
            if datos["elevador"] not in (0, 1):
                raise ValueError(f"eBOE {eboe}: elevador tiene que ser 0 o 1 (dice {datos['elevador']:g}).")
            datos["elevador"] = int(datos["elevador"])
        eboes[eboe] = datos
    return fecha_hora, eboes


def leer_carpeta(carpeta: Path) -> tuple[list[dict], list[tuple[str, int, str, str]]]:
    """Lee todos los CSV de la carpeta (en orden de nombre).

    Devuelve (lecturas, reporte). Cada lectura: eboe, fecha_hora, los CAMPOS y origen ("archivo › fila n").
    reporte: (archivo, fila, tipo, motivo), tipo "rechazada" o "aviso". Aplica la regla de las filas
    repetidas (mismo eBOE y fecha y hora).
    """
    archivos = sorted(Path(carpeta).glob("*.csv"))
    if not archivos:
        raise FileNotFoundError(f"No hay archivos CSV en {carpeta}.")
    lecturas, reporte = [], []
    for archivo in archivos:
        with open(archivo, newline="", encoding="utf-8-sig") as entrada:
            for numero, fila in enumerate(csv.reader(entrada), start=1):
                if not any(c.strip() for c in fila):
                    continue
                try:
                    fecha_hora, eboes = leer_fila(fila)
                except ValueError as error:
                    reporte.append((archivo.name, numero, "rechazada", str(error)))
                    continue
                for eboe, datos in eboes.items():
                    lecturas.append({"eboe": eboe, "fecha_hora": fecha_hora, **datos,
                                     "origen": f"{archivo.name} › fila {numero}"})
    return _sin_repetidas(lecturas, reporte)


def _sin_repetidas(lecturas: list[dict], reporte: list) -> tuple[list[dict], list]:
    """Idénticas (mismo eBOE, fecha y hora y datos): se carga la primera. Con datos distintos: ninguna."""
    grupos = defaultdict(list)
    for lectura in lecturas:
        grupos[(lectura["eboe"], lectura["fecha_hora"])].append(lectura)
    para_cargar = []
    for (eboe, fecha_hora), grupo in grupos.items():
        if len(grupo) == 1:
            para_cargar.append(grupo[0])
            continue
        origenes = ", ".join(l["origen"] for l in grupo)
        if len({tuple(l[c] for c in CAMPOS) for l in grupo}) == 1:
            para_cargar.append(grupo[0])
            for repetida in grupo[1:]:
                archivo, fila = _archivo_y_fila(repetida["origen"])
                reporte.append((archivo, fila, "aviso", f"eBOE {eboe} {fecha_hora:%d/%m/%Y %H:%M:%S}: idéntica a "
                                                        f"{grupo[0]['origen']}; se carga una sola vez."))
        else:
            for repetida in grupo:
                archivo, fila = _archivo_y_fila(repetida["origen"])
                reporte.append((archivo, fila, "rechazada", f"eBOE {eboe} {fecha_hora:%d/%m/%Y %H:%M:%S} repetido con "
                                                            f"datos distintos ({origenes}): no se carga ninguna."))
    return para_cargar, reporte


def _archivo_y_fila(origen: str) -> tuple[str, int]:
    archivo, fila = origen.split(" › fila ")
    return archivo, int(fila)


# ---------------------------------------------------------------- carga

def importar_plc(sesion: Session, carpeta: Path = CARPETA_PLC) -> tuple[dict, list]:
    """Carga las lecturas de la carpeta en LecturaPLC (inserta las nuevas y actualiza las que cambiaron).

    Devuelve (resumen, reporte). resumen: nuevas, actualizadas, sin_cambios, rechazadas, avisos y por_eboe
    ({n.º: {"filas", "desde", "hasta"}}, de lo que quedó cargado en la base para ese eBOE).
    """
    equipos = {numero: sesion.scalars(select(Equipo).where(Equipo.codigo == codigo)).one_or_none()
               for numero, codigo in EQUIPOS.items()}
    faltan = [EQUIPOS[n] for n, e in equipos.items() if e is None]
    if faltan:
        raise ValueError(f"Faltan los equipos {', '.join(faltan)} en los datos maestros: "
                         "cargalos con python -m core.importar_maestros.")
    lecturas, reporte = leer_carpeta(carpeta)

    ids = [e.id for e in equipos.values()]
    existentes = {(fila.equipo_id, fila.fecha_hora): fila for fila in sesion.execute(
        select(LecturaPLC.id, LecturaPLC.equipo_id, LecturaPLC.fecha_hora, *(getattr(LecturaPLC, c) for c in CAMPOS))
        .where(LecturaPLC.equipo_id.in_(ids)))}
    nuevas, cambios, sin_cambios = [], [], 0
    for lectura in lecturas:
        equipo_id = equipos[lectura["eboe"]].id
        datos = {c: lectura[c] for c in CAMPOS} | {"origen": lectura["origen"]}
        anterior = existentes.get((equipo_id, lectura["fecha_hora"]))
        if anterior is None:
            nuevas.append({"equipo_id": equipo_id, "fecha_hora": lectura["fecha_hora"], "cargado_por": CARGADO_POR,
                           "cargado_en": datetime.now(), **datos})
        elif any(getattr(anterior, c) != lectura[c] for c in CAMPOS):
            cambios.append({"id": anterior.id, **datos})
        else:
            sin_cambios += 1
    if nuevas:
        sesion.execute(insert(LecturaPLC), nuevas)
    if cambios:
        sesion.execute(update(LecturaPLC), cambios)
    sesion.commit()

    por_eboe = {}
    for numero, equipo in equipos.items():
        fechas = sesion.scalars(select(LecturaPLC.fecha_hora).where(LecturaPLC.equipo_id == equipo.id)).all()
        por_eboe[numero] = {"filas": len(fechas), "desde": min(fechas, default=None), "hasta": max(fechas, default=None)}
    resumen = {"nuevas": len(nuevas), "actualizadas": len(cambios), "sin_cambios": sin_cambios,
               "rechazadas": sum(1 for f in reporte if f[2] == "rechazada"),
               "avisos": sum(1 for f in reporte if f[2] == "aviso"), "por_eboe": por_eboe}
    return resumen, reporte


def guardar_reporte(reporte: list, ruta: Path = RUTA_REPORTE) -> Path:
    """CSV con separador ";" (se abre directo en Excel): archivo, fila, tipo y motivo."""
    ruta = Path(ruta)
    ruta.parent.mkdir(exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8-sig") as salida:
        escritor = csv.writer(salida, delimiter=";")
        escritor.writerow(["archivo", "fila", "tipo", "motivo"])
        escritor.writerows(sorted(reporte, key=lambda f: (f[0], f[1])))
    return ruta


def texto_resumen(resumen: dict, ruta_reporte: Path | None = None) -> str:
    """Resumen para la consola: lecturas nuevas, actualizadas, rechazadas y filas y fechas por eBOE."""
    lineas = [f"Lecturas nuevas: {resumen['nuevas']} · actualizadas: {resumen['actualizadas']} · sin cambios: "
              f"{resumen['sin_cambios']}",
              f"Rechazadas: {resumen['rechazadas']} · avisos: {resumen['avisos']}"]
    for numero, datos in resumen["por_eboe"].items():
        rango = (f"del {datos['desde']:%d/%m/%Y %H:%M} al {datos['hasta']:%d/%m/%Y %H:%M}" if datos["filas"]
                 else "sin datos")
        lineas.append(f"eBOE {numero}: {datos['filas']} lecturas en la base, {rango}")
    if ruta_reporte:
        lineas.append(f"Reporte: {ruta_reporte}")
    return "\n".join(lineas)


if __name__ == "__main__":
    carpeta = Path(sys.argv[1]) if len(sys.argv) > 1 else CARPETA_PLC
    motor = crear_motor()
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        resumen, reporte = importar_plc(sesion, carpeta)
    print(texto_resumen(resumen, guardar_reporte(reporte)))
