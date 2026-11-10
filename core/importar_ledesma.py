"""Importa el histórico de LEDESMA: data/historico/eficiencia eBOE Naranja 25 2.xlsm.

Hojas que se leen (detalle en docs/mapeo_ledesma.md, secciones 2 a 5):
- BOE: cada fila de Entrada carga el análisis de fruta entrada, el de emulsión y la
  lectura de operación; cada fila de Salida carga el análisis de fruta salida.
- Resumen (solo la tabla por turno, columnas A:V): RegistroProduccion. Nunca se importan
  los promedios: se recalculan desde la hoja BOE.

Reglas:
- Se guardan solo datos crudos; nada calculado.
- La columna Fecha de BOE es la fecha de producción: las muestras de la madrugada
  (antes de la hora de inicio del día) se tomaron el día siguiente. Las filas que traen
  la fecha del calendario se detectan por contexto (resolver_fechas).
- Un PROCESO inválido (fecha pegada, número > 1000) se corrige con el de la fila
  anterior si el Día y la Fecha coinciden; si no, se rechaza (resolver_procesos).
- Se puede correr muchas veces: lo que ya existe (por su clave) se actualiza.
- data/reporte_importacion.csv lista cada fila rechazada, corregida o con aviso.

Uso (desde la carpeta del proyecto):  python -m core.importar_ledesma
"""

import csv
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.duplicados import separar_repetidas
from core.models import (AnalisisScott, Cliente, LecturaOperacion, Planta, Proceso, RegistroProduccion,
                         texto_origen)
from core.parametros import validar_parametros
from core.turnos import fecha_produccion

CARPETA_DATOS = Path(__file__).resolve().parent.parent / "data"
RUTA_EXCEL = CARPETA_DATOS / "historico" / "eficiencia eBOE Naranja 25 2.xlsm"
RUTA_REPORTE = CARPETA_DATOS / "reporte_importacion.csv"
CARGADO_POR = "importar_ledesma"
FILA_ENCABEZADO = 3          # en BOE y Resumen el encabezado está en la fila 3
COLUMNAS_TABLA_TURNO = 22    # Resumen: la tabla por turno ocupa A:V
PROCESO_MAXIMO = 1000        # un PROCESO mayor es un error de tipeo (por ejemplo una fecha)

# Encabezado del Excel (sin saltos de línea) → nombre interno.
COLUMNAS_BOE = {
    "PROCESO": "proceso", "Día": "dia", "Especie": "fruta", "Fecha": "fecha", "Hora": "hora",
    "Químico": "analista", "Operador": "operador", "eBOE": "eboe", "pH": "ph", "°Brix": "brix",
    "Muestra [g]": "emulsion_g", "Vol [ml]": "emulsion_ml", "Sólidos Centrífugables [%]": "solidos",
    "Programa": "programa", "Flip Gate": "flip_gate", "Ton/H": "tph",
    "Caudal fase pesada [m3/h]": "caudal", "Lugar de muestreo": "lugar",
    "Calidad de fruta": "calidad", "Pinchado": "rallado", "Peso fruta [Kg]": "peso_fruta_kg",
    "Número de frutos": "n_frutos", "Peso muestra [g]": "fruta_g", "Vol [ml]2": "fruta_ml",
    "N BrO3": "normalidad", "N° de Ticket": "ticket", "Licuadora": "licuadora",
}
COLUMNAS_RESUMEN = {
    "PROCESO": "proceso", "Fecha": "fecha", "Turno": "turno",
    "Fruta procesada [Ton]": "fruta_t", "Aceite envasado [kg]": "aceite_envasado_kg",
    "Jugo conc [kg]": "jugo_conc_kg", "Reproc [kg]": "reproceso_kg",
    "QF N°3": "qf3", "Brix Fritz": "brix_fritz",
}
PRODUCCION = ["fruta_t", "aceite_envasado_kg", "jugo_conc_kg", "reproceso_kg", "qf3", "brix_fritz"]
# Datos crudos de BOE que se comparan para decidir si dos filas con la misma clave son iguales.
DATOS_CRUDOS_BOE = ["proceso", "fruta", "analista", "operador", "ph", "brix", "emulsion_g", "emulsion_ml",
                    "solidos", "programa", "flip_gate", "tph", "caudal", "calidad", "rallado",
                    "peso_fruta_kg", "n_frutos", "fruta_g", "fruta_ml", "normalidad", "ticket", "licuadora"]
