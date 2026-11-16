"""Menú de la app (st.navigation): páginas agrupadas en secciones, con título e ícono.

Las rutas son las mismas que usan los enlaces entre páginas (st.switch_page, st.page_link: "pages/3_Control.py"), y
cada página conserva la dirección que tenía (url_path: /Analisis, /Control…), así siguen andando los enlaces
guardados y los query params. El Inicio es la página por defecto (la dirección raíz).
"""

import streamlit as st

INICIO = "inicio.py"
# sección → [(ruta desde app/, título en el menú, ícono, dirección)]
MENU = {
    "Planta": [(INICIO, "Inicio", ":material/home:", ""),
               ("pages/1_Planta.py", "Planta", ":material/factory:", "Planta")],
    "Análisis": [("pages/2_Analisis.py", "Análisis", ":material/insights:", "Analisis"),
                 ("pages/3_Control.py", "Gráficos de control", ":material/monitoring:", "Control"),
                 ("pages/4_Laboratorio.py", "Laboratorio", ":material/science:", "Laboratorio"),
                 ("pages/5_Operacion.py", "Operación", ":material/precision_manufacturing:", "Operacion"),
                 ("pages/6_Centrifugas.py", "Centrífugas", ":material/cyclone:", "Centrifugas")],
    "Carga diaria": [("pages/7_Carga_Scott.py", "Carga Scott", ":material/edit_note:", "Carga_Scott"),
                     ("pages/8_Datos.py", "Datos", ":material/table_edit:", "Datos"),
                     ("pages/10_Produccion.py", "Producción", ":material/inventory_2:", "Produccion")],
    "Gestión": [("pages/9_Alertas.py", "Alertas", ":material/notifications:", "Alertas")],
}


def paginas() -> dict[str, list]:
    """{sección: [st.Page]} para st.navigation, en el orden de MENU."""
    return {seccion: [st.Page(ruta, title=titulo, icon=icono, default=ruta == INICIO,
                              **({} if ruta == INICIO else {"url_path": direccion}))
                      for ruta, titulo, icono, direccion in lista]
            for seccion, lista in MENU.items()}
