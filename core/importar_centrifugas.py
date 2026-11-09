"""Importa la planilla de centrífugas de LEDESMA: data/historico/CENTRIGUGAS Naranja 25 2.xlsx, hoja Datos.

Estructura (detalle en docs/mapeo_ledesma.md, sección 10):
- Bloques de 4 filas por primaria y por muestreo: emulsión, fase liviana, fase pesada y descarga. Se lee la
  hoja entera (los datos siguen debajo de la tabla de Excel). Una fila que no encaja en un bloque se salta.
- Primaria: columnas L a W. Secundaria: X a AG, una vez por muestreo; "130" → ESB 130. "0" o vacío = el
  operario no eligió el equipo: si hay datos de secundaria (g, mL, t o % de fase liviana) se asume la única
  secundaria usada ese mes (revisar = "secundaria no indicada, asumida …"); si ese mes se usó más de una, o
  ninguna, no se asume y se saltan con su motivo. Sin datos, no hay secundaria. Su alimentación (emulsión)
  no se guarda: se calcula con alimentacion_secundaria().
- Pulidora OSD 60 (AH, AI de la fila de emulsión): sin titulación; dos muestras por muestreo, "descarga" con
  t_descarga_s = AH × 60 y "fase_liviana" con cp_liviana_bar = AI. Una CP en 0 se guarda como viene, con
  cp_liviana_marca = "no medible (valor cercano a 0 o manómetro a revisar)".
- Normalidad: emulsión y descarga N 0,1 (columna I); fase pesada N 0,01 (columna J).
- Fase liviana: el % se anota directo → aceite_pct_medido. En las otras fases se guardan g y mL; si solo hay
  un % (pegado), va a aceite_pct_medido y la muestra se marca para revisar.
- Fecha: la columna Fecha es el día de producción (igual que en el BOE): de 00:00 a 04:59 la muestra es del
  día siguiente. Se usa resolver_fechas() de importar_ledesma. Si el Día cambia y la Fecha no (o la Fecha
  va para atrás), la Fecha se corrige con la del Día anterior + 1 (fecha_original guarda la de la planilla).
  Si la Fecha avanza y el Día no, el que está mal es el Día: la Fecha se deja y se marca.
- No se importan los resultados de la planilla (P, U, V): se recalculan. Solo se comparan con lo recalculado
  para marcar las filas con errores de planilla (campo revisar). Nada se descarta por estar marcado.
- Se puede correr muchas veces: lo que ya existe (equipo + fecha_hora + fase) se actualiza.

Uso (desde la carpeta del proyecto):
    python -m core.importar_centrifugas            # importa
    python -m core.importar_centrifugas --simular  # arma el reporte sin guardar nada
"""

import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.centrifugas import aceite_pct, balance_centrifuga
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.duplicados import separar_repetidas
from core.importar_ledesma import (ErrorFila, Reporte, _fecha, normalizar_hora, normalizar_texto,
                                   resolver_fechas, resolver_procesos)
from core.models import MARCAS_CP, Cliente, MuestraCentrifuga, Planta, Proceso, texto_origen
from core.parametros import validar_parametros
from core.turnos import fecha_produccion

CARPETA_DATOS = Path(__file__).resolve().parent.parent / "data"
RUTA_EXCEL = CARPETA_DATOS / "historico" / "CENTRIGUGAS Naranja 25 2.xlsx"
RUTA_REPORTE = CARPETA_DATOS / "reporte_centrifugas.csv"
CARGADO_POR = "importar_centrifugas"
HOJA = "Datos"
FILA_ENCABEZADO = 2

# Columna de Excel → (nombre interno, texto que tiene que estar en el encabezado).
COLUMNAS = {
    "A": ("proceso", "PROCESO"), "B": ("dia", "Día"), "C": ("fruta", "Especie"), "D": ("fecha", "Fecha"),
    "E": ("hora", "Hora"), "F": ("analista", "Químico"), "G": ("operador", "Operador"), "I": ("n01", "N"),
    "J": ("n001", "N"), "K": ("fase", "Fase"), "L": ("equipo", "Centrifuga"), "M": ("caudal", "Caudal"),
    "N": ("g", "Muestra"), "O": ("ml", "Vol"), "P": ("aceite", "Aceite"), "Q": ("t_descarga", "descarga"),
    "R": ("solidos", "Sólidos"), "S": ("cp_liviana", "Liviana"), "T": ("cp_pesada", "Pesada"),
    "U": ("eficiencia", "Eficiencia"), "W": ("observacion", "Observaciones"),
    "X": ("sec_equipo", "Centrifuga"), "Z": ("sec_g", "Muestra"), "AA": ("sec_ml", "Vol"),
    "AB": ("sec_aceite", "Aceite"), "AC": ("sec_t_descarga", "descarga"), "AD": ("sec_cp_liviana", "Liviana"),
    "AE": ("sec_cp_pesada", "Pesada"), "AH": ("pul_t_descarga_min", "descarga"), "AI": ("pul_cp_liviana", "Liviana"),
}
ORDEN_BLOQUE = ("emulsion", "fase_liviana", "fase_pesada", "descarga")
EQUIPOS_SECUNDARIA = {"130": "ESB 130"}          # abreviaturas de la columna X
NO_INDICADA = {"0"}                               # el operario no eligió la secundaria
PULIDORA = "OSD 60"
FASES_COPIABLES = ("fase_pesada", "descarga")
DATOS_FASE = ("caudal", "g", "ml", "aceite", "t_descarga", "solidos", "cp_liviana", "cp_pesada", "observacion")
DATOS_SECUNDARIA = ("sec_g", "sec_ml", "sec_aceite", "sec_t_descarga", "sec_cp_liviana", "sec_cp_pesada")
DATOS_CRUDOS = ["caudal_lh", "gramos", "ml", "normalidad", "aceite_pct_medido", "solidos_texto", "cp_liviana_bar",
                "cp_pesada_bar", "t_descarga_s", "observacion", "proceso", "fruta", "analista", "operador"]