# Mediciones de operación: sin ninguna de ellas no se crea la lectura (el operador solo no alcanza).
MEDICIONES_OPERACION = ["programa", "flip_gate", "tph", "caudal"]


class ErrorFila(Exception):
    """Problema en una fila. El mensaje es el motivo que va al reporte."""


class Reporte:
    """Filas rechazadas, corregidas o con aviso: (hoja, fila, tipo, motivo)."""

    TIPOS = ("rechazada", "corregida", "aviso")

    def __init__(self):
        self.filas = []

    def agregar(self, hoja: str, fila: int, tipo: str, motivo: str) -> None:
        self.filas.append((hoja, fila, tipo, motivo))

    def cantidad(self, tipo: str) -> int:
        return sum(1 for _, _, t, _ in self.filas if t == tipo)

    def guardar(self, ruta: Path = RUTA_REPORTE) -> Path:
        """CSV con separador ";" para abrirlo directo en Excel."""
        ruta = Path(ruta)
        ruta.parent.mkdir(exist_ok=True)
        with open(ruta, "w", newline="", encoding="utf-8-sig") as archivo:
            escritor = csv.writer(archivo, delimiter=";")
            escritor.writerow(["hoja", "fila", "tipo", "motivo"])
            escritor.writerows(sorted(self.filas, key=lambda f: (f[0], f[1])))
        return ruta


# ---------------------------------------------------------------- lectura

def leer_tabla(hoja, columnas: dict, max_col: int | None = None) -> list[tuple[int, dict]]:
    """Filas con datos de una hoja como (n.º de fila de Excel, {nombre interno: valor})."""
    filas = hoja.iter_rows(min_row=FILA_ENCABEZADO, max_col=max_col, values_only=True)
    encabezado = [" ".join(c.split()) if isinstance(c, str) else c for c in next(filas)]
    faltan = [c for c in columnas if c not in encabezado]
    if faltan:
        raise ValueError(f'A la hoja "{hoja.title}" le faltan las columnas: {", ".join(faltan)}.')
    resultado = []
    for numero, valores in enumerate(filas, start=FILA_ENCABEZADO + 1):
        datos = {columnas[c]: _limpiar(v) for c, v in zip(encabezado, valores) if c in columnas}
        if any(valor is not None for valor in datos.values()):
            resultado.append((numero, datos))
    return resultado


def _limpiar(valor):
    """Recorta los textos; un texto vacío o con solo espacios queda como None."""
    if isinstance(valor, str):
        return valor.strip() or None
    return valor


# ---------------------------------------------------------------- normalización (funciones puras)

def normalizar_hora(valor) -> tuple[time, bool]:
    """Devuelve (hora, corregida). Acepta horas de Excel o textos como "15.10", "21_10", "7.10"."""
    if isinstance(valor, time):
        return valor, False
    if isinstance(valor, datetime):
        return valor.time(), False
    coincide = re.fullmatch(r"(\d{1,2})\s*[.:_,]\s*(\d{2})", str(valor).strip())
    if coincide:
        hora, minutos = int(coincide.group(1)), int(coincide.group(2))
        if hora < 24 and minutos < 60:
            return time(hora, minutos), True
    raise ErrorFila(f'Hora "{valor}" no válida.')


