"""Bitácora de cambios operativos (tabla CambioOperativo) y comparación antes/después.

- crear_cambio(), editar_cambio(), borrar_cambio(): la carga, con historial en Cambio (tabla "cambio_operativo";
  editar y borrar piden motivo; un borrado guarda la fila completa). Permiso: core.cambios.puede_editar.
- listar_cambios(): los cambios de la planta (con día de producción y turno, que se calculan, no se guardan).
- periodo_de(): en qué período de un gráfico cae cada cambio (mes, semana, turno), para la línea vertical.
- antes_despues(): KPI en los N días de producción antes y después del día del cambio (ese día no entra en ninguno de
  los dos lados): media antes y después, diferencia, %, N y prueba (Welch si los dos lados son normales según
  Anderson-Darling, como Minitab; si no, Mann-Whitney). Si el cambio es de un eBOE, los KPI de los eBOE son solo de
  ese eBOE; los de la planta (proceso, retorno de FP, captado) son siempre de la planta.
Una comparación antes/después no prueba la causa: en esos días pudieron cambiar otras cosas (fruta, programa…).
"""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import select

from core.analisis import pares_con_emulsion, tabla_analisis, tabla_emulsion
from core.analisis_centrifugas import agua_por_eboe, balance_por_muestreo, por_muestreo, primarias_por_turno, tabla_muestras
from core.cambios import a_texto, exigir_permiso, fila_completa, nuevo_grupo, registrar
from core.capacidad import normalidad
from core.efectos import captado_por_turno, datos_por_turno, preparar_datos
from core.fase_pesada import PARAMETROS_FP, tabla_fase_pesada
from core.models import CATEGORIAS_CAMBIO_OPERATIVO, CambioOperativo, Equipo, Linea, Planta
from core.parametros import ParametrosIncompletos, validar_parametros
from core.produccion import tabla_produccion, toneladas_por_turno
from core.turnos import fecha_produccion, turno_de
from core.volatilizacion import eficiencias_por_dia

TABLA = "cambio_operativo"
CAMPOS_EDITABLES = ("fecha_hora", "equipo_id", "categoria", "descripcion")
DIAS_POR_DEFECTO = 10
ALFA = 0.05
TIPO_EBOE = "BOE"
SIN_EMULSION = pd.DataFrame(columns=["eboe", "fecha_hora", "aceite_emulsion"])
# (clave, nombre, unidad, decimales, por qué se cuenta, ámbito)
KPI = [("aceite_entrada", "Aceite en fruta de entrada", "kg/MT", 2, "análisis", "eBOE"),
       ("eficiencia", "Eficiencia eBOE", "%", 1, "par", "eBOE"),
       ("aceite_salida", "Aceite en fruta de salida", "kg/MT", 2, "análisis", "eBOE"),
       ("emulsion", "Aceite en emulsión", "%", 2, "análisis", "eBOE"),
       ("rendimiento", "Rendimiento en tambor", "kg/MT", 2, "día", "planta"),
       ("eficiencia_proceso", "Eficiencia de proceso", "%", 1, "día", "planta"),
       ("retorno_fp", "Retorno de FP a eBOE", "m³/h", 2, "turno", "planta"),
       ("captado", "Aceite captado por las primarias", "%", 1, "turno", "planta")]
COLUMNAS_LISTA = ["id", "fecha_hora", "fecha_produccion", "turno", "equipo_id", "equipo", "categoria", "descripcion",
                  "cargado_por", "cargado_en"]


# ---------------------------------------------------------------- carga, edición y borrado

def _validar(sesion, planta_id: int, fecha_hora, categoria: str, descripcion: str, equipo_id: int | None) -> None:
    if not isinstance(fecha_hora, datetime):
        raise ValueError("Falta la fecha y hora del cambio.")
    if categoria not in CATEGORIAS_CAMBIO_OPERATIVO:
        raise ValueError(f'Categoría "{categoria}" no válida. Usá una de: {", ".join(CATEGORIAS_CAMBIO_OPERATIVO)}.')
    if not (descripcion or "").strip():
        raise ValueError("La descripción del cambio es obligatoria.")
    if equipo_id is not None:
        equipo = sesion.get(Equipo, equipo_id)
        if equipo is None or equipo.linea.planta_id != planta_id:
            raise ValueError("El equipo no es de esta planta.")


def _usuario(usuario: str) -> str:
    if not (usuario or "").strip():
        raise ValueError("Falta el usuario (quién carga el cambio).")
    return usuario.strip()