TOLERANCIA_ACEITE = 1e-6                          # relativa: lo recalculado contra la columna P de la planilla

# Motivos del campo revisar (la fila se importa igual).
REVISAR_ESB_PRIMARIA = "ESB 130 anotada como 1° centrífuga"
REVISAR_FISICO = "fuera de rango físico"
REVISAR_PEGADO = "valor pegado, sin datos crudos"
REVISAR_COPIADA = "copiada de la otra primaria"
REVISAR_NORMALIDAD = "verificar normalidad de la descarga (¿se tituló con 0,01?)"
REVISAR_NORMALIDAD_PESADA = "la planilla calcula la fase pesada con N 0,1 (acá se usa N 0,01)"
REVISAR_ACEITE = "el aceite % de la planilla no coincide con el de g y mL"
REVISAR_SIN_LIVIANA = "en la planilla falta el caudal de fase liviana (Excel da eficiencia 0)"
REVISAR_FECHA = "fecha fuera de secuencia"
REVISAR_ASUMIDA = "secundaria no indicada, asumida {}"
REVISAR_FECHA_CORREGIDA = "fecha corregida por el Día"
REVISAR_DIA = "Día fuera de secuencia (la Fecha avanza y el Día no)"
REVISAR_EXTREMO = "valor extremo: muestra muy chica o titulación fuera de rango del método"
ACEITE_DESCARGA_EXTREMO = 20.0                    # % de aceite en descarga por encima del cual se marca
GRAMOS_MINIMOS_DESCARGA = 1.0                     # g de muestra de descarga por debajo de los cuales se marca
MOTIVOS_DE_FECHA = (REVISAR_FECHA, REVISAR_FECHA_CORREGIDA, REVISAR_DIA)
LARGO_REVISAR = 200


class ReporteCentrifugas(Reporte):
    """Como el reporte del BOE, más el tipo "revisar" (muestra importada que hay que mirar)."""

    TIPOS = ("rechazada", "corregida", "aviso", "revisar")


# ---------------------------------------------------------------- normalización (funciones puras)

def normalizar_fase(valor) -> str | None:
    """ "Emulsion", "F Livina", "Fase livina", "F. Pesada", "DESCARGA"… → emulsion, fase_liviana, fase_pesada,
    descarga. Sin tildes, mayúsculas ni espacios; tolera errores de tipeo ("livina"). None si no se reconoce."""
    if valor is None:
        return None
    texto = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode().lower()
    texto = re.sub(r"[^a-z]", "", texto)
    if texto.startswith("emul"):
        return "emulsion"
    if texto.startswith("desc"):
        return "descarga"
    if "liv" in texto:
        return "fase_liviana"
    if "pes" in texto:
        return "fase_pesada"
    return None


def normalizar_equipo(valor) -> str | None:
    """Nombre de la centrífuga: "130" → "ESB 130"; "0" o vacío → None (no hay equipo)."""
    if valor is None:
        return None
    texto = str(int(valor)) if isinstance(valor, float) and valor.is_integer() else " ".join(str(valor).split())
    if not texto or texto in NO_INDICADA:
        return None
    return EQUIPOS_SECUNDARIA.get(texto, texto)


def leer_solidos(valor) -> tuple[float | None, str | None, str | None]:
    """Sólidos centrifugables → (número %, texto original, marca).

    12 → (12, None, None); "<1" → (1, "<1", "menor que"); ">7 y <8" → (7,5, ">7 y <8", "rango").
    Otro texto da ErrorFila.
    """
    if valor is None:
        return None, None, None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor), None, None
    texto = " ".join(str(valor).split())
    numero = r"(\d+(?:[.,]\d+)?)"
    if coincide := re.fullmatch(rf"<\s*{numero}", texto):
        return _a_numero(coincide.group(1)), texto, "menor que"
    if coincide := re.fullmatch(rf">\s*{numero}\s*y\s*<\s*{numero}", texto):
        desde, hasta = _a_numero(coincide.group(1)), _a_numero(coincide.group(2))
        return (desde + hasta) / 2, texto, "rango"
    try:
        return _a_numero(texto), texto, None
    except ValueError:
        raise ErrorFila(f'Sólidos "{texto}" no se entienden (se espera un número, "<1" o ">7 y <8").')


def leer_hora(valor):
    """Hora de la planilla: como la del BOE, aceptando también "5;10"."""
    if isinstance(valor, str):
        valor = valor.replace(";", ":")
    return normalizar_hora(valor)


def _a_numero(texto: str) -> float:
    return float(texto.replace(",", "."))


def _numero(valor) -> float | None:
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


# ---------------------------------------------------------------- lectura y bloques

