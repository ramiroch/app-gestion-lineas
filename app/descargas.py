"""Descargas de la página Análisis: botón "PNG" en cada gráfico, "Excel" en cada tabla y "Descargar todo".

Cada pestaña crea un Descargas y dibuja sus gráficos y tablas con él: así cada uno lleva su botón y
queda anotado para el ZIP de "Descargar todo" (todos los PNG y un Excel con todas las tablas de la
pestaña). Los archivos se generan recién al hacer clic (st.download_button con una función), así la
página no se hace lenta.
"""

import kaleido
import pandas as pd
import streamlit as st

from core.exportar import excel_de_tablas, nombre_de_archivo, zip_de_archivos
from graficos import ANCHO_DIBUJO_PNG, ANCHO_DIBUJO_PNG_MEDIO, png

MIME_EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@st.cache_resource
def iniciar_servidor_png() -> bool:
    """Deja Chrome abierto para kaleido una sola vez: cada PNG tarda ~0,5 s en lugar de ~2,5 s."""
    kaleido.start_sync_server(silence_warnings=True)
    return True


class Descargas:
    def __init__(self, pestana: str):
        iniciar_servidor_png()
        self.pestana = pestana
        self.figuras: dict[str, tuple] = {}     # nombre → (figura, ancho de dibujo del PNG)
        self.tablas: dict[str, pd.DataFrame] = {}

    def _nombre_unico(self, usados: dict, nombre: str) -> str:
        candidato, numero = nombre, 2
        while candidato in usados:
            candidato, numero = f"{nombre} ({numero})", numero + 1
        return candidato

    def _clave(self, tipo: str, nombre: str) -> str:
        return f"descarga_{tipo}_{nombre_de_archivo(self.pestana)}_{nombre_de_archivo(nombre)}"

    def grafico(self, figura, nombre: str, donde=None, **opciones):
        """Dibuja el gráfico (st.plotly_chart) con un botón "PNG" debajo. Devuelve lo que devuelve
        st.plotly_chart (la selección, si se pasa on_select).

        Si se pasa `donde` (una columna), el gráfico ocupa media pantalla y el PNG se dibuja a ese ancho.
        """
        ancho = ANCHO_DIBUJO_PNG if donde is None else ANCHO_DIBUJO_PNG_MEDIO
        donde = donde or st
        nombre = self._nombre_unico(self.figuras, nombre)
        self.figuras[nombre] = (figura, ancho)
        evento = donde.plotly_chart(figura, width="stretch", **opciones)
        donde.download_button("PNG", data=lambda: png(figura, ancho), file_name=f"{nombre_de_archivo(nombre)}.png",
                              mime="image/png", on_click="ignore", key=self._clave("png", nombre),
                              icon=":material/download:", help="Imagen de 1600 px de ancho, fondo blanco")
        return evento

    def tabla(self, mostrada, nombre: str, donde=None) -> None:
        """Dibuja la tabla (st.dataframe, sin índice) con un botón "Excel" debajo.

        mostrada puede ser un DataFrame o un Styler; al Excel va el DataFrame, con los números como números.
        """
        donde = donde or st
        nombre = self._nombre_unico(self.tablas, nombre)
        datos = mostrada.data if hasattr(mostrada, "data") and isinstance(mostrada.data, pd.DataFrame) else mostrada
        self.tablas[nombre] = datos
        donde.dataframe(mostrada, hide_index=True)
        donde.download_button("Excel", data=lambda: excel_de_tablas({nombre: datos}),
                              file_name=f"{nombre_de_archivo(nombre)}.xlsx", mime=MIME_EXCEL, on_click="ignore",
                              key=self._clave("excel", nombre), icon=":material/download:")

    def contenido_zip(self) -> bytes:
        """ZIP con un PNG por gráfico y, si hay tablas, un tablas.xlsx con una hoja por tabla."""
        archivos = {f"{nombre_de_archivo(nombre)}.png": png(figura, ancho)
                    for nombre, (figura, ancho) in self.figuras.items()}
        if self.tablas:
            archivos["tablas.xlsx"] = excel_de_tablas(self.tablas)
        return zip_de_archivos(archivos)

    def boton_todo(self, donde) -> None:
        """Botón "Descargar todo" (se dibuja en `donde`, por ejemplo un contenedor arriba de la pestaña)."""
        partes = []
        if self.figuras:
            partes.append(f"{len(self.figuras)} gráficos en PNG")
        if self.tablas:
            partes.append(f"{len(self.tablas)} tablas en un Excel")
        donde.download_button("Descargar todo", data=self.contenido_zip,
                              file_name=f"analisis_{nombre_de_archivo(self.pestana)}.zip", mime="application/zip",
                              on_click="ignore", key=self._clave("zip", "todo"), type="primary",
                              icon=":material/folder_zip:", disabled=not partes,
                              help="ZIP con " + " y ".join(partes) if partes else "No hay nada para descargar")