def crear_cambio(sesion, planta_id: int, fecha_hora: datetime, categoria: str, descripcion: str, usuario: str,
                 ahora: datetime, equipo_id: int | None = None) -> CambioOperativo:
    """Carga un cambio operativo (y lo guarda). Error claro si falta la fecha, la descripción o el usuario, si la
    categoría no es una de CATEGORIAS_CAMBIO_OPERATIVO o si el equipo no es de la planta."""
    usuario = _usuario(usuario)
    _validar(sesion, planta_id, fecha_hora, categoria, descripcion, equipo_id)
    cambio = CambioOperativo(planta_id=planta_id, fecha_hora=fecha_hora, equipo_id=equipo_id, categoria=categoria,
                             descripcion=descripcion, cargado_por=usuario, cargado_en=ahora)
    sesion.add(cambio)
    sesion.commit()
    return cambio


def _cambio(sesion, cambio_id: int) -> CambioOperativo:
    cambio = sesion.get(CambioOperativo, cambio_id)
    if cambio is None:
        raise ValueError(f"No existe el cambio operativo {cambio_id}.")
    return cambio


def _motivo(motivo: str) -> str:
    if not (motivo or "").strip():
        raise ValueError("El motivo es obligatorio.")
    return motivo.strip()


def editar_cambio(sesion, cambio_id: int, campos: dict, motivo: str, usuario: str, ahora: datetime) -> int:
    """Cambia campos de un cambio operativo (CAMPOS_EDITABLES), con motivo: un registro de Cambio por campo que
    cambia. Devuelve cuántos campos cambiaron."""
    usuario, motivo = _usuario(usuario), _motivo(motivo)
    cambio = _cambio(sesion, cambio_id)
    exigir_permiso(usuario, cambio)
    desconocidos = [c for c in campos if c not in CAMPOS_EDITABLES]
    if desconocidos:
        raise ValueError(f"No se puede editar: {', '.join(desconocidos)}.")
    nuevos = {c: getattr(cambio, c) for c in CAMPOS_EDITABLES} | campos
    _validar(sesion, cambio.planta_id, nuevos["fecha_hora"], nuevos["categoria"], nuevos["descripcion"],
             nuevos["equipo_id"])
    grupo, cantidad = nuevo_grupo(), 0
    for campo, valor in campos.items():
        valor = valor.strip() if isinstance(valor, str) else valor
        anterior = getattr(cambio, campo)
        if a_texto(anterior) == a_texto(valor):
            continue
        registrar(sesion, TABLA, cambio.id, "edición", motivo, usuario, ahora, grupo, campo=campo, anterior=anterior,
                  nuevo=valor)
        setattr(cambio, campo, valor)
        cantidad += 1
    sesion.commit()
    return cantidad


def borrar_cambio(sesion, cambio_id: int, motivo: str, usuario: str, ahora: datetime) -> None:
    """Borra un cambio operativo, con motivo; la fila completa queda en el historial."""
    usuario, motivo = _usuario(usuario), _motivo(motivo)
    cambio = _cambio(sesion, cambio_id)
    exigir_permiso(usuario, cambio)
    registrar(sesion, TABLA, cambio.id, "borrado", motivo, usuario, ahora, nuevo_grupo(), anterior=fila_completa(cambio))
    sesion.delete(cambio)
    sesion.commit()


def listar_cambios(sesion, planta_id: int, desde: date | None = None, hasta: date | None = None) -> pd.DataFrame:
    """Cambios operativos de la planta, en orden (días de producción desde–hasta, inclusive). Columnas: COLUMNAS_LISTA
    (fecha_produccion y turno calculados; equipo = nombre, vacío si es de la planta)."""
    planta = sesion.get(Planta, planta_id)
    filas = []
    for c in sesion.scalars(select(CambioOperativo).where(CambioOperativo.planta_id == planta_id)
                            .order_by(CambioOperativo.fecha_hora, CambioOperativo.id)):
        dia = fecha_produccion(planta, c.fecha_hora)
        if (desde is not None and dia < desde) or (hasta is not None and dia > hasta):
            continue
        filas.append({"id": c.id, "fecha_hora": c.fecha_hora, "fecha_produccion": pd.Timestamp(dia),
                      "turno": turno_de(planta, c.fecha_hora).nombre, "equipo_id": c.equipo_id,
                      "equipo": c.equipo.nombre if c.equipo else None, "categoria": c.categoria,
                      "descripcion": c.descripcion, "cargado_por": c.cargado_por, "cargado_en": c.cargado_en})
    tabla = pd.DataFrame(filas, columns=COLUMNAS_LISTA)
    tabla["fecha_produccion"] = pd.to_datetime(tabla["fecha_produccion"])
    return tabla


