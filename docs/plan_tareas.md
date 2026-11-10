# Plan de tareas – Primera planta LEDESMA (hasta el 30/11/2026)

Fuente: `Plan diario - Primera planta LEDESMA.xlsx` (App_Gestion). Bloques de 2 h, lunes a viernes.

## Tarea 1 · Git, GitHub y Claude Code (26/10) — Hecha
- **Qué hacer:** Instalar Git for Windows. Crear cuenta en GitHub y un repositorio privado "app-gestion-lineas". Clonarlo en VS Code. Instalar la extensión de Claude Code en VS Code e iniciar sesión.
- **Listo cuando:** El repo está en tu PC y en GitHub, con un README. Claude Code responde dentro de VS Code.
- **Notas:** Terminada (pasos 1–5).

## Tarea 2 · Estructura del proyecto y entorno virtual (27/10) — Hecha
- **Qué hacer:** Crear el entorno virtual (venv), requirements.txt y las carpetas. Copiar al repo el CLAUDE.md (contexto del proyecto) y los docs 01 y 02. Primera pantalla "Hola LEDESMA" en Streamlit.
- **Listo cuando:** Corrés "streamlit run app.py" y se abre una página en el navegador.
- **Notas:** Terminada el 27/10. Python 3.13; se habilitó ExecutionPolicy RemoteSigned.

## Tarea 3 · Modelo de datos v1: planta y equipos (28/10) — Hecha
- **Qué hacer:** Tablas Cliente, Planta, Línea, Equipo y PuntoMuestreo con SQLAlchemy sobre SQLite. Script que crea la base.
- **Listo cuando:** Existe data/app.db con las 5 tablas y un test que crea una planta de prueba.
- **Notas:** 28/10: 5 tablas, 3 tests OK. Auditoría (cargado_por/en) se agrega con la carga de análisis.

## Tarea 4 · Datos maestros de todos los clientes (desde MC) (29/10) — Hecha
- **Qué hacer:** Revisar data/maestros_clientes.xlsx (generada desde el listado de Maintenance Connection): completar lo amarillo, resolver las observaciones en rojo y confirmar líneas. Pedirle a Claude Code que agregue a models.py: Cliente.grupo, Equipo.nombre, estado, serie_mc y numero_activo_fijo.
- **Listo cuando:** Planilla revisada en data/ y models.py actualizado con tests en verde.
- **Notas:** 29-30/10: planilla de maestros revisada + campos nuevos en models.py.

## Tarea 5 · Carga de datos maestros + pantalla de planta (30/10) — Hecha
- **Qué hacer:** Script que importa la planilla a la base. Pantalla Streamlit que muestra el árbol Planta → Línea → Equipo → Punto de muestreo. Commit y push. Revisión de la semana (15 min).
- **Listo cuando:** Ves LEDESMA completa en la app.
- **Notas:** 30/10: importador de maestros, código fijo por equipo, parámetros "a definir", pip install -e. 43 tests.

## Tarea 6 · Modelos: análisis, operación, producción y turnos (02/11) — Hecha
- **Qué hacer:** Una sola tarea para todo el modelo que necesita el histórico: Turno (M/T/N), AnalisisScott (crudos + fruta + emulsión + cargado_por/en), LecturaOperacion (programa, flip gate, t/h, caudal fase pesada, operador), Proceso y RegistroProduccion por turno (fruta t, aceite envasado kg, jugo conc kg, reproceso). Funciones turno_de() y fecha_produccion().
- **Listo cuando:** pytest en verde, incluidos los bordes de turno (04:59, 05:00, 21:00, 00:30) y la muestra del 25/06 02:00 → día 24/06, turno N.
- **Notas:** Modelos Turno, Proceso, AnalisisScott, LecturaOperacion, RegistroProduccion. 72 tests.

## Tarea 7 · Cálculos Brown + importador del histórico LEDESMA (03/11) — Hecha
- **Qué hacer:** core/calculos.py (aceite en fruta, aceite en emulsión, eficiencia y rendimiento eBOE, usando parametros_planta) con tests contra filas reales del Excel. core/importar_ledesma.py: hoja BOE (empareja Entrada/Salida) y Resumen por turno; normaliza textos y horas en texto; reporte de rechazos.
- **Listo cuando:** Importación completa sin errores graves; data/reporte_importacion.csv lista las 7 Entradas sin Salida y las horas corregidas.
- **Notas:** 03/11: importador de las hojas BOE y Resumen con reporte de rechazadas, corregidas y avisos. 117 tests.

