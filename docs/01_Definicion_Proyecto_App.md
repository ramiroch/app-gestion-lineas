# Proyecto App de Servicio Técnico – Paso 1: Definición y arquitectura

**Estado:** borrador v0.2 · 05/10/2026. Se agregaron los datos del PLC, los KPIs de confiabilidad, los datos de producción con aprobación y la ciberseguridad.
**Responsable:** Ramiro (Tech Service Engineer)

---

## 1. Problema

- Hay un solo ingeniero de servicio técnico para muchos clientes (plantas citrícolas) en varios países.
- Cada cliente registra y procesa sus datos a su manera y manda archivos con formatos distintos: Excel, PDF, fotos de planillas, mails.
- Unificar, revisar y analizar esos datos consume la mayor parte del tiempo. Queda poco para diagnosticar y asesorar.
- El mantenimiento se gestiona en **Maintenance Connection (Accruent)**, que es de uso interno: los clientes no tienen acceso.

## 2. Objetivo

Construir un **sistema de gestión integrado** en el que cada cliente cargue sus datos en un **formato único**, y que con eso:

1. Elimine el trabajo manual de unificar formatos.
2. Muestre en tiempo real el estado de cada planta y equipo (BOE, mBOE, JEX, desludger, pulidora).
3. Avise automáticamente cuando un valor sale de rango; por ejemplo, la eficiencia del desludger o el °Brix de la fase media.
4. Permita al ingeniero diagnosticar a distancia, con un asistente de IA que conoce los manuales Brown y los datos históricos.
5. Vincule los hallazgos con el mantenimiento en Maintenance Connection.

## 3. Usuarios y roles

| Rol | Quién | Qué hace |
|---|---|---|
| **Administrador / ingeniero de servicio** | Ramiro (y en el futuro otros técnicos) | Ve **todos** los clientes, configura plantas, equipos y rangos, revisa alertas, emite recomendaciones y gestiona el mantenimiento |
| **Laboratorio del cliente** | Analistas de cada planta | Cargan los análisis: Scott, °Brix, sólidos y spin test |
| **Ingeniero de procesos / jefe de planta del cliente** | Usuario **habilitado** por el cliente | Carga y **aprueba** los datos de producción: fruta procesada, kg de aceite, jugo y subproductos. Valida el cierre diario o semanal |
| **Operador o supervisor del cliente** | Jefe de planta o de turno | Carga parámetros de operación (t/h, programa del eBOE, presiones, disparos), ve sus dashboards y recibe recomendaciones |
| **Gerencia del cliente** (opcional) | Gerente de planta | Solo ve reportes y KPIs |

**Reglas clave:**

- Cada cliente ve **solo sus propios datos**. El administrador ve todo, pero el acceso de soporte queda registrado (ver sección 9).
- **Separación de fuentes:** el laboratorio carga análisis; los datos de producción solo los carga o aprueba un usuario con rol de ingeniero de procesos, jefe o gerente. Los datos de PLC entran automáticamente, sin intervención manual.
- Un dato de producción no entra en los KPIs oficiales hasta que está **aprobado**. Mientras tanto se ve como "borrador".

## 4. Módulos