def periodo_de(fechas, periodos: pd.DataFrame) -> list:
    """Para cada fecha de producción, la etiqueta del período que la contiene (periodos: desde, hasta, etiqueta;
    inclusive), o None si no cae en ninguno. Sirve para ubicar la línea de un cambio en un eje de categorías (meses,
    semanas)."""
    resultado = []
    for fecha in pd.to_datetime(pd.Series(fechas)):
        dentro = periodos[(pd.to_datetime(periodos["desde"]) <= fecha) & (fecha <= pd.to_datetime(periodos["hasta"]))]
        resultado.append(dentro["etiqueta"].iloc[0] if len(dentro) else None)
    return resultado


# ---------------------------------------------------------------- antes / después

def comparar(antes, despues, alfa: float = ALFA) -> dict:
    """Media antes y después, diferencia (después − antes), % (sobre el antes), N y la prueba: Welch si los dos lados
    son normales (Anderson-Darling, p ≥ alfa) y Mann-Whitney si no. Sin prueba con menos de 2 datos de algún lado."""
    a = pd.to_numeric(pd.Series(antes), errors="coerce").dropna().to_numpy(dtype=float)
    d = pd.to_numeric(pd.Series(despues), errors="coerce").dropna().to_numpy(dtype=float)
    resultado = {"antes": float(a.mean()) if len(a) else None, "despues": float(d.mean()) if len(d) else None,
                 "n_antes": len(a), "n_despues": len(d), "diferencia": None, "pct": None, "prueba": None, "p": None}
    if len(a) and len(d):
        resultado["diferencia"] = resultado["despues"] - resultado["antes"]
        resultado["pct"] = resultado["diferencia"] / resultado["antes"] * 100 if resultado["antes"] else None
    if len(a) < 2 or len(d) < 2:
        return resultado
    normales = normalidad(a)["normal"] and normalidad(d)["normal"]
    if normales:
        resultado.update(prueba="Welch", p=float(stats.ttest_ind(a, d, equal_var=False).pvalue))
    else:
        resultado.update(prueba="Mann-Whitney", p=float(stats.mannwhitneyu(a, d, alternative="two-sided").pvalue))
    return resultado


def _lado(fechas: pd.Series, dia: pd.Timestamp, dias: int) -> pd.Series:
    """"antes" (los `dias` días previos al del cambio), "despues" (los siguientes) o None (el día del cambio o fuera)."""
    fechas = pd.to_datetime(fechas)
    antes = (fechas >= dia - pd.Timedelta(days=dias)) & (fechas < dia)
    despues = (fechas > dia) & (fechas <= dia + pd.Timedelta(days=dias))
    return pd.Series(np.where(antes, "antes", np.where(despues, "despues", None)), index=fechas.index)


def valores_kpi(sesion, planta: Planta, desde: datetime, hasta: datetime, eboe: int | None = None) -> dict:
    """Los valores de cada KPI (clave de KPI → DataFrame con fecha_produccion y valor) entre desde y hasta. eboe:
    solo ese eBOE en los KPI de los eBOE. Los KPI que no se pueden calcular (faltan parámetros o datos) quedan vacíos."""
    vacio = pd.DataFrame(columns=["fecha_produccion", "valor"])
    fruta = tabla_analisis(sesion, planta.id, desde=desde, hasta=hasta, con_plc=False)
    emulsion = tabla_emulsion(sesion, planta.id, desde=desde, hasta=hasta, con_plc=False)
    pares_planta = pares_con_emulsion(fruta, SIN_EMULSION)
    del_eboe = fruta if eboe is None else fruta[fruta["eboe"] == eboe]
    pares = pares_planta if eboe is None else pares_planta[pares_planta["eboe"] == eboe]
    emulsion = emulsion if eboe is None else emulsion[emulsion["eboe"] == eboe]

    def tabla(datos, columna):
        return datos[["fecha_produccion", columna]].rename(columns={columna: "valor"}).dropna()

    valores = {"aceite_entrada": tabla(del_eboe[del_eboe["lugar"] == "Entrada"], "aceite_fruta"),
               "eficiencia": tabla(pares, "eficiencia_pct"),
               "aceite_salida": tabla(del_eboe[del_eboe["lugar"] == "Salida"], "aceite_fruta"),
               "emulsion": tabla(emulsion, "aceite_emulsion")}
    produccion = tabla_produccion(sesion, planta.id)
    dias = eficiencias_por_dia(pares_planta, None, toneladas_por_turno(sesion, planta.id), produccion)
    valores["rendimiento"] = tabla(dias, "rendimiento")
    valores["eficiencia_proceso"] = tabla(dias, "eficiencia_proceso")
    muestras = tabla_muestras(sesion, planta.id, desde=desde, hasta=hasta)
    balance = balance_por_muestreo(por_muestreo(muestras)) if not muestras.empty else muestras
    valores["retorno_fp"] = vacio
    if planta.umbral_paralelo_lh is not None and not muestras.empty and not balance.empty:
        agua = agua_por_eboe(balance, primarias_por_turno(muestras, planta.umbral_paralelo_lh), fruta)
        valores["retorno_fp"] = tabla(agua, "agua_por_eboe_m3h")
    valores["captado"] = vacio
    try:
        parametros = validar_parametros(planta, PARAMETROS_FP)
    except ParametrosIncompletos:
        parametros = None
    if parametros is not None and not balance.empty and not pares_planta.empty:
        turnos = datos_por_turno(preparar_datos(tabla_fase_pesada(pares_planta, parametros)),
                                 captado_por_turno(balance))
        if "captado_pct" in turnos:
            valores["captado"] = tabla(turnos, "captado_pct")
    return valores