def leer_hoja(hoja) -> list[tuple[int, dict]]:
    """Filas con algún dato como (n.º de fila de Excel, {nombre interno: valor}). Controla el encabezado."""
    filas = hoja.iter_rows(min_row=FILA_ENCABEZADO, values_only=True)
    encabezado = next(filas)
    indices = {}
    for letra, (nombre, texto) in COLUMNAS.items():
        indice = _indice(letra)
        celda = " ".join(str(encabezado[indice]).split()) if indice < len(encabezado) else ""
        if texto.lower() not in celda.lower():
            raise ValueError(f'La columna {letra} de la hoja "{hoja.title}" tendría que ser "{texto}" (dice "{celda}"). '
                             "¿Cambió la planilla?")
        indices[nombre] = indice
    resultado = []
    for numero, valores in enumerate(filas, start=FILA_ENCABEZADO + 1):
        datos = {nombre: _limpiar(valores[i]) if i < len(valores) else None for nombre, i in indices.items()}
        if datos["fase"] is not None or datos["hora"] is not None:
            resultado.append((numero, datos))
    return resultado


def armar_bloques(filas: list[tuple[int, dict]], reporte: Reporte) -> list[dict]:
    """Agrupa las filas en bloques de 4 (emulsión, liviana, pesada, descarga), en el orden de la hoja.

    Devuelve [{"fila": n.º de la emulsión, "filas": {fase: (n.º, datos)}}]. Las filas que no encajan y los
    bloques sin ningún dato (filas de plantilla) se saltan con su motivo.
    """
    bloques, i = [], 0
    while i < len(filas):
        numero, datos = filas[i]
        fase = normalizar_fase(datos["fase"])
        if fase != "emulsion":
            motivo = (f'Fase "{datos["fase"]}" no reconocida' if fase is None
                      else f'Fila de {fase} fuera de un bloque (no viene después de una emulsión)')
            reporte.agregar(HOJA, numero, "rechazada", f"{motivo}: no se importa.")
            i += 1
            continue
        grupo = filas[i:i + 4]
        fases = [normalizar_fase(d["fase"]) for _, d in grupo]
        consecutivas = [n for n, _ in grupo] == list(range(numero, numero + len(grupo)))
        if tuple(fases) != ORDEN_BLOQUE or not consecutivas:
            reporte.agregar(HOJA, numero, "rechazada",
                            "Bloque incompleto: después de la emulsión tienen que venir fase liviana, fase pesada "
                            f"y descarga (vienen: {', '.join(str(f) for f in fases[1:]) or 'nada'}). No se importa.")
            i += 1
            continue
        bloque = {"fila": numero, "filas": dict(zip(ORDEN_BLOQUE, grupo))}
        if _sin_datos(bloque):
            reporte.agregar(HOJA, numero, "rechazada", f"Bloque sin datos (filas {numero}–{numero + 3}, plantilla "
                                                       "vacía): no se importa.")
        else:
            bloques.append(bloque)
        i += 4
    return bloques


def _sin_datos(bloque: dict) -> bool:
    columnas = DATOS_FASE + DATOS_SECUNDARIA + ("pul_t_descarga_min", "pul_cp_liviana")
    return all(datos.get(c) in (None, 0) or (c == "caudal" and fase != "emulsion")
               for fase, (_, datos) in bloque["filas"].items() for c in columnas)


def _indice(letra: str) -> int:
    valor = 0
    for caracter in letra:
        valor = valor * 26 + ord(caracter) - ord("A") + 1
    return valor - 1


def _limpiar(valor):
    if isinstance(valor, str):
        return valor.strip() or None
    return valor


# ---------------------------------------------------------------- importación

def importar_centrifugas(sesion: Session, ruta: Path = RUTA_EXCEL, cliente: str = "LEDESMA",
                         planta: str = "LEDESMA", guardar: bool = True,
                         corregir_fechas: bool = True) -> tuple[dict, ReporteCentrifugas]:
    """Carga la hoja Datos en MuestraCentrifuga. Devuelve (resumen, reporte).

    resumen: filas leídas, muestras creadas/actualizadas, muestras por equipo y año, motivos de revisión.
    guardar=False arma todo y deshace (para simular). corregir_fechas=False solo marca las fechas fuera de
    secuencia (sirve para comparar).
    """
    ruta = Path(ruta)
    la_planta = sesion.scalars(select(Planta).join(Cliente).where(
        Cliente.nombre == cliente, Planta.nombre == planta)).one_or_none()
    if la_planta is None:
        raise ValueError(f'No existe la planta "{planta}" del cliente "{cliente}". '
                         "Importá primero los datos maestros (python -m core.importar_maestros).")
    parametros = validar_parametros(la_planta, ["blanco_centrifugas_ml", "factor_aceite_emulsion", "hora_inicio_dia"])
    equipos = {e.nombre: e for linea in la_planta.lineas for e in linea.equipos}

    reporte = ReporteCentrifugas()
    filas = leer_hoja(load_workbook(ruta, data_only=True, read_only=True)[HOJA])
    resumen = {"filas_leidas": len(filas), "creados": 0, "actualizados": 0, "procesos_creados": 0,
               "por_equipo": Counter(), "revisar": Counter(), "cp_cero": 0}

    bloques = _con_fecha(armar_bloques(filas, reporte), la_planta, reporte, corregir_fechas)
    por_mes = secundarias_por_mes(bloques)
    for bloque in bloques:
        bloque["secundarias_del_mes"] = por_mes.get((bloque["fecha_hora"].year, bloque["fecha_hora"].month), set())
    muestras = []
    for bloque in bloques:
        try:
            muestras += _muestras_del_bloque(bloque, equipos, parametros, ruta.name, reporte)
        except ErrorFila as error:
            reporte.agregar(HOJA, bloque["fila"], "rechazada", str(error))
    _marcar_copiadas(muestras)
    resumen["secundaria_asumida"] = Counter(b["secundaria_asumida"] for b in bloques if b.get("secundaria_asumida"))

    para_guardar = []
    for equipo in ("primaria", "secundaria", "pulidora"):     # por separado: comparten n.º de fila
        grupo = [(m["fila"], m) for m in muestras if m["grupo"] == equipo]
        grupo, errores, avisos = separar_repetidas(HOJA, grupo, ["equipo", "fecha_hora", "fase"], DATOS_CRUDOS)
        for hoja, fila, motivo in errores:
            reporte.agregar(hoja, fila, "rechazada", motivo)
        for hoja, fila, motivo in avisos:
            reporte.agregar(hoja, fila, "aviso", motivo)
        para_guardar += [m for _, m in grupo]
    _guardar(sesion, la_planta, _sin_choques(para_guardar, reporte), resumen, reporte)
    if guardar:
        sesion.commit()
    else:
        sesion.rollback()
    return resumen, reporte