| # | Módulo | Contenido | Prioridad |
|---|---|---|---|
| 1 | **Controles analíticos** | Carga de análisis por punto de muestreo con fórmulas automáticas (Scott, eficiencias) y validación de rangos | **MVP** |
| 2 | **Control de equipos** | Inventario de plantas, líneas y equipos, con parámetros de operación por turno e historial | MVP (básico) |
| 3 | **Dashboards y alertas** | KPIs por planta y país: remoción del BOE, eficiencia del desludger y la pulidora, aceite en barros, °Brix. Tendencias y comparación entre plantas | MVP |
| 3b | **Datos de PLC (BOE, mBOE, JEX)** | Lectura automática de variables de los PLC: velocidades y corrientes de rodillos, programas, t/h, fallas, horas de marcha, nivel de agua, alarmas del OHWS, carga de la JEX | Fase 2 (piloto en 1 planta) |
| 3c | **Producción y balance** | Fruta procesada, aceite obtenido (tambores), jugo y subproductos, con flujo de carga y aprobación por rol | MVP (manual) |
| 3d | **KPIs de confiabilidad** | MTBF, MTTR, disponibilidad, fallas por equipo y componente, horas por pieza de desgaste (a partir del PLC y de Maintenance Connection) | Fase 2–3 |
| 4 | **Asistente IA** | Responde preguntas sobre los datos y los manuales, sugiere causas probables (tablas de fallas Brown) y redacta informes de visita | Fase 2 |
| 5 | **Importador inteligente** | Para los clientes que sigan mandando archivos: la IA lee el Excel, PDF o foto y lo transforma al formato único, con revisión humana | Fase 2 |
| 6 | **Mantenimiento** | Registro de hallazgos y recomendaciones, sincronizado con Maintenance Connection | Fase 3 |
| 7 | **Multi-idioma y multi-país** | Español, inglés y portugués. Unidades métricas e imperiales | Fase 3 |

## 5. Modelo de datos (núcleo)

Este es el corazón del proyecto: **un solo formato para todos los clientes**.

```
Cliente ──< Planta ──< Línea ──< Equipo ──< Lectura de operación
   (país)     (fruta)                │
                                     └──< Punto de muestreo ──< Análisis ──< Resultado calculado
Equipo ──< Variable PLC (tag) ──< Serie de tiempo (valor, timestamp, calidad)
Equipo ──< Evento PLC (falla, alarma, arranque, parada) ──> Indicador de confiabilidad
Planta ──< Registro de producción (día/turno) ── estado: borrador → aprobado
Planta ──< Alerta
Planta ──< Visita / Recomendación ──> (Orden de trabajo en Maintenance Connection)
Tipo de equipo ──< Rango de referencia (por fruta y país)
```

### Entidades principales

| Entidad | Campos clave |
|---|---|
| **Cliente** | nombre, país, idioma, unidades, contactos |
| **Planta** | cliente, ciudad, frutas que procesa (limón, naranja, pomelo, mandarina, lima), temporada |
| **Línea** | planta, nombre, configuración (por ejemplo "2 × eBOE + mBOE en serie + desludger 3 etapas") |
| **Equipo** | línea, tipo (BOE 6107B/E, mBOE 6108, JEX 620/720, finisher 202, desludger, pulidora), modelo, n.º de serie, **código del activo en Maintenance Connection** |
| **Punto de muestreo** | equipo, tipo (fruta entrada BOE, fruta salida BOE, emulsión entrada a la centrífuga primaria, fase liviana, fase pesada, descarga, entrada a la pulidora, descarga de la pulidora, fase media, °Brix) |
| **Análisis** | punto, fecha y hora, turno, analista, método (Scott), normalidad, peso de la muestra (g), mL gastados, blanco, factor de dilución |
| **Resultado** | % de aceite o kg/t **calculado por el sistema**, con la fórmula de la técnica: `(mL − bco) × N × factor / g` |
| **Lectura de operación** | equipo, fecha y hora, t/h, programa o diferencial, presión de aire de la JEX, contrapresión, ciclo de disparo, caudal |
| **Rango de referencia** | variable, fruta, país, mínimo, máximo, fuente (manual Brown, página) |
| **Variable PLC (tag)** | equipo, nombre estándar (por ejemplo `roll_17_rpm`), dirección en el PLC, unidad, frecuencia de muestreo, rango |
| **Evento PLC** | equipo, tipo (Start Fault, Roll Fault, Low Water, sobrecarga JEX, alarma OHWS, ESTOP), inicio, fin, duración, componente |
| **Registro de producción** | planta, fecha, turno, fruta (t, por variedad), descarte, aceite obtenido (kg), jugo (t o L, °Brix), cáscara o subproductos, origen del dato, **cargado por**, **aprobado por**, fecha de aprobación |
| **Alerta** | resultado fuera de rango, severidad, estado (abierta, vista, resuelta), causa probable |
| **Recomendación** | planta, equipo, texto, autor, fecha, vínculo a una OT de Maintenance Connection |