def normalizar_programa(valor) -> tuple[str | None, str | None]:
    """Devuelve (programa N0–N9 o None, nota para el reporte o None si no hubo cambios)."""
    if valor is None:
        return None, None
    if isinstance(valor, (int, float)) and float(valor).is_integer():
        numero = int(valor)
        if 0 <= numero <= 9:
            return f"N{numero}", f'Programa "{valor}" → N{numero}.'
        return None, f'Programa "{valor}" fuera de N0–N9: la lectura se carga sin programa.'
    texto = str(valor).strip().upper()
    if re.fullmatch(r"N[0-9]", texto):
        return texto, (None if texto == valor else f'Programa "{valor}" → {texto}.')
    return None, f'Programa "{valor}" no válido: la lectura se carga sin programa.'


def normalizar_texto(valor: str | None, nombre_propio: bool = False) -> str | None:
    """"firme" → "Firme", "OK" → "Ok", "poco rallada" → "Poco rallada"; con nombre_propio,
    cada palabra en mayúscula ("cuesta" → "Cuesta"). "N Val S7R" → "N Val S/R"."""
    if valor is None:
        return None
    texto = " ".join(str(valor).split()).replace("S7R", "S/R")
    if texto.startswith("N Val"):
        return texto
    if nombre_propio:
        return texto.title()
    return texto[:1].upper() + texto[1:].lower()