## Tarea 8 · Validación + estadística descriptiva (04/11) — Hecha
- **Qué hacer:** Test que reproduce la tabla de la sección 7 del mapeo (eficiencia y rendimiento por eBOE y temporada). Página "Análisis" con filtros (temporada, eBOE, proceso, turno): histograma de aceite en fruta Entrada vs Salida, boxplot por eBOE y lugar, tabla de estadística (N, media, desvío, mín, Q1, mediana, Q3, máx) y límites de outliers por IQR. Igual que las diapositivas 2–5 de la presentación de septiembre. Filtros globales interactivos (fechas, eBOE, turno, proceso, programa) compartidos por todas las páginas, como en Power BI.
- **Listo cuando:** La app reproduce la eficiencia por eBOE y temporada de la sección 7 del mapeo y los gráficos se ven como los de septiembre.
- **Notas:** 04/11: validación OK (promedio simple y ponderado por toneladas), filtros globales, página Análisis, kaleido. 142 tests.

## Tarea 9 · Comparaciones: temporadas y eficiencia vs programa y t/h (05/11) — Hecha
- **Qué hacer:** ANOVA de un factor + Tukey entre temporadas (aceite en fruta, eficiencia eBOE, aceite en emulsión) con gráfico de intervalos al 95 %. Burbujas: eficiencia promedio vs t/h, color por programa, tamaño por N, una por eBOE. Superficie y contorno de eficiencia vs programa y t/h. Igual que las diapositivas 6–13.
- **Listo cuando:** Resultados de ANOVA iguales a Minitab (con un caso cargado a mano) y los 3 tipos de gráfico funcionando por eBOE.
- **Notas:** 04/11: Temporadas (ANOVA/Welch/Tukey), Agua y licor (regresión por temporada), Programa y MTPH (burbujas, superficie, ranking, filtrado cruzado). Unidades kg/MT y MTPH. 173 tests.

## Tarea 10 · Vista de proceso por temporada + exportar para la presentación (06/11) — Hecha
- **Qué hacer:** Pestaña "Proceso": por temporada, tarjetas por eBOE (aceite entrada y salida kg/t, t/h, eficiencia) y totales (eficiencia de proceso, fruta procesada, aceite envasado, rendimiento de aceite), como el flowsheet de septiembre. Botón para descargar cada gráfico en PNG y las tablas en Excel. Revisión de la semana.
- **Listo cuando:** Todos los gráficos de la reunión se descargan desde la app.
- **Notas:** Pestaña Proceso (KPI por eBOE, totales, comparación de períodos) + descargas PNG/Excel/ZIP.

## Tarea 11 · Presentación de naranja para LEDESMA (09/11) — Pendiente
- **Qué hacer:** Sin código: armar la presentación con el formato de septiembre usando lo exportado de la app. Escribir las conclusiones (aceite en fruta, eficiencia por eBOE, programa y t/h, emulsión, comparación de temporadas).
- **Listo cuando:** Presentación lista para revisar.
- **Notas:** Postergada hasta tener fecha de reunión.

## Tarea 12 · Reserva / ajustes después de la revisión (10/11) — Pendiente
- **Qué hacer:** Lo que haya quedado pendiente de la semana 2 o lo que surja al armar la presentación.
- **Listo cuando:** Nada atrasado.

## Tarea 13 · Cartas de control: cálculo y reglas de Nelson (11/11) — Hecha
- **Qué hacer:** core/spc.py: límites I-MR, X̄-R y X̄-S con sus constantes; las 8 reglas de Nelson configurables. Tests contra Minitab.
- **Listo cuando:** Límites y violaciones iguales a Minitab.
- **Notas:** Adelantada: página Control (X̄-R/X̄-S, I-MR, Nelson, fase I 2025 / fase II 2026).

## Tarea 14 · Pantalla de cartas de control (12/11) — Hecha
- **Qué hacer:** Pestaña "Cartas" en Análisis: variable, eBOE, período; carta I-MR en dos paneles con violaciones marcadas por forma y color y tooltip con fecha, turno y analista.
- **Listo cuando:** Carta I-MR de eficiencia de un eBOE con datos reales.
- **Notas:** Adelantada: página Control (X̄-R/X̄-S, I-MR, Nelson, fase I 2025 / fase II 2026).

## Tarea 15 · Capacidad de proceso: cálculo (13/11) — Hecha
- **Qué hacer:** core/capacidad.py: Cp, Cpk, Pp, Ppk, % fuera de especificación, Anderson-Darling y Box-Cox.
- **Listo cuando:** Iguales a Minitab en un caso normal y uno no normal.
- **Notas:** 07/11: core/capacidad.py (Cp/Cpk dentro, Pp/Ppk total, AD, Box-Cox, percentiles ISO 22514, estabilidad por binomial). 376 tests.