**Por qué guardar los datos crudos** (mL, g, N) y no solo el resultado: así el sistema recalcula con la fórmula correcta, detecta errores de cálculo del cliente y unifica criterios. Por ejemplo, la planta titula con 0,01 N y la referencia de Brown es 0,100 N; el sistema las convierte igual.

## 6. Cálculos automáticos (de la base de conocimiento)

| KPI | Fórmula | Rango de referencia (limón, Argentina) |
|---|---|---|
| Aceite en fruta (kg/t) | (mL − bco) × N × 68,841 / g | Disponible: 4–8 kg/t |
| Aceite en licor o fase (%) | (mL − bco) × N × 3,442 / g | Según el punto |
| Remoción del BOE (%) | 100 × (entrada − salida) / entrada | 85–90 % |
| Eficiencia simple del desludger (%) | (alimentación − fase media) / alimentación × 100 | 94–98 % |
| Eficiencia real del desludger | [(F×f) − (M×m) − (S×s)] / (F×f) × 100 | — |
| Eficiencia de la pulidora | (E − S) / [E − (S×E/100)] × 100 | 99 % |
| Alimentación del desludger | — | 0,70–0,90 % |
| Fase liviana del primario | — | 75–85 % |
| Fase media | — | 0,05–0,10 % |
| Barros del desludger | — | 0,30–0,50 % |
| °Brix de la fase media | — | < 4,0 (3,0 indica que hay que vaciar el BOE) |

Los rangos se cargan por fruta y país, y cada uno queda vinculado a la fuente en la base de conocimiento: `equipos/BOE_Brown_Oil_Extractor.md` y `tecnicas_de_laboratorio/Determinacion_aceite_metodo_Scott.md`.

### KPIs que se habilitan al cruzar fuentes

| KPI | Fuentes |
|---|---|
| **Recuperación de aceite en tambor (%)** = kg de aceite obtenido / (t de fruta × aceite disponible kg/t) | Producción (aprobada) + Laboratorio |
| Rendimiento por t de fruta (kg de aceite/t, L de jugo/t) | Producción |
| Remoción del BOE frente a t/h real y programa del eBOE | Laboratorio + PLC |
| Pérdidas en centrífugas (kg/h, kg/t) | Laboratorio + Producción |
| Consumo y carga de la JEX frente a fruta procesada | PLC + Producción |

## 6b. Datos de PLC

### Qué hay disponible (según los manuales)

| Equipo | Variables útiles | Uso |
|---|---|---|
| **eBOE 6107E / mBOE 6108** | RPM y corriente de cada uno de los 40 (o 12) rodillos, programa activo (1–10) y paso, t/h configuradas, baldes/min, kg por balde, flip gate arriba/abajo, estado del indexador, barredor y secador, horímetro, temporizador de engrase, fallas (Start Fault, Roll Fault, **Low Water**), alarmas del OHWS (válvulas, sensores de proximidad, VFD, ESTOP) y etapa del CIP | Proceso: relacionar programa y t/h con remoción. Confiabilidad: fallas por rodillo, motores que consumen de más (aviso de reemplazo), horas de marcha |
| **bBOE 6107B** | Estado y velocidad del motor principal, tiempo de marcha, fallas | Disponibilidad |
| **JEX 620/720** | Marcha/parada (sensor de fruta), horas, **monitor electrónico de carga (ELM)**, disparos de sobrecarga, presión de aire (si está instrumentada) | Confiabilidad: horas por pieza de desgaste (cuchilla 500/1.000 h, escariadores 2.000 h, copas 3.000 h, aceite del cabezal 300 h). Proceso: carga frente a fruta |