def _con_fecha(bloques: list[dict], planta: Planta, reporte: Reporte, corregir: bool = True) -> list[dict]:
    """Proceso, fecha_hora real (reglas del BOE) y control de la secuencia de Día y Fecha de cada proceso.

    - El Día cambia y la Fecha no, o la Fecha va para atrás: la Fecha es la del Día anterior + 1 (si ese Día
      ya apareció; si no, solo se marca). Se guarda la de la planilla en fecha_original.
    - La Fecha avanza y el Día no: la Fecha se deja (el Día está mal) y se marca.
    """
    filas = []
    for bloque in bloques:
        numero, datos = bloque["filas"]["emulsion"]
        if _fecha(datos["fecha"]) is None:
            reporte.agregar(HOJA, numero, "rechazada", f'Fecha "{datos["fecha"]}" no válida: no se importa el bloque.')
            continue
        try:
            hora, corregida = leer_hora(datos["hora"])
        except ErrorFila as error:
            reporte.agregar(HOJA, numero, "rechazada", f"{error} No se importa el bloque.")
            continue
        if corregida:
            reporte.agregar(HOJA, numero, "corregida", f'Hora "{datos["hora"]}" → {hora:%H:%M}.')
        filas.append((numero, {"proceso": datos["proceso"], "dia": datos["dia"], "fecha": datos["fecha"],
                               "hora": hora, "bloque": bloque}))
    filas = resolver_fechas(resolver_procesos(filas, reporte, HOJA), planta, reporte, HOJA)

    resultado = []
    anterior = {}       # (temporada, proceso) → (día, fecha de producción) del último bloque bien fechado
    por_dia = {}        # (temporada, proceso, día) → fecha de producción de ese Día
    for numero, datos in filas:
        bloque, dia = datos["bloque"], datos["dia"]
        bloque.update(fecha_hora=datos["fecha_hora"], proceso=datos["proceso"], dia=dia,
                      temporada=_fecha(datos["fecha"]).year, revisar=[], fecha_original=None)
        produccion = fecha_produccion(planta, datos["fecha_hora"])
        clave = (bloque["temporada"], datos["proceso"])
        if clave in anterior:
            dia_antes, produccion_antes = anterior[clave]
            cambia_dia, cambia_fecha = dia != dia_antes, produccion != produccion_antes
            if produccion < produccion_antes or (cambia_dia and not cambia_fecha):
                dia_previo = por_dia.get((*clave, dia - 1)) if corregir else None
                if dia_previo is None:
                    bloque["revisar"].append(REVISAR_FECHA)
                    reporte.agregar(HOJA, numero, "revisar",
                                    f"{REVISAR_FECHA}: Día {dia}, día de producción {produccion:%d/%m/%Y}; el bloque "
                                    f"anterior del proceso {datos['proceso']} es Día {dia_antes}, "
                                    f"{produccion_antes:%d/%m/%Y}.")
                else:
                    corregida = dia_previo + timedelta(days=1)
                    bloque["fecha_hora"] += corregida - produccion
                    bloque["fecha_original"] = _fecha(datos["fecha"])
                    bloque["revisar"].append(REVISAR_FECHA_CORREGIDA)
                    reporte.agregar(HOJA, numero, "revisar",
                                    f"{REVISAR_FECHA_CORREGIDA}: proceso {datos['proceso']}, Día {dia}, Fecha "
                                    f"{bloque['fecha_original']:%d/%m/%Y} → día de producción {corregida:%d/%m/%Y} "
                                    f"(el Día {dia - 1} es del {dia_previo:%d/%m/%Y}).")
                    produccion = corregida
            elif cambia_fecha and not cambia_dia:
                bloque["revisar"].append(REVISAR_DIA)
                reporte.agregar(HOJA, numero, "revisar",
                                f"{REVISAR_DIA}: proceso {datos['proceso']}, Día {dia} del {produccion:%d/%m/%Y}; el "
                                f"bloque anterior es el mismo Día del {produccion_antes:%d/%m/%Y}. Se deja la Fecha.")
        if REVISAR_FECHA not in bloque["revisar"]:    # un bloque con la fecha mal no sirve de referencia
            anterior[clave] = (dia, produccion)
            por_dia.setdefault((*clave, dia), produccion)
        resultado.append(bloque)
    return resultado


