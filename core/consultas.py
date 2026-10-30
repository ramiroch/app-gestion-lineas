"""Lecturas de la base para las pantallas.

Las funciones devuelven diccionarios y listas comunes (no objetos de SQLAlchemy),
así las páginas solo muestran datos y no dependen de la base.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core.models import Cliente, Equipo, Linea, Planta


def listar_clientes(sesion: Session) -> list[dict]:
    """Clientes ordenados por nombre: [{"id", "nombre"}]."""
    clientes = sesion.scalars(select(Cliente).order_by(Cliente.nombre)).all()
    return [{"id": c.id, "nombre": c.nombre} for c in clientes]


def listar_plantas(sesion: Session, cliente_id: int) -> list[dict]:
    """Plantas de un cliente ordenadas por nombre: [{"id", "nombre"}]."""
    plantas = sesion.scalars(
        select(Planta).where(Planta.cliente_id == cliente_id).order_by(Planta.nombre)).all()
    return [{"id": p.id, "nombre": p.nombre} for p in plantas]


def detalle_planta(sesion: Session, planta_id: int) -> dict | None:
    """Datos de una planta con sus líneas, equipos y puntos de muestreo.

    Devuelve None si la planta no existe. Las líneas van ordenadas por nombre,
    los equipos por código y los puntos en el orden en que se cargaron.
    """
    planta = sesion.scalars(
        select(Planta)
        .where(Planta.id == planta_id)
        .options(selectinload(Planta.cliente),
                 selectinload(Planta.lineas)
                 .selectinload(Linea.equipos)
                 .selectinload(Equipo.puntos_muestreo))
    ).first()
    if planta is None:
        return None
    return {
        "id": planta.id,
        "nombre": planta.nombre,
        "cliente": planta.cliente.nombre,
        "pais": planta.pais,
        "blanco_ml": planta.blanco_ml,
        "factor_aceite_fruta": planta.factor_aceite_fruta,
        "factor_aceite_emulsion": planta.factor_aceite_emulsion,
        "hora_inicio_dia": planta.hora_inicio_dia,
        "lineas": [
            {"nombre": linea.nombre,
             "equipos": [_equipo(e) for e in sorted(linea.equipos, key=lambda e: e.codigo)]}
            for linea in sorted(planta.lineas, key=lambda l: l.nombre)
        ],
    }


def _equipo(equipo: Equipo) -> dict:
    return {
        "codigo": equipo.codigo,
        "nombre": equipo.nombre,
        "tipo": equipo.tipo,
        "numero": equipo.numero,
        "modelo": equipo.modelo,
        "numero_serie": equipo.numero_serie,
        "estado": equipo.estado,
        "puntos": [{"tipo": p.tipo, "nombre": p.nombre}
                   for p in sorted(equipo.puntos_muestreo, key=lambda p: p.id)],
    }


def filas_equipos(equipos: list[dict]) -> list[dict]:
    """Arma las filas de la tabla de equipos: cada equipo y, debajo, sus puntos de muestreo.

    Cada fila tiene codigo, nombre, tipo, numero, modelo, numero_serie, estado y
    es_punto (True en las filas de puntos de muestreo, que solo llevan nombre y tipo).
    """
    filas = []
    for equipo in equipos:
        fila = {clave: equipo[clave] for clave in
                ("codigo", "nombre", "tipo", "numero", "modelo", "numero_serie", "estado")}
        fila["es_punto"] = False
        filas.append(fila)
        for punto in equipo["puntos"]:
            filas.append({"codigo": "", "nombre": f"↳ {punto['nombre']}", "tipo": punto["tipo"],
                          "numero": None, "modelo": None, "numero_serie": None, "estado": None,
                          "es_punto": True})
    return filas
