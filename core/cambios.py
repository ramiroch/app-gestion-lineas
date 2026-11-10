"""Historial de cambios (tabla Cambio) y permiso de edición.

Liviano a propósito (solo modelos): lo usan core.edicion, core.gestion_alertas y los importadores, que no pisan ni
vuelven a crear datos con historial en Cambio ("corregido en la app").
- puede_editar(): el ÚNICO punto donde se decide si un usuario puede cambiar un dato. Hoy siempre sí; en la tarea 22:
  laboratorio solo lo que cargó y en las últimas 24 h, ingeniero todo.
- puede_aprobar(): el ÚNICO punto donde se decide si un usuario puede aprobar (o devolver a borrador) un registro de
  producción. Hoy siempre sí; en la tarea 22: solo ingeniero o jefe.
- clave_de(), fila_completa(), desde_fila(): la clave (punto o equipo + fecha_hora) y la fila completa como texto
  JSON (para el borrado y la restauración).
- registrar(): un registro de Cambio. claves_protegidas(): lo que los importadores no tienen que tocar.
"""

import json
import uuid
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import AnalisisScott, Cambio, LecturaOperacion

SISTEMA = "sistema"
TABLAS = {"analisis_scott": AnalisisScott, "lectura_operacion": LecturaOperacion}
# Columna que, con fecha_hora, identifica el dato (la clave única de la tabla)
COLUMNA_CLAVE = {"analisis_scott": "punto_muestreo_id", "lectura_operacion": "equipo_id"}


def puede_editar(usuario: str, dato) -> bool:
    """True si el usuario puede editar, borrar o restaurar el dato. Hoy siempre True (tarea 22: permisos por rol)."""
    return True


def puede_aprobar(usuario: str, registro) -> bool:
    """True si el usuario puede aprobar o devolver a borrador el registro de producción. Hoy siempre True (tarea 22:
    solo ingeniero o jefe)."""
    return True


def exigir_aprobacion(usuario: str, registro) -> None:
    """Error claro si puede_aprobar() dice que no."""
    if not puede_aprobar(usuario, registro):
        raise PermissionError(f"{usuario} no tiene permiso para aprobar registros de producción.")


def exigir_permiso(usuario: str, dato) -> None:
    """Error claro si puede_editar() dice que no."""
    if not puede_editar(usuario, dato):
        raise PermissionError(f"{usuario} no tiene permiso para cambiar este dato.")


def nuevo_grupo() -> str:
    """Identificador de un guardado (junta sus registros de Cambio)."""
    return uuid.uuid4().hex


def clave_de(tabla: str, dato) -> str:
    """"12|2026-11-10 06:10:00": punto de muestreo (análisis) o equipo (lectura) y fecha_hora."""
    return f"{getattr(dato, COLUMNA_CLAVE[tabla])}|{dato.fecha_hora:%Y-%m-%d %H:%M:%S}"


def a_texto(valor) -> str | None:
    """Un valor como texto para el historial (fechas en ISO; vacío = None)."""
    if valor is None:
        return None
    if isinstance(valor, (datetime, date, time)):
        return valor.isoformat()
    return str(valor)


def fila_completa(dato) -> str:
    """Todas las columnas del dato como JSON (fechas en ISO): así se guarda un dato borrado."""
    return json.dumps({c.key: a_texto(getattr(dato, c.key)) if isinstance(getattr(dato, c.key), (datetime, date, time))
                       else getattr(dato, c.key) for c in dato.__table__.columns}, ensure_ascii=False)


def desde_fila(modelo, texto: str) -> dict:
    """Los valores de una fila_completa() con sus tipos (fechas, horas) para volver a crear el dato."""
    datos = json.loads(texto)
    for columna in modelo.__table__.columns:
        valor = datos.get(columna.key)
        if valor is None:
            continue
        tipo = columna.type.python_type
        if tipo is datetime:
            datos[columna.key] = datetime.fromisoformat(valor)
        elif tipo is date:
            datos[columna.key] = date.fromisoformat(valor)
        elif tipo is time:
            datos[columna.key] = time.fromisoformat(valor)
    return datos


def registrar(sesion: Session, tabla: str, dato_id: int, accion: str, motivo: str, usuario: str, ahora: datetime,
              grupo: str, clave: str | None = None, campo: str | None = None, anterior=None, nuevo=None) -> Cambio:
    """Agrega un registro de Cambio a la sesión (no guarda: lo hace quien llama)."""
    cambio = Cambio(tabla=tabla, dato_id=dato_id, clave=clave, accion=accion, campo=campo,
                    valor_anterior=anterior if accion == "borrado" else a_texto(anterior), valor_nuevo=a_texto(nuevo),
                    motivo=motivo, cambiado_por=usuario, cambiado_en=ahora, grupo=grupo)
    sesion.add(cambio)
    return cambio


def claves_protegidas(sesion: Session) -> set[tuple[str, tuple]]:
    """{(tabla, (punto o equipo, fecha_hora))} de los datos con historial en Cambio: la clave que tenían al cambiarse
    y la que tienen hoy (si siguen existiendo). Los importadores no los pisan ni los vuelven a crear."""
    protegidas = set()
    for tabla, dato_id, clave in sesion.execute(select(Cambio.tabla, Cambio.dato_id, Cambio.clave)
                                                .where(Cambio.tabla.in_(TABLAS))).all():
        if clave:
            numero, momento = clave.split("|")
            protegidas.add((tabla, (int(numero), datetime.fromisoformat(momento))))
        dato = sesion.get(TABLAS[tabla], dato_id)
        if dato is not None:
            protegidas.add((tabla, (getattr(dato, COLUMNA_CLAVE[tabla]), dato.fecha_hora)))
    return protegidas


def historial(sesion: Session, tabla: str | None = None, dato_ids=None, grupo: str | None = None) -> list[Cambio]:
    """Registros de Cambio (del más nuevo al más viejo), filtrados por tabla, datos o grupo."""
    consulta = select(Cambio).order_by(Cambio.cambiado_en.desc(), Cambio.id.desc())
    if tabla is not None:
        consulta = consulta.where(Cambio.tabla == tabla)
    if dato_ids is not None:
        consulta = consulta.where(Cambio.dato_id.in_(list(dato_ids)))
    if grupo is not None:
        consulta = consulta.where(Cambio.grupo == grupo)
    return list(sesion.scalars(consulta))