**Dato técnico:** los terminales de seguridad que nombra el manual del eBOE (EL1904, EL6910, TwinSAFE) son de **Beckhoff**, lo que sugiere un PLC con TwinCAT. Esa plataforma suele admitir **OPC UA** y **MQTT** como módulos de software. Hay que confirmarlo con el área de automatización de Brown.

### Cómo se lee el PLC (arquitectura propuesta)

```
[PLC eBOE / JEX] --(OPC UA, solo lectura)--> [Gateway edge en planta] --(MQTT/HTTPS saliente, cifrado TLS)--> [Nube de la app]
       Red OT del cliente                     DMZ industrial del cliente                       Internet
```

- Un **gateway edge** en la planta (PC industrial o equipo dedicado) lee los tags del PLC con **OPC UA en modo solo lectura**, guarda un buffer local si se corta internet y envía los datos agregados (por ejemplo, cada 1–60 s según la variable) **solo hacia afuera**.
- **La app nunca escribe en el PLC.** No hay control remoto de la máquina: es monitoreo.
- Se estandariza un **diccionario de tags** igual para todos los clientes (por ejemplo `BOE.roll[17].rpm`), aunque cada PLC tenga direcciones distintas. Es el mismo principio del "formato único" del laboratorio.
- **Piloto:** 1 planta con eBOE, para validar qué tags valen la pena antes de escalar.

## 6c. KPIs de confiabilidad (mantenimiento)

| KPI | Cálculo | Fuente |
|---|---|---|
| **Disponibilidad (%)** | Tiempo en marcha / (tiempo en marcha + tiempo detenido por falla) | Eventos PLC |
| **MTBF** (tiempo medio entre fallas) | Horas de marcha / n.º de fallas | PLC + OT de Maintenance Connection |
| **MTTR** (tiempo medio de reparación) | Suma de duraciones de paradas por falla / n.º de fallas | PLC + MC |
| Fallas por componente | Ranking de rodillos, VFD, sensores, levas | Eventos PLC + MC |
| Horas hasta el próximo cambio | Horas acumuladas frente al límite del manual (cuchilla JEX, escariadores, copas, aceite del cabezal, rodamientos) | Horímetro PLC + manuales |
| Anomalía de corriente | Corriente de un motor por encima de su histórico o de sus pares | PLC (predictivo) |
| Cumplimiento de preventivos | OT preventivas hechas en fecha / programadas | Maintenance Connection |

## 7. Integración con Maintenance Connection

- MC (Accruent) tiene una **API documentada** (api.maintenanceconnection.com). Falta confirmar con IT qué versión y qué permisos tiene la cuenta de Brown/ASB.
- Como solo lo usa Brown/ASB, la integración es **interna**:
  1. **Fase 3a (sin API):** exportar desde MC la lista de activos y OT a Excel/CSV e importarla en la app, para vincular cada equipo con su código de activo.
  2. **Fase 3b (con API):** desde una alerta o recomendación, crear la OT en MC con un clic. También consultar el historial de OT de un equipo desde la app.
- Los clientes **nunca** ven MC. Solo ven las recomendaciones y el estado ("programado", "realizado").

## 7b. Datos de producción (otra fuente, con aprobación)

- Los valores de fruta procesada, kg de aceite y jugo y subproductos **no vienen del laboratorio**. Suelen salir de balanzas, del sistema de producción o del ERP del cliente.
- **Flujo:**
  1. El ingeniero de procesos (o el usuario designado) carga el registro diario o por turno: manual, por planilla estándar o por integración futura con el ERP.
  2. El jefe o gerente lo **aprueba**.
  3. Recién entonces entra en los KPIs oficiales (recuperación en tambor, rendimientos).