def _proceso_valido(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and 1 <= valor <= PROCESO_MAXIMO


def _fecha(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def resolver_procesos(filas: list, reporte: Reporte, hoja: str = "BOE") -> list:
    """Corrige el PROCESO inválido con el de la última fila válida anterior, si el Día y la
    Fecha coinciden; si no, rechaza la fila. Devuelve las filas que siguen."""
    ultima_valida = None   # (proceso, día, fecha)
    resultado = []
    for numero, datos in filas:
        proceso = datos["proceso"]
        if _proceso_valido(proceso):
            ultima_valida = (proceso, datos["dia"], _fecha(datos["fecha"]))
            resultado.append((numero, datos))
        elif ultima_valida and (datos["dia"], _fecha(datos["fecha"])) == ultima_valida[1:]:
            reporte.agregar(hoja, numero, "corregida",
                            f'Proceso corregido por contexto: decía "{proceso}"; se usa el proceso '
                            f"{ultima_valida[0]} de la fila anterior (mismo Día y Fecha).")
            datos["proceso"] = ultima_valida[0]
            resultado.append((numero, datos))
        else:
            reporte.agregar(hoja, numero, "rechazada",
                            f'PROCESO "{proceso}" no válido y no se puede deducir de la fila anterior '
                            "(el Día o la Fecha no coinciden).")
    return resultado


def resolver_fechas(filas: list, planta: Planta, reporte: Reporte, hoja: str = "BOE") -> list:
    """Arma datos["fecha_hora"] (momento real de la muestra).

    En el Excel, la Fecha es la fecha de producción: antes de la hora de inicio del día,
    la muestra es del día siguiente (Fecha + 1). Se compara con la fecha de producción de la
    fila anterior del mismo proceso y día de la misma temporada (los procesos se vuelven a
    numerar cada temporada):
    - igual a la Fecha → regla general (Fecha + 1);
    - un día antes que la Fecha → la fila trae la fecha del calendario: no se suma el día
      ("fecha de calendario corregida");
    - sin fila anterior → regla general, con aviso;
    - otra diferencia → se rechaza (fecha fuera de secuencia).
    """
    inicio = validar_parametros(planta, ["hora_inicio_dia"])["hora_inicio_dia"]
    produccion_anterior = {}   # (temporada, proceso, día) → fecha de producción de la última fila
    resultado = []
    for numero, datos in filas:
        fecha, hora = _fecha(datos["fecha"]), datos["hora"]
        clave = (fecha.year, datos["proceso"], datos["dia"])
        anterior = produccion_anterior.get(clave)
        if hora >= inicio:
            real = datetime.combine(fecha, hora)
        elif anterior is None or anterior == fecha:
            real = datetime.combine(fecha + timedelta(days=1), hora)
            if anterior is None:
                reporte.agregar(hoja, numero, "aviso",
                                f"Madrugada sin filas anteriores del mismo proceso y día para verificar la "
                                f"fecha: se toma {fecha:%d/%m/%Y} como fecha de producción "
                                f"(muestra del {real:%d/%m/%Y %H:%M}).")
        elif anterior == fecha - timedelta(days=1):
            real = datetime.combine(fecha, hora)
            reporte.agregar(hoja, numero, "corregida",
                            f"Fecha de calendario corregida: la fila anterior del mismo proceso y día es del "
                            f"día de producción {anterior:%d/%m/%Y}; la muestra es del {real:%d/%m/%Y %H:%M} "
                            "(no se suma un día).")
        else:
            reporte.agregar(hoja, numero, "rechazada",
                            f"Fecha fuera de secuencia: {fecha:%d/%m/%Y} {hora:%H:%M}, pero la fila anterior "
                            f"del mismo proceso y día es del día de producción {anterior:%d/%m/%Y}.")
            continue
        datos["fecha_hora"] = real
        produccion_anterior[clave] = fecha_produccion(planta, real)
        resultado.append((numero, datos))
    return resultado


# ---------------------------------------------------------------- conversión de números

def _numero(datos: dict, clave: str, notas: list) -> float | None:
    """Número decimal; un número escrito como texto ("10.01") se convierte y se anota."""
    valor = datos.get(clave)
    if valor is None or (isinstance(valor, (int, float)) and not isinstance(valor, bool)):
        return None if valor is None else float(valor)
    try:
        numero = float(str(valor).replace(",", "."))
    except ValueError:
        raise ErrorFila(f'"{clave}" tiene que ser un número (dice "{valor}").')
    notas.append(f'{clave} "{valor}" escrito como texto → {numero}.')
    return numero


def _entero(datos: dict, clave: str, notas: list) -> int | None:
    numero = _numero(datos, clave, notas)
    if numero is None:
        return None
    if not numero.is_integer():
        raise ErrorFila(f'"{clave}" tiene que ser un número entero (dice {numero}).')
    return int(numero)


def _texto(valor) -> str | None:
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return None if valor is None else str(valor)


# ---------------------------------------------------------------- importación

def importar_ledesma(sesion: Session, ruta: Path = RUTA_EXCEL, cliente: str = "LEDESMA",
                     planta: str = "LEDESMA") -> tuple[dict, Reporte]:
    """Carga las hojas BOE y Resumen en la base. Devuelve (resumen por tabla, reporte).

    resumen: {tabla: {"creados": n, "actualizados": n}}. Todo se guarda junto al final:
    si algo falla, no queda nada a medio cargar.
    """
    ruta = Path(ruta)
    la_planta = sesion.scalars(select(Planta).join(Cliente).where(
        Cliente.nombre == cliente, Planta.nombre == planta)).one_or_none()
    if la_planta is None:
        raise ValueError(f'No existe la planta "{planta}" del cliente "{cliente}". '
                         "Importá primero los datos maestros (python -m core.importar_maestros).")
    blanco = validar_parametros(la_planta, ["blanco_ml", "hora_inicio_dia"])["blanco_ml"]
    turnos = {turno.nombre: turno for turno in la_planta.turnos}
    if not turnos:
        raise ValueError(f'La planta "{planta}" no tiene turnos cargados (hoja Turno de los maestros).')

    libro = load_workbook(ruta, data_only=True, read_only=True)
    reporte = Reporte()
    resumen = {tabla: {"creados": 0, "actualizados": 0}
               for tabla in ("Proceso", "AnalisisScott", "LecturaOperacion", "RegistroProduccion")}
    cargador = _Cargador(sesion, la_planta, resumen)

    _importar_boe(libro["BOE"], ruta.name, la_planta, blanco, cargador, reporte)
    _importar_resumen(libro["Resumen"], ruta.name, la_planta, turnos, cargador, reporte)
    sesion.commit()
    return resumen, reporte


class _Cargador:
    """Crea o actualiza registros por su clave, con los existentes ya leídos de la base."""

    def __init__(self, sesion: Session, planta: Planta, resumen: dict):
        self.sesion, self.planta, self.resumen = sesion, planta, resumen
        self.procesos = {(p.temporada, p.numero): p for p in planta.procesos}
        puntos = [p.id for linea in planta.lineas for e in linea.equipos for p in e.puntos_muestreo]
        equipos = [e.id for linea in planta.lineas for e in linea.equipos]
        self.analisis = {(a.punto_muestreo_id, a.fecha_hora): a for a in sesion.scalars(
            select(AnalisisScott).where(AnalisisScott.punto_muestreo_id.in_(puntos)))}
        self.lecturas = {(l.equipo_id, l.fecha_hora): l for l in sesion.scalars(
            select(LecturaOperacion).where(LecturaOperacion.equipo_id.in_(equipos)))}
        self.registros = {(r.fecha_produccion, r.turno_id, r.proceso_id): r
                          for r in planta.registros_produccion}

    def proceso(self, temporada: int, numero: int) -> Proceso:
        """El proceso de la temporada; lo crea si no existe."""
        clave = (temporada, numero)
        if clave not in self.procesos:
            proceso = Proceso(planta_id=self.planta.id, temporada=temporada, numero=numero)
            self.sesion.add(proceso)
            self.sesion.flush()   # para tener su id
            self.procesos[clave] = proceso
            self.resumen["Proceso"]["creados"] += 1
        return self.procesos[clave]

    def guardar(self, tabla: str, existentes: dict, clave: tuple, modelo, campos: dict) -> None:
        """Crea o actualiza. Arma el registro nuevo primero, así los validadores del modelo
        controlan los datos antes de tocar uno existente (si fallan, dan ValueError)."""
        nuevo = modelo(**campos)
        existente = existentes.get(clave)
        if existente is None:
            self.sesion.add(nuevo)
            existentes[clave] = nuevo
            self.resumen[tabla]["creados"] += 1
        else:
            for campo, valor in campos.items():
                setattr(existente, campo, valor)
            self.resumen[tabla]["actualizados"] += 1


def _puntos_por_eboe(planta: Planta) -> dict:
    """{número de eBOE: {"Entrada": punto, "Salida": punto, "emulsión": punto, "equipo": equipo}}."""
    mapa = {}
    for linea in planta.lineas:
        for equipo in linea.equipos:
            if equipo.tipo != "BOE":
                continue
            puntos = {p.tipo: p for p in equipo.puntos_muestreo}
            mapa[equipo.numero] = {"equipo": equipo, "Entrada": puntos.get("fruta entrada"),
                                   "Salida": puntos.get("fruta salida"), "emulsión": puntos.get("emulsión")}
    return mapa


def _importar_boe(hoja, archivo: str, planta: Planta, blanco: float, cargador: _Cargador,
                  reporte: Reporte) -> None:
    filas = []
    for numero, datos in leer_tabla(hoja, COLUMNAS_BOE):
        faltan = [c for c in ("fecha", "hora", "dia", "analista", "normalidad") if datos.get(c) is None]
        if faltan:
            reporte.agregar("BOE", numero, "rechazada", f"Fila sin {', '.join(faltan)}: no se carga.")
            continue
        if _fecha(datos["fecha"]) is None:
            reporte.agregar("BOE", numero, "rechazada", f'Fecha "{datos["fecha"]}" no válida.')
            continue
        try:
            datos["hora"], corregida = normalizar_hora(datos["hora"])
        except ErrorFila as error:
            reporte.agregar("BOE", numero, "rechazada", str(error))
            continue
        if corregida:
            reporte.agregar("BOE", numero, "corregida", f"Hora escrita como texto → {datos['hora']:%H:%M}.")
        filas.append((numero, datos))

    filas = resolver_procesos(filas, reporte)
    filas = resolver_fechas(filas, planta, reporte)
    filas = _normalizar_textos_boe(filas, reporte)
    filas, errores, avisos = separar_repetidas("BOE", filas, ["eboe", "lugar", "fecha_hora"], DATOS_CRUDOS_BOE)
    for hoja_, fila, motivo in errores:
        reporte.agregar(hoja_, fila, "rechazada", motivo)
    for hoja_, fila, motivo in avisos:
        reporte.agregar(hoja_, fila, "aviso", motivo)
    _avisar_entradas_sin_salida(filas, reporte)

    puntos = _puntos_por_eboe(planta)
    for numero, datos in filas:
        try:
            _cargar_fila_boe(numero, datos, archivo, blanco, puntos, cargador, reporte)
        except ErrorFila as error:
            reporte.agregar("BOE", numero, "rechazada", str(error))


def _avisar_entradas_sin_salida(filas: list, reporte: Reporte) -> None:
    """Cada Entrada tiene que estar seguida por la Salida del mismo eBOE y la misma hora: si no,
    no se va a poder calcular la eficiencia de ese par (se carga igual, con aviso)."""
    for (numero, datos), (_, siguiente) in zip(filas, filas[1:] + [(None, {})]):
        if datos.get("lugar") != "Entrada":
            continue
        if (siguiente.get("lugar"), siguiente.get("eboe"), siguiente.get("fecha_hora")) != (
                "Salida", datos.get("eboe"), datos.get("fecha_hora")):
            hora_salida = siguiente.get("fecha_hora")
            detalle = f" (la fila siguiente es de las {hora_salida:%H:%M})" if hora_salida else ""
            reporte.agregar("BOE", numero, "aviso",
                            f"Entrada sin la Salida del mismo eBOE y la misma hora en la fila siguiente{detalle}: "
                            "no se va a poder calcular la eficiencia de este par.")


def _normalizar_textos_boe(filas: list, reporte: Reporte) -> list:
    for numero, datos in filas:
        cambios = []
        for clave, nombre_propio in (("fruta", False), ("analista", True), ("operador", True),
                                     ("calidad", False), ("rallado", False)):
            original = datos.get(clave)
            normalizado = normalizar_texto(original, nombre_propio)
            if normalizado != original:
                cambios.append(f'{clave} "{original}" → "{normalizado}"')
                datos[clave] = normalizado
        datos["programa"], nota = normalizar_programa(datos.get("programa"))
        if nota:
            cambios.append(nota.rstrip("."))
        if cambios:
            reporte.agregar("BOE", numero, "corregida", "Texto normalizado: " + "; ".join(cambios) + ".")
    return filas


def _cargar_fila_boe(numero: int, datos: dict, archivo: str, blanco: float, puntos: dict,
                     cargador: _Cargador, reporte: Reporte) -> None:
    eboe = puntos.get(datos["eboe"])
    if eboe is None:
        raise ErrorFila(f'El eBOE "{datos["eboe"]}" no existe en los datos maestros de la planta.')
    lugar = datos.get("lugar")
    if lugar not in ("Entrada", "Salida"):
        raise ErrorFila(f'Lugar de muestreo "{lugar}" no válido (tiene que ser Entrada o Salida).')

    notas = []
    fecha_hora = datos["fecha_hora"]
    proceso = cargador.proceso(_fecha(datos["fecha"]).year, datos["proceso"])   # temporada = año de la Fecha
    comunes = {
        "fecha_hora": fecha_hora, "proceso_id": proceso.id, "analista": datos["analista"],
        "fruta": datos.get("fruta"), "blanco_ml": blanco, "normalidad": _numero(datos, "normalidad", notas),
        "ticket": _texto(datos.get("ticket")), "licuadora": datos.get("licuadora"),
        "cargado_por": CARGADO_POR, "origen": texto_origen(archivo, "BOE", numero),
    }

    fruta_g, fruta_ml = _numero(datos, "fruta_g", notas), _numero(datos, "fruta_ml", notas)
    if fruta_g is None or fruta_ml is None:
        reporte.agregar("BOE", numero, "rechazada",
                        f"Sin datos crudos de fruta (peso de muestra o volumen): no se carga el análisis "
                        f"de fruta {lugar.lower()}.")
    else:
        punto = eboe[lugar]
        peso_fruta = _numero(datos, "peso_fruta_kg", notas)
        if peso_fruta == 0:   # dato opcional (frutos en 10 kg): un 0 es "no medido", no invalida el análisis
            notas.append("peso_fruta_kg 0 → vacío.")
            peso_fruta = None
        campos = comunes | {"punto_muestreo_id": punto.id, "peso_muestra_g": fruta_g, "vol_ml": fruta_ml,
                            "peso_fruta_kg": peso_fruta,
                            "n_frutos": _entero(datos, "n_frutos", notas),
                            "calidad": datos.get("calidad"), "rallado": datos.get("rallado")}
        _guardar_o_reportar(cargador, "AnalisisScott", cargador.analisis, (punto.id, fecha_hora),
                            AnalisisScott, campos, f"análisis de fruta {lugar.lower()}", numero, reporte)

    if lugar == "Entrada":
        emulsion_g, emulsion_ml = _numero(datos, "emulsion_g", notas), _numero(datos, "emulsion_ml", notas)
        if emulsion_g is None or emulsion_ml is None:
            reporte.agregar("BOE", numero, "aviso", "Sin datos de emulsión: no se carga el análisis de emulsión.")
        else:
            punto = eboe["emulsión"]
            campos = comunes | {"punto_muestreo_id": punto.id, "peso_muestra_g": emulsion_g,
                                "vol_ml": emulsion_ml, "ph": _numero(datos, "ph", notas),
                                "brix": _numero(datos, "brix", notas),
                                "solidos_centrifugables": _numero(datos, "solidos", notas)}
            _guardar_o_reportar(cargador, "AnalisisScott", cargador.analisis, (punto.id, fecha_hora),
                                AnalisisScott, campos, "análisis de emulsión", numero, reporte)

        if all(datos.get(c) is None for c in MEDICIONES_OPERACION):
            reporte.agregar("BOE", numero, "aviso", "Sin datos de operación (programa, flip gate, MTPH, "
                                                    "caudal): no se carga la lectura.")
        else:
            equipo = eboe["equipo"]
            campos = {"equipo_id": equipo.id, "fecha_hora": fecha_hora,
                      "operador": datos.get("operador"), "programa": datos.get("programa"),
                      "flip_gate": _entero(datos, "flip_gate", notas), "tph": _numero(datos, "tph", notas),
                      "caudal_fase_pesada": _numero(datos, "caudal", notas),
                      "cargado_por": CARGADO_POR, "origen": comunes["origen"]}
            _guardar_o_reportar(cargador, "LecturaOperacion", cargador.lecturas, (equipo.id, fecha_hora),
                                LecturaOperacion, campos, "lectura de operación", numero, reporte)

    if notas:
        reporte.agregar("BOE", numero, "corregida", " ".join(notas))


def _guardar_o_reportar(cargador, tabla, existentes, clave, modelo, campos, que, numero, reporte):
    """Guarda un registro; si el modelo lo rechaza, lo anota y sigue con el resto de la fila."""
    try:
        cargador.guardar(tabla, existentes, clave, modelo, campos)
    except ValueError as error:
        reporte.agregar("BOE", numero, "rechazada", f"No se carga el {que}: {error}")


def _importar_resumen(hoja, archivo: str, planta: Planta, turnos: dict, cargador: _Cargador,
                      reporte: Reporte) -> None:
    filas = []
    for numero, datos in leer_tabla(hoja, COLUMNAS_RESUMEN, max_col=COLUMNAS_TABLA_TURNO):
        if all(datos.get(c) is None for c in PRODUCCION):
            continue   # turno sin datos de producción cargados: no hay nada que importar
        fecha = _fecha(datos.get("fecha"))
        if fecha is None or datos.get("turno") not in turnos or not _proceso_valido(datos.get("proceso")):
            reporte.agregar("Resumen", numero, "rechazada",
                            f'Fecha, turno o proceso no válidos (fecha "{datos.get("fecha")}", turno '
                            f'"{datos.get("turno")}", proceso "{datos.get("proceso")}").')
            continue
        datos["fecha"] = fecha
        filas.append((numero, datos))

    filas, errores, avisos = separar_repetidas("Resumen", filas, ["fecha", "turno", "proceso"], PRODUCCION)
    for hoja_, fila, motivo in errores:
        reporte.agregar(hoja_, fila, "rechazada", motivo)
    for hoja_, fila, motivo in avisos:
        reporte.agregar(hoja_, fila, "aviso", motivo)

    procesos_del_turno = defaultdict(set)
    for _, datos in filas:
        procesos_del_turno[(datos["fecha"], datos["turno"])].add(datos["proceso"])

    for numero, datos in filas:
        procesos = sorted(procesos_del_turno[(datos["fecha"], datos["turno"])])
        if len(procesos) > 1:
            reporte.agregar("Resumen", numero, "aviso",
                            f"Turno compartido por {len(procesos)} procesos "
                            f"({', '.join(map(str, procesos))}): se cargan por separado.")
        notas = []
        try:
            proceso = cargador.proceso(datos["fecha"].year, datos["proceso"])
            turno = turnos[datos["turno"]]
            campos = {"planta_id": planta.id, "fecha_produccion": datos["fecha"], "turno_id": turno.id,
                      "proceso_id": proceso.id, "cargado_por": CARGADO_POR,
                      "origen": texto_origen(archivo, "Resumen", numero)}
            for clave in PRODUCCION:
                campos[clave] = _numero(datos, clave, notas)
            cargador.guardar("RegistroProduccion", cargador.registros,
                             (datos["fecha"], turno.id, proceso.id), RegistroProduccion, campos)
        except (ErrorFila, ValueError) as error:
            reporte.agregar("Resumen", numero, "rechazada", str(error))
            continue
        if notas:
            reporte.agregar("Resumen", numero, "corregida", " ".join(notas))


# ---------------------------------------------------------------- resumen en pantalla

def texto_resumen(resumen: dict, reporte: Reporte, ruta_reporte: Path = RUTA_REPORTE) -> str:
    lineas = ["Importación del histórico de LEDESMA"]
    for tabla, cuentas in resumen.items():
        lineas.append(f"  {tabla + ':':<20}{cuentas['creados']:>6} creados, {cuentas['actualizados']:>6} actualizados")
    lineas.append("  Reporte: " + " · ".join(f"{reporte.cantidad(t)} {t}s" for t in Reporte.TIPOS)
                  + f" (detalle en {ruta_reporte})")
    return "\n".join(lineas)


if __name__ == "__main__":
    motor = crear_motor()
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        resumen, reporte = importar_ledesma(sesion)
        from core.repetidas import marcar_entradas_repetidas
        ledesma = sesion.scalars(select(Planta).where(Planta.nombre == "LEDESMA")).one()
        repetidas = marcar_entradas_repetidas(sesion, ledesma.id)
    reporte.guardar()
    print(texto_resumen(resumen, reporte))
    print("\nEntradas repetidas en varios eBOE (posible copia), por temporada:")
    print(repetidas.to_string(index=False))