def _muestras_del_bloque(bloque: dict, equipos: dict, parametros: dict, archivo: str,
                         reporte: Reporte) -> list[dict]:
    """Muestras (como diccionarios) de la primaria del bloque y, si hay, de su secundaria."""
    _, emulsion = bloque["filas"]["emulsion"]
    nombre = normalizar_equipo(emulsion["equipo"])
    if nombre not in equipos:
        raise ErrorFila(f'Centrífuga primaria "{emulsion["equipo"]}" no existe en los datos maestros: no se importa '
                        "el bloque.")
    primaria = equipos[nombre]
    comunes = {"fecha_hora": bloque["fecha_hora"], "proceso": (bloque["temporada"], bloque["proceso"]),
               "fruta": normalizar_texto(emulsion["fruta"]), "analista": normalizar_texto(emulsion["analista"], True),
               "operador": normalizar_texto(emulsion["operador"], True), "archivo": archivo,
               "fecha_original": bloque["fecha_original"]}
    revisar_bloque = list(bloque["revisar"])
    if primaria.tipo != "Centrífuga primaria":
        revisar_bloque.append(REVISAR_ESB_PRIMARIA if nombre == "ESB 130" else f"{nombre} anotada como 1° centrífuga")

    muestras = [m for fase in ORDEN_BLOQUE
                if (m := _muestra(bloque, fase, "", primaria, comunes, parametros, reporte)) is not None]
    for motivo in _revisar_balance(bloque, primaria, muestras, parametros):
        revisar_bloque.append(motivo)
    for muestra in muestras:
        muestra["revisar"] = revisar_bloque + muestra["revisar"]

    secundaria, asumida = _secundaria(bloque, equipos, reporte)
    if secundaria is not None:
        revisar_secundaria = list(bloque["revisar"]) + ([REVISAR_ASUMIDA.format(secundaria.nombre)] if asumida else [])
        for fase in ORDEN_BLOQUE[1:]:           # su emulsión es la suma de las livianas: no es un dato crudo
            muestra = _muestra(bloque, fase, "sec_", secundaria, comunes, parametros, reporte)
            if muestra is not None:
                muestra["revisar"] = revisar_secundaria + muestra["revisar"]
                muestras.append(muestra)
        if asumida:
            bloque["secundaria_asumida"] = secundaria.nombre
    return muestras + _pulidora(bloque, equipos, comunes, reporte)


def _pulidora(bloque: dict, equipos: dict, comunes: dict, reporte: Reporte) -> list[dict]:
    """Muestras de la pulidora (AH: minutos entre descargas, AI: CP liviana) de la fila de emulsión."""
    numero, emulsion = bloque["filas"]["emulsion"]
    minutos, cp = _numero(emulsion["pul_t_descarga_min"]), _numero(emulsion["pul_cp_liviana"])
    if minutos is None and cp is None:
        return []
    if PULIDORA not in equipos:
        reporte.agregar(HOJA, numero, "rechazada", f'La pulidora "{PULIDORA}" no existe en los datos maestros: no '
                                                   "se importan sus datos.")
        return []
    muestras = []
    if minutos is not None:
        muestras.append(_vacia(numero, equipos[PULIDORA], "descarga", "pulidora", comunes)
                        | {"t_descarga_s": minutos * 60})
    if cp is not None:
        muestras.append(_vacia(numero, equipos[PULIDORA], "fase_liviana", "pulidora", comunes)
                        | {"cp_liviana_bar": cp, "cp_liviana_marca": MARCAS_CP[0] if cp == 0 else None})
    for muestra in muestras:
        muestra["revisar"] = list(bloque["revisar"])
    return muestras


def _vacia(numero: int, equipo, fase: str, grupo: str, comunes: dict) -> dict:
    """Muestra con todos los datos vacíos (se completan después)."""
    return {"fila": numero, "equipo": equipo.codigo, "equipo_obj": equipo, "fase": fase, "revisar": [],
            "grupo": grupo, "caudal_lh": None, "gramos": None, "ml": None, "normalidad": None, "blanco_ml": None,
            "aceite_pct_medido": None, "t_descarga_s": None, "cp_liviana_bar": None, "cp_liviana_marca": None,
            "cp_pesada_bar": None, "observacion": None, "solidos_pct": None, "solidos_texto": None,
            "solidos_marca": None} | comunes


def secundarias_por_mes(bloques: list[dict]) -> dict[tuple[int, int], set[str]]:
    """{(año, mes): nombres de las secundarias anotadas en la columna X ese mes}."""
    resultado = defaultdict(set)
    for bloque in bloques:
        nombre = _nombre_secundaria(bloque)
        if nombre is not None:
            resultado[(bloque["fecha_hora"].year, bloque["fecha_hora"].month)].add(nombre)
    return dict(resultado)


def tiene_datos_secundaria(bloque: dict) -> bool:
    """¿Hay datos de secundaria en el bloque? (% de fase liviana, g o mL de pesada o descarga, t de descarga)."""
    filas = {fase: datos for fase, (_, datos) in bloque["filas"].items()}
    valores = [filas["fase_liviana"]["sec_aceite"], filas["descarga"]["sec_t_descarga"]] + [
        filas[fase][c] for fase in ("fase_pesada", "descarga") for c in ("sec_g", "sec_ml")]
    return any(_numero(v) is not None for v in valores)