- Cada registro guarda **quién lo cargó, quién lo aprobó y cuándo**. Si se corrige después de aprobado, queda la versión anterior (trazabilidad).
- El cliente decide **qué personas** tienen ese rol. El administrador no puede aprobar datos de producción en nombre del cliente.

## 8. Arquitectura propuesta

| Etapa | Tecnología | Por qué |
|---|---|---|
| **Prototipo (ahora)** | App web publicada en claude.ai (Artifact) con base de datos compartida y asistente IA | Rápido y sin costo de infraestructura. Sirve para validar formularios, cálculos y dashboards con 1 o 2 clientes piloto |
| **Producción (después de validar)** | Web app (por ejemplo Next.js/React) + base de datos en la nube (PostgreSQL, por ejemplo Supabase) + autenticación por cliente + API de IA (Claude) + conector a la API de MC | Multi-cliente real, seguridad, escalable, accesible desde el celular en la planta |

**Decisión pendiente:** si tu empresa (Brown/ASB) tiene una política de IT o una nube preferida (Microsoft Azure / 365, Google, AWS), hay que adoptarla en producción.

## 9. Ciberseguridad y soporte remoto

Objetivo: poder **ver al cliente en forma remota y resolver problemas de la app** sin poner en riesgo su red industrial ni sus datos.

### Principios

1. **Nada entra a la red del cliente desde afuera.** El gateway edge **solo inicia conexiones salientes** (HTTPS/MQTT con TLS) hacia la nube. No se abren puertos entrantes, no hay VPN permanente ni acceso remoto al PLC.
2. **Solo lectura en planta.** El gateway lee el PLC por OPC UA con un usuario de solo lectura. La app no tiene ningún camino para escribir en el PLC.
3. **Segmentación (modelo Purdue / IEC 62443).** El PLC queda en la red OT del cliente y el gateway en una DMZ industrial. Entre ellos hay firewall, y solo se habilita el puerto OPC UA del PLC hacia el gateway.
4. **Aislamiento entre clientes (multi-tenant).** Cada cliente es un "tenant" separado en la base de datos, con reglas a nivel de fila: un usuario de la empresa A nunca puede consultar datos de la empresa B.
5. **Mínimo privilegio y roles (RBAC):** laboratorio, procesos/aprobador, operador, gerencia, soporte y administrador. Cada rol ve y edita solo lo que necesita.

### Acceso de soporte remoto (para que vos puedas resolver problemas)

- El soporte se hace **dentro de la app**, no entrando a la red del cliente. Ves los datos, la configuración, el estado del gateway (conectado, último dato, errores) y los registros de errores.
- **Modo soporte con consentimiento:** para ver o editar datos de un cliente como si fueras su usuario (por ejemplo, para corregir una carga), la app pide una **autorización temporal** del administrador del cliente, o un acceso de emergencia que **queda registrado y se le notifica**.
- **Diagnóstico remoto del gateway:** estado, versión, uso de disco, cola de datos pendientes. Las actualizaciones del gateway son firmadas y las descarga el propio gateway (conexión saliente).
- **Registro de auditoría** inmutable: quién vio, cargó, aprobó o modificó qué y cuándo, incluido el personal de Brown/ASB.

### Controles técnicos mínimos

| Área | Control |
|---|---|
| Identidad | Usuario personal (nunca compartido), **doble factor (MFA)** obligatorio para administración, soporte y aprobadores. Idealmente inicio de sesión con la cuenta corporativa (SSO de Microsoft o Google) |
| Datos | Cifrado en tránsito (TLS 1.2+) y en reposo. Copias de seguridad diarias con prueba de restauración |
| Gateway | Equipo endurecido (sin servicios innecesarios), certificado propio por planta (revocable), buffer local cifrado |
| Nube | Proveedor con certificaciones (ISO 27001 / SOC 2). Región de datos acordada con el cliente |
| Aplicación | Validación de entradas, protección contra inyección, límites de carga de archivos y antivirus en los archivos subidos (importador) |
| IA | Los datos de un cliente nunca se usan en respuestas a otro. La IA consulta solo el tenant del usuario |
| Contractual | Acuerdo de confidencialidad y de tratamiento de datos con cada cliente. Política de retención y borrado |
| Operación | Monitoreo de accesos anómalos, procedimiento de respuesta a incidentes, revisión periódica de usuarios activos |