## Tarea 16 · Pantalla de capacidad (16/11) — Hecha
- **Qué hacer:** Pestaña "Capacidad": histograma con curva normal, LIE/LSE (de RangoReferencia) y tabla de índices.
- **Listo cuando:** Cp/Cpk de eficiencia por eBOE para un mes.
- **Notas:** 07/11: pestaña Capacidad en Gráficos de control (lado de especificación por variable, % observado primero, nota Gage R&R, descargas).

## Tarea 17 · Límites congelados y alertas (17/11) — Hecha
- **Qué hacer:** Tabla LimitesControl (fase I) y Alerta. Cada dato nuevo se evalúa contra los límites congelados.
- **Listo cuando:** Un dato fuera de control genera una alerta abierta.
- **Notas:** 08-09/11: límites entre/dentro (I-MR-R/S) por turno congelados con 2025; alertas por episodios (persistencia 2 turnos, ±4σ inmediata salvo emulsión, lado malo, comunes → planta, pH solo rango). Alertas "histórico" de 2026 cargadas. 426 tests.

## Tarea 18 · Formulario de carga de Scott (18/11) — Hecha
- **Qué hacer:** Página de carga: punto de muestreo, datos crudos, cálculo en vivo, guardar con cargado_por/en.
- **Listo cuando:** Cargás un análisis desde el navegador y aparece en Análisis.
- **Notas:** 10/11: core/carga_scott.py + página Carga Scott. Ronda con entrada, salida, emulsión y operación por cada eBOE en marcha (cada eBOE con su entrada); hora propuesta según la última ronda; normalidad por tipo de punto; cálculo en vivo con semáforo; errores y avisos (¿copiaste la entrada?, normalidad equivocada, muestra muy chica, valor poco habitual); chequeo inmediato del turno en curso y evaluación completa de los turnos cerrados (TurnoEvaluado). Entradas repetidas marcadas (core/repetidas.py). 443 tests.

## Tarea 19 · Validación de rangos y listado con edición (19/11) — Hecha
- **Qué hacer:** Al guardar, semáforo contra RangoReferencia. Lista de análisis con filtros y edición.
- **Listo cuando:** Un valor fuera de rango queda marcado.
- **Notas:** 10/11: página Datos (lista por ronda con filtros, corrección, borrado de un dato o de la ronda, cambio de hora, historial y restaurar; motivo obligatorio; tabla Cambio con la fila completa de lo borrado; permiso en un único punto, core.cambios.puede_editar) y página Alertas (filtros, carta del período, datos, causa / acción / comentario, estados). Al corregir: recálculo, marca de entrada repetida y reevaluación del turno; una alerta sin datos se cierra sola con "dato corregido". El importador no pisa ni recrea lo corregido en la app. Modo prueba con APP_DB. 463 tests.

## Tarea 20 · Carga de producción por turno con aprobación (20/11) — Pendiente
- **Qué hacer:** Formulario de RegistroProduccion (borrador → aprobado). Revisión de la semana.
- **Listo cuando:** Un registro aprobado entra en los KPIs; uno en borrador no.

## Tarea 21 · Día colchón (23/11) — Pendiente
- **Qué hacer:** Ponerse al día. (Verificar si es feriado.) Inicio con semáforo por equipo y alertas abiertas (checklist 12 y 13).
- **Listo cuando:** Nada atrasado.

## Tarea 22 · Usuarios y roles básicos (24/11) — Pendiente
- **Qué hacer:** Login simple; roles laboratorio, procesos e ingeniero.
- **Listo cuando:** Laboratorio no ve la configuración.

## Tarea 23 · Prueba con datos reales recientes (25/11) — Pendiente
- **Qué hacer:** Cargar desde el formulario 1–2 semanas de análisis reales de LEDESMA. Anotar lo incómodo.
- **Listo cuando:** Lista de mejoras.

## Tarea 24 · Corrección de errores y pruebas (26/11) — Pendiente
- **Qué hacer:** Corregir lo encontrado. Todo en verde. Respaldo de la base.
- **Listo cuando:** pytest en verde.

## Tarea 25 · Documentación (27/11) — Pendiente
- **Qué hacer:** README, manual de 1 página para el laboratorio, guion de demo. Revisión de la semana.
- **Listo cuando:** Otra persona podría instalarla con el README.

## Tarea 26 · Entrega: LEDESMA lista (v0.1) (30/11) — Pendiente
- **Qué hacer:** Checklist de aceptación, video de demo, tag v0.1.
- **Listo cuando:** Checklist completo y v0.1 en GitHub.

## Pendientes fuera del 30/11
- Formulario de carga de centrífugas (para la temporada 2027).