def _nombre_secundaria(bloque: dict) -> str | None:
    valores = [d["sec_equipo"] for _, d in bloque["filas"].values() if d["sec_equipo"] is not None]
    return normalizar_equipo(valores[0]) if valores else None


def _secundaria(bloque: dict, equipos: dict, reporte: Reporte):
    """(equipo de la secundaria, asumida) o (None, False) si no hay.

    Sin equipo en la columna X ("0" o vacío) pero con datos: se asume la única secundaria anotada ese mes.
    """
    valores = [d["sec_equipo"] for _, d in bloque["filas"].values() if d["sec_equipo"] is not None]
    nombre = _nombre_secundaria(bloque)
    if nombre is None:
        if not tiene_datos_secundaria(bloque):
            return None, False
        del_mes = sorted(bloque["secundarias_del_mes"])
        if len(del_mes) != 1:
            usadas = " y ".join(del_mes) if del_mes else "ninguna"
            reporte.agregar(HOJA, bloque["fila"], "rechazada",
                            f"Secundaria no indicada (dice \"{valores[0] if valores else ''}\") y ese mes se usaron "
                            f"{usadas}: no se asume ninguna y no se importan sus muestras.")
            return None, False
        nombre = del_mes[0]
        if nombre in equipos:
            return equipos[nombre], True
    if nombre not in equipos:
        reporte.agregar(HOJA, bloque["fila"], "rechazada", f'Secundaria "{valores[0]}" no existe en los datos '
                                                           "maestros: no se importan sus muestras.")
        return None, False
    if str(valores[0]).strip() != nombre:
        reporte.agregar(HOJA, bloque["fila"], "corregida", f'Secundaria "{valores[0]}" → {nombre}.')
    return equipos[nombre], False


def _muestra(bloque: dict, fase: str, prefijo: str, equipo, comunes: dict, parametros: dict,
             reporte: Reporte) -> dict | None:
    """Datos crudos de una fase de un equipo (prefijo "" = primaria, "sec_" = secundaria). None si no hay datos."""
    numero, datos = bloque["filas"][fase]
    valor = lambda clave: datos.get(prefijo + clave)            # noqa: E731
    g, ml, aceite = _numero(valor("g")), _numero(valor("ml")), _numero(valor("aceite"))
    muestra = _vacia(numero, equipo, fase, "secundaria" if prefijo else "primaria", comunes) | {
        "caudal_lh": _numero(datos["caudal"]) if fase == "emulsion" and not prefijo else None,
        "t_descarga_s": _numero(valor("t_descarga")), "cp_liviana_bar": _numero(valor("cp_liviana")),
        "cp_pesada_bar": _numero(valor("cp_pesada")), "observacion": None if prefijo else datos["observacion"]}
    if not prefijo:
        try:
            muestra["solidos_pct"], muestra["solidos_texto"], muestra["solidos_marca"] = leer_solidos(datos["solidos"])
        except ErrorFila as error:
            reporte.agregar(HOJA, numero, "aviso", f"{error} Se importa sin sólidos.")
            muestra["solidos_texto"] = str(datos["solidos"])[:30]
        if muestra["solidos_marca"]:
            reporte.agregar(HOJA, numero, "corregida", f'Sólidos "{muestra["solidos_texto"]}" → '
                                                       f'{muestra["solidos_pct"]:g} ({muestra["solidos_marca"]}).')

    if fase == "fase_liviana":
        muestra["aceite_pct_medido"] = aceite                  # se anota directo: no hay titulación
    elif g is not None and ml is not None:
        normalidad = _numero(datos["n001"] if fase == "fase_pesada" else datos["n01"])
        muestra.update(gramos=g, ml=ml, normalidad=normalidad, blanco_ml=parametros["blanco_centrifugas_ml"])
        muestra["revisar"] += _revisar_aceite(fase, g, ml, aceite, datos, parametros)
    elif g is not None or ml is not None:
        reporte.agregar(HOJA, numero, "aviso", f"{equipo.nombre} {fase}: titulación incompleta (g {g}, mL {ml}): se "
                                               "importa sin aceite.")
    elif aceite is not None:
        muestra["aceite_pct_medido"] = aceite
        muestra["revisar"].append(REVISAR_PEGADO)
    if fase == "descarga":
        muestra["revisar"] += _revisar_extremo(muestra, parametros)

    if all(muestra[c] is None for c in ("caudal_lh", "gramos", "aceite_pct_medido", "t_descarga_s", "cp_liviana_bar",
                                        "cp_pesada_bar", "observacion", "solidos_pct", "solidos_texto")):
        return None
    return muestra


def _revisar_extremo(muestra: dict, parametros: dict) -> list[str]:
    """Descarga con aceite > ACEITE_DESCARGA_EXTREMO % o muestra < GRAMOS_MINIMOS_DESCARGA g: se marca (no se
    excluye de ningún cálculo). El aceite es el de g y mL o, si no hay titulación, el pegado."""
    g, aceite = muestra.get("gramos"), muestra.get("aceite_pct_medido")
    if g is not None and muestra.get("ml") is not None:
        try:
            aceite = aceite_pct(muestra["ml"], muestra["blanco_ml"], muestra["normalidad"], g,
                                parametros["factor_aceite_emulsion"])
        except ValueError:
            aceite = None
    if (g is not None and g < GRAMOS_MINIMOS_DESCARGA) or (aceite is not None and aceite > ACEITE_DESCARGA_EXTREMO):
        return [REVISAR_EXTREMO]
    return []