**Importante:** la parte de PLC y red industrial hay que acordarla con el área de IT/OT de cada cliente y con automatización de Brown. Muchas plantas tienen políticas propias que exigen aprobación previa.

## 10. Plan paso a paso

| Paso | Entregable | Quién aporta qué |
|---|---|---|
| **1. Definición** (este documento) | Alcance, roles, módulos, modelo de datos | Ramiro valida o corrige |
| **2. Relevamiento de datos** | Inventario de clientes, plantas y equipos, más **2–3 archivos reales** de distintos clientes | Ramiro envía muestras |
| **3. Formato estándar** | Diccionario de datos y planilla de carga estándar (por punto de muestreo) | Claude diseña, Ramiro valida |
| **3b. Diccionario de tags PLC** | Lista de variables útiles del eBOE, mBOE y JEX, con nombre estándar | Ramiro consigue la lista de tags del programa del PLC (automatización de Brown) |
| **4. Prototipo del MVP** | App con controles analíticos, producción con aprobación, cálculos, rangos, alertas y dashboard por planta | Claude construye |
| **5. Piloto** | 1–2 clientes cargan datos reales durante 2–4 semanas | Ramiro coordina |
| **6. Asistente IA + importador** | Chat sobre datos y manuales, y lectura automática de archivos | Claude construye |
| **6b. Piloto PLC** | Gateway edge en 1 planta (solo lectura) y dashboards de confiabilidad | Automatización de Brown + IT/OT del cliente |
| **7. Mantenimiento + MC** | Recomendaciones vinculadas a OT de MC, MTBF/MTTR con datos de MC | IT de Brown (acceso a la API) |
| **8. Producción y escalado** | Migración a la plataforma definitiva y alta de todos los clientes | Ramiro, IT y desarrollador |

## 11. Supuestos tomados (a confirmar)

- El MVP arranca por **controles analíticos**, porque es donde se pierde más tiempo unificando formatos.
- Se construye primero un **prototipo rápido** para validar con clientes, y después se pasa a desarrollo definitivo.
- Los clientes cargan sus datos **directamente** en la app (respuesta de Ramiro).
- Maintenance Connection es **solo interno** de Brown/ASB (respuesta de Ramiro).

## 12. Preguntas abiertas

1. ¿Cuántos clientes y plantas hay, y en qué países? (para dimensionar e idiomas)
2. ¿Qué frutas procesan? ¿Solo limón o también naranja, pomelo, etc.?
3. ¿Con qué frecuencia cargan análisis? (¿por turno, cada 8 h, diario?)
4. ¿Tu empresa permite subir datos de clientes a una nube externa? ¿Hay acuerdos de confidencialidad que considerar?
5. ¿Hay presupuesto o apoyo de IT para la etapa de producción?
6. ¿Querés que la app lleve marca (Brown/ASB o Propulsion) o que sea neutra?
7. ¿Qué PLC tienen los eBOE y las JEX instalados (marca y modelo)? ¿Existe la lista de tags o el proyecto TwinCAT, y quién lo tiene en Brown?
8. ¿Las JEX están conectadas a un PLC de línea o a la computadora de planta (el manual menciona el monitor de carga ELM)?
9. ¿De dónde sacan hoy los clientes los datos de producción (balanza, ERP, planilla)? ¿Quién los firma?
10. ¿Brown/ASB ya tiene alguna solución de acceso remoto aprobada (por ejemplo, para soporte de PLC) o una política de ciberseguridad que haya que cumplir?