def antes_despues(sesion, cambio_id: int, dias: int = DIAS_POR_DEFECTO) -> pd.DataFrame:
    """Comparación de los KPI (KPI) en los `dias` días de producción antes y después del día del cambio (sin ese
    día). Columnas: clave, kpi, unidad, decimales, por (análisis, par, día o turno), ambito (eBOE n.º o Planta) y las
    de comparar()."""
    if dias < 1:
        raise ValueError("La ventana tiene que ser de al menos 1 día.")
    cambio = _cambio(sesion, cambio_id)
    planta = cambio.planta
    dia = pd.Timestamp(fecha_produccion(planta, cambio.fecha_hora))
    inicio = datetime.combine((dia - pd.Timedelta(days=dias)).date(), planta.hora_inicio_dia)
    fin = datetime.combine((dia + pd.Timedelta(days=dias + 1)).date(), planta.hora_inicio_dia)
    eboe = cambio.equipo.numero if cambio.equipo is not None and cambio.equipo.tipo == TIPO_EBOE else None
    valores = valores_kpi(sesion, planta, inicio, fin, eboe)
    filas = []
    for clave, nombre, unidad, decimales, por, ambito in KPI:
        datos = valores[clave]
        lado = _lado(datos["fecha_produccion"], dia, dias) if len(datos) else pd.Series(dtype=object)
        filas.append({"clave": clave, "kpi": nombre, "unidad": unidad, "decimales": decimales, "por": por,
                      "ambito": f"eBOE {eboe}" if ambito == "eBOE" and eboe is not None else "Planta",
                      **comparar(datos.loc[lado == "antes", "valor"] if len(datos) else [],
                                 datos.loc[lado == "despues", "valor"] if len(datos) else [])})
    return pd.DataFrame(filas)


def equipos_de_planta(sesion, planta_id: int) -> dict[int, str]:
    """{id: nombre} de los equipos de la planta, para elegir a qué equipo corresponde un cambio."""
    return {e.id: e.nombre for e in sesion.scalars(select(Equipo).join(Linea).where(Linea.planta_id == planta_id)
                                                   .order_by(Equipo.codigo))}


def texto_cambio(fila) -> str:
    """"10/08/2025 08:00 · centrífugas · ESE 230 (2): reemplaza a la (1)" (para el tooltip de la línea del cambio)."""
    equipo = f" · {fila['equipo']}" if isinstance(fila.get("equipo"), str) and fila["equipo"] else ""
    return f"{fila['fecha_hora']:%d/%m/%Y %H:%M} · {fila['categoria']}{equipo}: {fila['descripcion']}"


def marcas(cambios: pd.DataFrame, posiciones: list) -> list[dict]:
    """Las marcas de los cambios para graficos.marcar_cambios: una por cambio con su posición en el eje X (la que
    calculó quien llama, por ejemplo con periodo_de; None = fuera del gráfico, no se dibuja), su categoría y el texto
    del tooltip."""
    return [{"x": x, "categoria": fila["categoria"], "texto": texto_cambio(fila)}
            for x, fila in zip(posiciones, cambios.to_dict("records")) if x is not None]