def _revisar_aceite(fase, g, ml, aceite_planilla, datos, parametros) -> list[str]:
    """Compara el aceite % de la planilla (columna P o AB) con el recalculado con g y mL."""
    if aceite_planilla is None:
        return []
    blanco, factor = parametros["blanco_centrifugas_ml"], parametros["factor_aceite_emulsion"]
    normalidad = _numero(datos["n001"] if fase == "fase_pesada" else datos["n01"])
    try:
        calculado = aceite_pct(ml, blanco, normalidad, g, factor)
    except ValueError:
        return []
    if abs(calculado - aceite_planilla) <= TOLERANCIA_ACEITE * max(abs(aceite_planilla), 1):
        return []
    # ¿la planilla usó la otra normalidad?
    otra, motivo = {"descarga": (datos["n001"], REVISAR_NORMALIDAD),
                    "fase_pesada": (datos["n01"], REVISAR_NORMALIDAD_PESADA)}.get(fase, (None, None))
    otra = _numero(otra)
    if otra and abs(aceite_pct(ml, blanco, otra, g, factor) - aceite_planilla) <= \
            TOLERANCIA_ACEITE * max(abs(aceite_planilla), 1):
        return [motivo]
    return [REVISAR_ACEITE]


def _revisar_balance(bloque: dict, equipo, muestras: list[dict], parametros: dict) -> list[str]:
    """Eficiencia fuera del rango físico, o la planilla sin caudal de fase liviana (Excel da eficiencia 0)."""
    por_fase = {m["fase"]: m for m in muestras}
    if set(por_fase) != set(ORDEN_BLOQUE):
        return []
    aceites = {fase: _aceite_de(m, parametros) for fase, m in por_fase.items()}
    caudal, t = por_fase["emulsion"]["caudal_lh"], por_fase["descarga"]["t_descarga_s"]
    if None in aceites.values() or caudal is None or not t:
        return []
    try:
        balance = balance_centrifuga(
            caudal_emulsion=caudal, aceite_emulsion_pct=aceites["emulsion"], aceite_liviana_pct=aceites["fase_liviana"],
            aceite_pesada_pct=aceites["fase_pesada"], aceite_descarga_pct=aceites["descarga"],
            volumen_descarga_l=equipo.volumen_descarga_l, t_descarga_s=t)
    except ValueError:
        return []
    motivos = []
    if balance["eficiencia"] is not None and balance["eficiencia"].fuera_de_rango_fisico:
        motivos.append(REVISAR_FISICO)
    if bloque["filas"]["emulsion"][1]["eficiencia"] == 0:
        motivos.append(REVISAR_SIN_LIVIANA)
    return motivos


def _aceite_de(muestra: dict, parametros: dict) -> float | None:
    if muestra["gramos"] is not None:
        try:
            return aceite_pct(muestra["ml"], muestra["blanco_ml"], muestra["normalidad"], muestra["gramos"],
                              parametros["factor_aceite_emulsion"])
        except ValueError:
            return None
    return muestra["aceite_pct_medido"]


def _marcar_copiadas(muestras: list[dict]) -> None:
    """Fase pesada o descarga de una primaria con los mismos g y mL que la de otra primaria del mismo muestreo."""
    primera = {}                       # (fecha_hora, fase) → (equipo, (g, mL)) de la primera primaria
    for muestra in muestras:
        if muestra["fase"] not in FASES_COPIABLES or muestra["equipo_obj"].tipo != "Centrífuga primaria" \
                or muestra["gramos"] is None:
            continue
        datos = (muestra["gramos"], muestra["ml"])
        anterior = primera.setdefault((muestra["fecha_hora"], muestra["fase"]), (muestra["equipo"], datos))
        if anterior[0] != muestra["equipo"] and anterior[1] == datos:
            muestra["revisar"].append(REVISAR_COPIADA)


def _sin_choques(muestras: list[dict], reporte: Reporte) -> list[dict]:
    """Misma clave en la primaria y en la secundaria (una ESB 130 anotada en las dos columnas del mismo
    muestreo): si los datos son iguales se carga una; si no, ninguna (misma regla que separar_repetidas)."""
    grupos = defaultdict(list)
    for muestra in muestras:
        grupos[(muestra["equipo"], muestra["fecha_hora"], muestra["fase"])].append(muestra)
    resultado = []
    for (equipo, fecha_hora, fase), grupo in grupos.items():
        if len(grupo) == 1 or all(_datos(m) == _datos(grupo[0]) for m in grupo):
            resultado.append(grupo[0])
            continue
        filas = " y ".join(str(fila) for fila in sorted(m["fila"] for m in grupo))
        for muestra in grupo:
            reporte.agregar(HOJA, muestra["fila"], "rechazada",
                            f"{equipo} {fase} {fecha_hora:%d/%m/%Y %H:%M} está como primaria y como secundaria (filas "
                            f"{filas}) con datos distintos. No se carga ninguna: corregí el Excel.")
    return resultado


def _datos(muestra: dict) -> tuple:
    return tuple(muestra.get(c) for c in DATOS_CRUDOS)


def _guardar(sesion: Session, planta: Planta, muestras: list[dict], resumen: dict, reporte: Reporte) -> None:
    """Crea o actualiza cada muestra por equipo + fecha_hora + fase."""
    ids = [e.id for linea in planta.lineas for e in linea.equipos]
    existentes = {(m.equipo_id, m.fecha_hora, m.fase): m for m in sesion.scalars(
        select(MuestraCentrifuga).where(MuestraCentrifuga.equipo_id.in_(ids)))}
    procesos = {(p.temporada, p.numero): p for p in planta.procesos}
    for muestra in muestras:
        clave_proceso = muestra["proceso"]
        if clave_proceso not in procesos:
            procesos[clave_proceso] = Proceso(planta_id=planta.id, temporada=clave_proceso[0], numero=clave_proceso[1])
            sesion.add(procesos[clave_proceso])
            sesion.flush()
            resumen["procesos_creados"] += 1
        motivos = list(dict.fromkeys(muestra["revisar"]))       # sin repetir, en orden
        campos = {c: muestra[c] for c in ("fecha_hora", "fase", "fruta", "analista", "operador", "caudal_lh", "gramos",
                                          "ml", "normalidad", "blanco_ml", "aceite_pct_medido", "solidos_pct",
                                          "solidos_texto", "solidos_marca", "cp_liviana_bar", "cp_liviana_marca",
                                          "cp_pesada_bar", "t_descarga_s", "observacion", "fecha_original")}
        campos |= {"equipo_id": muestra["equipo_obj"].id, "proceso_id": procesos[clave_proceso].id,
                   "revisar": "; ".join(motivos)[:LARGO_REVISAR] or None, "cargado_por": CARGADO_POR,
                   "origen": texto_origen(muestra["archivo"], HOJA, muestra["fila"])}
        try:
            nuevo = MuestraCentrifuga(**campos)
        except ValueError as error:
            reporte.agregar(HOJA, muestra["fila"], "rechazada", f"No se importa la muestra de {muestra['fase']} de "
                                                                f"{muestra['equipo']}: {error}")
            continue
        clave = (campos["equipo_id"], campos["fecha_hora"], campos["fase"])
        if clave in existentes:
            for campo, valor in campos.items():
                setattr(existentes[clave], campo, valor)
            resumen["actualizados"] += 1
        else:
            sesion.add(nuevo)
            existentes[clave] = nuevo
            resumen["creados"] += 1
        resumen["por_equipo"][(muestra["proceso"][0], muestra["equipo_obj"].nombre, muestra["fase"])] += 1
        resumen["cp_cero"] += campos["cp_liviana_marca"] is not None
        for motivo in motivos:
            resumen["revisar"][motivo] += 1
            if motivo not in MOTIVOS_DE_FECHA:          # la fecha ya se anotó por bloque
                reporte.agregar(HOJA, muestra["fila"], "revisar", f"{muestra['equipo']} {muestra['fase']}: {motivo}.")


# ---------------------------------------------------------------- resumen en pantalla

def texto_resumen(resumen: dict, reporte: ReporteCentrifugas, ruta_reporte: Path = RUTA_REPORTE,
                  simulado: bool = False) -> str:
    """Reporte para la pantalla: filas leídas, muestras importadas, saltadas, para revisar y por qué."""
    lineas = [f"Importación de centrífugas de LEDESMA{' (SIMULACIÓN: no se guardó nada)' if simulado else ''}",
              f"  Filas leídas:     {resumen['filas_leidas']:>6}",
              f"  Muestras:         {resumen['creados']:>6} creadas, {resumen['actualizados']} actualizadas",
              f"  Procesos creados: {resumen['procesos_creados']:>6}",
              f"  Saltadas:         {reporte.cantidad('rechazada'):>6} (bloques o filas que no se importan)",
              f"  Corregidas:       {reporte.cantidad('corregida'):>6} · avisos: {reporte.cantidad('aviso')}",
              f"  Para revisar:     {sum(1 for f in reporte.filas if f[2] == 'revisar'):>6} anotaciones"]
    for motivo, cantidad in resumen["revisar"].most_common():
        lineas.append(f"      {cantidad:>5} muestras · {motivo}")
    saltadas = Counter(re.sub(r"\d+", "…", m.split(" (")[0].split(":")[0]).rstrip(".") for _, _, t, m in reporte.filas
                       if t == "rechazada")
    if saltadas:
        lineas.append("  Motivos de las saltadas:")
        lineas += [f"      {n:>5} · {motivo}" for motivo, n in saltadas.most_common()]
    lineas.append("  Muestras por temporada, equipo y fase:")
    por_equipo = defaultdict(dict)
    for (anio, equipo, fase), n in resumen["por_equipo"].items():
        por_equipo[(anio, equipo)][fase] = n
    for (anio, equipo), fases in sorted(por_equipo.items()):
        lineas.append(f"      {anio} {equipo:<12} " + " · ".join(f"{f} {fases.get(f, 0)}" for f in ORDEN_BLOQUE))
    for nombre, bloques in resumen.get("secundaria_asumida", {}).items():
        lineas.append(f"  Secundaria no indicada, asumida {nombre}: {bloques} muestreos.")
    if resumen["cp_cero"]:
        lineas.append(f"  CP liviana en 0 (marca \"{MARCAS_CP[0]}\", sin revisar): {resumen['cp_cero']} muestras.")
    lineas.append(f"  Detalle en {ruta_reporte}")
    return "\n".join(lineas)


if __name__ == "__main__":
    simular = "--simular" in sys.argv[1:]
    motor = crear_motor()
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        resumen, reporte = importar_centrifugas(sesion, guardar=not simular)
    reporte.guardar(RUTA_REPORTE)
    print(texto_resumen(resumen, reporte, simulado=simular))
