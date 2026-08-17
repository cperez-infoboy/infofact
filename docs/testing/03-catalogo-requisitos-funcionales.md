# Catálogo de Requisitos Funcionales — PlaniTrack 2.0

> **Propósito:** Documento estructurado de requisitos funcionales listo para alimentar la generación de un SRS (Software Requirements Specification). Cada requisito tiene ID único, descripción, origen, prioridad y trazabilidad.
>
> **Fecha:** 2026-08-13
> **Fuentes:** `docs/01-inventario-funcional-v1.5.md` (231 funcionalidades v1.5) + `docs/02-vision-producto-2.0.md` (21 gaps aspiracionales)
>
> **Convención de IDs:**
> - `RF-<DOM>-<NNN>` = Requisito Funcional
> - Origen: `[v1.5]` funcional existente a preservar | `[GAP]` nueva del 2.0 | `[MOD]` modernización de existente
> - Prioridad: 🔴 Crítica | 🟠 Alta | 🟡 Media | 🟢 Baja

---

## 0. Principio de Abstracción Multi-Industria

> **PRINCIPIO FUNDAMENTAL DEL 2.0:** PlaniTrack 2.0 no es un sistema logístico. Es una **plataforma de gestión de operaciones en terreno** aplicable a cualquier industria. Los términos logísticos (entrega, retiro, paquete, bulto) usados en este documento son **instancias de referencia**, no conceptos hardcodeados.

### Abstracción conceptual

| Concepto logístico (v1.5) | Concepto abstracto (v2.0) | Ejemplos multi-industria |
|---|---|---|
| **Entrega** | Gestión de tipo entrega | Entrega de paquete, instalación de equipo, entrega de documentos |
| **Retiro** | Gestión de tipo retiro | Retiro de mercadería, recolección de medidor, retiro de muestra médica |
| **Logística inversa** | Gestión de retorno | Devolución de producto, retiro de equipo dañado, canje |
| **Visita** | Gestión de visita | Cobranza domiciliaria, inspección técnica, lectura de medidor |
| **Paquete / bulto** | Item a gestionar | Paquete, documento, medidor, equipo, mercadería |
| **Fiscalización** | Gestión de inspección | Inventario, auditoría, fiscalización regulatoria |
| **Transportista** | Gestor de terreno | Conductor, técnico, cobrador, inspector, instalador |
| **Ruta** | Circuito de gestiones | Ruta de entrega, circuito de lectura, recorrido de cobranza |

### Reglas del modelo configurable

1. **El tipo de gestión es definido por el modelo de operación del tenant** (RF-OPS-001), no está hardcodeado
2. **Los campos de la gestión son extensibles** según el modelo de operación (RF-OPS-002)
3. **Los campos de carga/item son una extensión logística** — un tenant de lectura de medidores puede definir campos como "número de medidor", "lectura anterior", "lectura actual" en lugar de "peso" y "volumen"
4. **Los estados y workflows son configurables por tipo de gestión** — no todos los tipos requieren firma o foto; cada modelo define sus reglas (RF-MOV-033)
5. **Toda referencia a "paquete", "bulto", "entrega" o "retiro" en este catálogo** debe interpretarse como el equivalente abstracto para el modelo de operación correspondiente

---

## 1. Autenticación y Gestión de Identidad (AUTH)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-AUTH-001 | Autenticación unificada | El sistema debe proporcionar un único mecanismo de autenticación (OAuth2/OIDC) para todos los canales: web, móvil y API, reemplazando los 5 esquemas actuales. | [MOD] IF-AUTH-01..09 | 🔴 |
| RF-AUTH-002 | Multi-factor (MFA) | El sistema debe soportar autenticación de dos factores (TOTP/SMS) para usuarios web y móviles. | [GAP] [DESEO] | 🟡 |
| RF-AUTH-003 | Single Sign-On (SSO) | El sistema debe soportar SSO via SAML/OIDC para clientes enterprise. | [GAP] G-13 | 🟡 |
| RF-AUTH-004 | Gestión de sesiones | El sistema debe gestionar sesiones con refresh tokens, revocación remota y expiración configurable por perfil. | [MOD] IF-AUTH-01..02 | 🔴 |
| RF-AUTH-005 | Gate de suscripción | El sistema debe verificar el estado de suscripción del tenant antes de permitir acceso operacional, redirigiendo a gestión de pagos si está moroso/bloqueado. | [v1.5] IF-AUTH-04 | 🔴 |
| RF-AUTH-006 | Autenticación de dispositivos móviles | La app móvil debe autenticar el dispositivo (ID único) + usuario, con re-validación periódica. | [v1.5] IF-AUTH-08..09 | 🔴 |
| RF-AUTH-007 | Sistema de roles y permisos (RBAC) | El sistema debe soportar roles jerárquicos con permisos granulares por módulo, configurables por tenant. | [MOD] IF-AUTH-11 | 🔴 |
| RF-AUTH-008 | Menú dinámico por perfil+modulo | El sistema debe renderizar el menú de navegación según el perfil del usuario y los módulos habilitados para su tenant. | [v1.5] IF-AUTH-12 | 🔴 |
| RF-AUTH-009 | Auditoría de acciones | El sistema debe registrar todas las acciones de usuario (quién, qué, cuándo, desde dónde) en un log inmutable. | [v1.5] IF-AUTH-13 | 🟠 |
| RF-AUTH-010 | Credencial de usuario | El sistema debe generar una tarjeta de identificación visual por usuario (HTML/PDF). | [v1.5] IF-AUTH-14 | 🟢 |
| RF-AUTH-011 | Gestión de usuarios | El sistema debe permitir CRUD completo de usuarios con asignación de rol, tenant y estado. | [v1.5] IF-AUTH-10 | 🔴 |

---

## 2. Gestión de Tenant y Multi-Tenancy (TENANT)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-TEN-001 | Arquitectura multi-tenant unificada | El sistema debe soportar múltiples tenants con aislamiento de datos (Row-Level Security o schema-per-tenant), reemplazando el modelo DB-per-tenant. | [MOD] | 🔴 |
| RF-TEN-002 | Configuración por tenant | El sistema debe permitir configurar por tenant: logo, dominio, idioma, zona horaria, parámetros operacionales, reglas de negocio. | [v1.5] IF-CLI-06 | 🔴 |
| RF-TEN-003 | Onboarding self-service | El sistema debe permitir que nuevos clientes se registren, configuren su tenant y paguen su suscripción sin intervención manual. | [GAP] G-19 | 🟠 |
| RF-TEN-004 | Generación de entornos demo | El sistema debe generar entornos demo con datos de prueba y expiración automática. | [v1.5] IF-CLI-07 | 🟠 |
| RF-TEN-005 | Solicitud de demo (landing) | El sistema debe exponer un formulario público de solicitud demo con validación anti-spam. | [v1.5] IF-CLI-08 | 🟡 |
| RF-TEN-006 | Gestión de licencias | El sistema debe controlar el número de licencias activas por tenant y tipo. | [v1.5] IF-CLI-09 | 🟠 |
| RF-TEN-007 | Parámetros del sistema | El sistema debe almacenar configuraciones key-value por tenant para parámetros operacionales. | [v1.5] IF-CLI-16 | 🟠 |
| RF-TEN-008 | Multi-país | El sistema debe soportar operación en múltiples países con sus respectivas configuraciones geográficas, fiscales y de feriados. | [GAP] G-03 | 🟡 |

---

## 3. Gestión de Módulos (MOD)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-MOD-001 | Catálogo de módulos | El sistema debe mantener un catálogo dinámico de módulos disponibles (definidos en base de datos, no hardcodeados). | [v1.5] IF-MOD-01 | 🔴 |
| RF-MOD-002 | Activar/desactivar módulos por tenant | El sistema debe permitir habilitar o deshabilitar módulos por tenant, con registro de auditoría (quién, cuándo, motivo). | [v1.5] IF-MOD-02 | 🔴 |
| RF-MOD-003 | Configuración modular tipada | El sistema debe almacenar configuración por módulo por tenant con tipado (string, número, boolean, JSON, secret). | [v1.5] IF-MOD-03 | 🔴 |
| RF-MOD-004 | Secretos encriptados | El sistema debe encriptar valores sensibles (AES-256 o superior) en reposo. | [v1.5] IF-MOD-07 | 🔴 |
| RF-MOD-005 | Templates de configuración | El sistema debe soportar definición de esquemas UI para formularios de configuración (tipo de input, validación, opciones). | [v1.5] IF-MOD-05 | 🟠 |
| RF-MOD-006 | Asociación módulo-menú | El sistema debe vincular módulos con items del menú de navegación. | [v1.5] IF-MOD-06 | 🟠 |

---

## 4. Gestión de Clientes y Maestros (CLI)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-CLI-001 | CRUD Clientes | El sistema debe permitir gestionar (crear, leer, actualizar, eliminar) clientes con identificación fiscal (RUT), razón social, dirección, contacto y zona. | [v1.5] IF-CLI-01 | 🔴 |
| RF-CLI-002 | Campos personalizados | El sistema debe soportar campos personalizados por tenant para enriquecer el modelo de cliente. | [v1.5] IF-CLI-02 | 🟠 |
| RF-CLI-003 | Libreta de direcciones | El sistema debe mantener un directorio de direcciones frecuentes por cliente. | [v1.5] IF-CLI-03 | 🟡 |
| RF-CLI-004 | Cuentas corrientes | El sistema debe gestionar cuentas corrientes de clientes con movimientos. | [v1.5] IF-CLI-04 | 🟠 |
| RF-CLI-005 | Clientes via API | El sistema debe exponer endpoints REST para crear/actualizar/consultar clientes. | [v1.5] IF-CLI-05 | 🔴 |
| RF-CLI-006 | CRUD Categorías | El sistema debe permitir gestionar categorías de servicio. | [v1.5] IF-CLI-10 | 🟡 |
| RF-CLI-007 | CRUD Proveedores | El sistema debe permitir gestionar proveedores. | [v1.5] IF-CLI-11 | 🟡 |
| RF-CLI-008 | CRUD Motivos de excepción | El sistema debe mantener un catálogo configurable de motivos de no entrega/no retiro. | [v1.5] IF-CLI-12 | 🔴 |
| RF-CLI-009 | CRUD Feriados | El sistema debe gestionar feriados para cálculo de días hábiles, con soporte multi-país. | [v1.5] IF-CLI-13 | 🟠 |
| RF-CLI-010 | Cobertura geográfica jerárquica | El sistema debe mantener jerarquía geográfica (país → región → provincia → comuna) navegable. | [v1.5] IF-CLI-14 | 🔴 |
| RF-CLI-011 | Datos extra configurables | El sistema debe permitir almacenar datos adicionales configurables por tenant. | [v1.5] IF-CLI-15 | 🟡 |
| RF-CLI-012 | Blacklist de correos | El sistema debe mantener una lista negra de destinatarios de email que se excluyen automáticamente. | [v1.5] IF-CLI-17 | 🟡 |

---

## 5. Gestión de Puntos / Gestiones (PTO)

> **Nota de abstracción:** Los tipos de gestión (entrega, retiro, LI, visita, etc.) son **instancias configurables** del modelo de operación del tenant. Ver Principio de Abstracción (§0). Un tenant de lectura de medidores usará "lectura" como tipo; uno de cobranza usará "cobranza".

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-PTO-001 | Crear gestión | El sistema debe permitir crear una gestión con cliente, dirección, ventana horaria y datos del item a gestionar (campos extensibles según modelo de operación). En logística: entrega con datos del paquete. En lectura de medidores: lectura con datos del medidor. | [v1.5] IF-PTO-01 | 🔴 |
| RF-PTO-002 | Tipos de gestión configurables | El sistema debe permitir al tenant definir tipos de gestión (ej: entrega, retiro, lectura de medidor, cobranza, instalación, inspección) con campos y workflows específicos por tipo. Los tipos no están hardcodeados. | [MOD] IF-PTO-02, RF-OPS-001 | 🔴 |
| RF-PTO-003 | Crear gestión vía API | El sistema debe exponer endpoint REST para crear gestiones con validación de duplicados y asignación automática de zona. | [v1.5] IF-PTO-03 | 🔴 |
| RF-PTO-004 | Carga masiva de gestiones | El sistema debe permitir importar gestiones masivamente desde archivo (CSV/Excel) con template configurable según tipo de gestión. | [v1.5] IF-PTO-04 | 🔴 |
| RF-PTO-005 | Cancelar gestión | El sistema debe permitir cancelar/eliminar (soft-delete) una gestión. | [v1.5] IF-PTO-05 | 🔴 |
| RF-PTO-006 | Consultar gestión | El sistema debe mostrar el detalle completo de una gestión: datos, tracking, eventos, fotos, archivos adjuntos. | [v1.5] IF-PTO-06 | 🔴 |
| RF-PTO-007 | Historial de tracking | El sistema debe mostrar el timeline completo de eventos de una gestión con evidencia fotográfica. | [v1.5] IF-PTO-07 | 🔴 |
| RF-PTO-008 | Adjuntar archivos | El sistema debe permitir adjuntar y descargar archivos asociados a una gestión. | [v1.5] IF-PTO-08 | 🟠 |
| RF-PTO-009 | Gestión sin ruta (on-demand) | El sistema debe soportar gestiones que no estén asociadas a una ruta planificada, ejecutables on-demand. | [GAP] G-09, G-10 | 🟠 |
| RF-PTO-010 | Gestión de retorno (logística inversa) | El sistema debe permitir crear gestiones de retorno/devolución con orden de flete (aplicable a logística). El tipo "retorno" es uno de los tipos configurables. | [v1.5] IF-PTO-14..15 | 🔴 |
| RF-PTO-011 | Gestión multi-tipo simultánea | El sistema debe soportar múltiples tipos de gestión simultáneamente en una misma operación. Ej: un gestor puede hacer entregas y lecturas de medidor en la misma ruta. | [v1.5] Canvas + RF-OPS-003 | 🔴 |
| RF-PTO-012 | Variables de perecibles/congelados | El sistema debe soportar campos adicionales para gestión de cadena de frío (temperatura, sensores IoT, tiempo máximo exposición) configurable por tenant. | [GAP] G-08 | 🟢 |

---

## 6. Planificación y Programación (PLAN)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-PLAN-001 | Calendario de programación | El sistema debe mostrar un calendario de días disponibles para programación de gestiones. | [v1.5] IF-PTO-09 | 🔴 |
| RF-PLAN-002 | Asignación manual de gestiones | El sistema debe permitir asignar manualmente gestiones a rutas y transportistas. | [v1.5] IF-PTO-10 | 🔴 |
| RF-PLAN-003 | Asignación masiva | El sistema debe permitir asignar masivamente gestiones a múltiples rutas/transportistas. | [v1.5] IF-PTO-11 | 🔴 |
| RF-PLAN-004 | Programador visual | El sistema debe proporcionar una interfaz visual de programación con drag-and-drop o selección masiva. | [MOD] IF-PTO-12 | 🟠 |
| RF-PLAN-005 | Vista mapa de programación | El sistema debe mostrar la programación del día en un mapa geográfico con los puntos visibles. | [v1.5] IF-PTO-13 | 🟠 |
| RF-PLAN-006 | Rutas recurrentes | El sistema debe soportar la programación de rutas recurrentes (diaria, semanal, mensual). | [v1.5] IF-RUT-03 | 🟠 |
| RF-PLAN-007 | Programador de logística inversa | El sistema debe proporcionar un programador específico para gestiones de LI. | [v1.5] IF-PTO-16 | 🟠 |
| RF-PLAN-008 | Detección de feriados | El sistema debe excluir automáticamente días feriados de la programación según el país del tenant. | [v1.5] IF-CLI-13 | 🔴 |
| RF-PLAN-009 | Semáforo SLA | El programador diario debe mostrar un semáforo visual (verde/amarillo/rojo) basado en tolerancia/SLA por gestión. | [v1.5] Inducción | 🔴 |
| RF-PLAN-010 | Restricción post-sincronización | Si el transportista ya sincronizó, el sistema debe restringir la reprogramación libre, permitiendo solo pasar puntos a otro transportista. | [v1.5] Inducción | 🔴 |
| RF-PLAN-011 | Reset de medianoche | A las 00:00 la app móvil debe limpiar la carga del día y cargar el patrón del día siguiente (los adicionales no se repiten automáticamente). | [v1.5] Inducción | 🟠 |
| RF-PLAN-012 | Intervalo mínimo de solicitud | El sistema debe validar un intervalo mínimo (~20 min) entre hora de inicio y fin de una solicitud. | [v1.5] Inducción | 🟠 |
| RF-PLAN-013 | PWA con capacidades offline | La plataforma web debe proporcionar capacidades offline limitadas (PWA) para operación básica sin conectividad. | [GAP] G-01 | 🟡 |

---

## 7. Optimización de Rutas (VRP)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-VRP-001 | Optimización multi-vehículo | El sistema debe optimizar rutas considerando múltiples vehículos con capacidades distintas (peso, volumen, dimensiones). | [v1.5] IF-RUT-07 | 🔴 |
| RF-VRP-002 | Ventanas de tiempo | El sistema debe respetar ventanas de tiempo por gestión y por turno de vehículo. | [v1.5] IF-RUT-12 | 🔴 |
| RF-VRP-003 | Prioridades de gestión | El sistema debe soportar prioridades (escala configurable) que influyan en el orden de visita. | [v1.5] IF-RUT-12 | 🟠 |
| RF-VRP-004 | Restricciones de capacidad | El sistema debe modelar capacidad por peso (kg) y volumen (cm³) con merma configurable. | [v1.5] IF-RUT-12 | 🔴 |
| RF-VRP-005 | Optimización asíncrona | El sistema debe procesar optimizaciones de forma asíncrona con polling de estado. | [v1.5] IF-RUT-08 | 🔴 |
| RF-VRP-006 | Puntos no asignables | El sistema debe reportar gestiones que el optimizador no pudo asignar, con razón. | [v1.5] IF-RUT-13 | 🟠 |
| RF-VRP-007 | Cuota de optimización por cliente | El sistema debe limitar el número de optimizaciones por cliente por mes. | [v1.5] IF-RUT-14 | 🟠 |
| RF-VRP-008 | Estadísticas de optimización | El sistema debe mostrar métricas por tour y por solución (tiempos, distancias, utilización). | [v1.5] IF-RUT-15 | 🟡 |
| RF-VRP-009 | Ruteo básico sin optimizador | El sistema debe proporcionar ruteo básico (secuencia por distancia) sin motor externo. | [v1.5] IF-RUT-06 | 🟠 |
| RF-VRP-010 | Tiempo de servicio por punto | El sistema debe permitir configurar duración de servicio (tiempo en el punto) por gestión. | [v1.5] IF-RUT-12 | 🟠 |
| RF-VRP-011 | Aviso de costo de optimización | El sistema debe advertir al usuario del costo monetario de cada optimización antes de ejecutarla. | [v1.5] Videos inducción | 🟠 |
| RF-VRP-012 | Sobrevivencia de sesión | La optimización debe continuar procesándose aunque el usuario cierre sesión. | [v1.5] Videos inducción | 🔴 |
| RF-VRP-013 | Límite configurable por vehículo | El sistema debe permitir configurar el límite máximo de puntos por vehículo en la optimización (default: 10). | [v1.5] Videos inducción | 🟠 |

---

## 8. Ejecución Operacional (EXEC)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-EXEC-001 | Consola de operación | El sistema debe proporcionar una consola de operación en tiempo real para monitorear y gestionar la ejecución de rutas. | [v1.5] IF-RUT-16 | 🔴 |
| RF-EXEC-002 | Cumplimiento de ruta | El sistema debe calcular y mostrar el porcentaje de cumplimiento de cada ruta en tiempo real. | [v1.5] IF-RUT-17 | 🔴 |
| RF-EXEC-003 | Eficiencia operacional | El sistema debe calcular KPIs de eficiencia por transportista y ruta (tiempo, distancia, gestiones completadas). | [v1.5] IF-RUT-18 | 🟠 |
| RF-EXEC-004 | Estados de gestión | El sistema debe manejar el ciclo de vida: Pendiente → Asignado → En Tránsito → Arribado → En Progreso → Completado, más estado de Excepción. | [v1.5] IF-TRK-06 | 🔴 |
| RF-EXEC-005 | Gestión de excepciones | El sistema debe permitir registrar excepciones con motivo (catálogo configurable) durante la ejecución. | [v1.5] IF-CLI-12 | 🔴 |
| RF-EXEC-006 | Reasignación en caliente | El sistema debe permitir reasignar gestiones a otro transportista durante la operación. | [MOD] | 🟠 |
| RF-EXEC-007 | Lock screen operativo | El sistema debe proporcionar un modo bloqueo de pantalla para operación dedicada en campo. | [v1.5] IF-RUT-04 | 🟡 |
| RF-EXEC-008 | Sorting de paquetería | El sistema debe soportar clasificación y sorting de paquetes para optimizar carga/descarga. | [GAP] G-11 | 🟢 |

---

## 9. Aplicación Móvil (MOV)

### 9.1 Capacidades Core

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-MOV-001 | Multiplataforma (Android + iOS) | La app móvil debe estar disponible para Android e iOS (nativo o cross-platform). | [GAP] G-02 | 🔴 |
| RF-MOV-002 | Offline-first | La app debe funcionar sin conectividad, almacenando datos localmente y sincronizando cuando haya conexión. | [v1.5] IF-MOV-23 | 🔴 |
| RF-MOV-003 | Sincronización diferida | La app debe sincronizar datos diferidamente (14+ tipos de entidad) con reintento automático. | [v1.5] IF-MOV-24 | 🔴 |
| RF-MOV-004 | Auto-actualización | La app debe verificar y descargar actualizaciones automáticamente. | [v1.5] IF-MOV-03 | 🟠 |
| RF-MOV-005 | White-label | La app debe soportar branding y configuración por cliente (logo, colores, features activas). | [v1.5] IF-MOV-04 | 🔴 |

### 9.2 Funciones Operacionales

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-MOV-006 | Confirmación de entrega | La app debe permitir confirmar entrega con firma digital, fotos, y escaneo de QR/barras. | [v1.5] IF-MOV-05 | 🔴 |
| RF-MOV-007 | Entrega parcial | La app debe permitir registrar entregas parciales con diferencias. | [v1.5] IF-MOV-06 | 🔴 |
| RF-MOV-008 | Devolución | La app debe permitir registrar devoluciones de mercadería. | [v1.5] IF-MOV-07 | 🔴 |
| RF-MOV-009 | Firma digital | La app debe capturar firmas en pantalla y almacenarlas como imagen. | [v1.5] IF-MOV-08 | 🔴 |
| RF-MOV-010 | Foto con geotag (EXIF) | La app debe capturar fotos con coordenadas GPS embebidas en EXIF para certificación de posición. | [v1.5] IF-MOV-09 | 🔴 |
| RF-MOV-011 | Escaneo QR/código de barras | La app debe escanear códigos via cámara (software) y hardware dedicado. | [v1.5] IF-MOV-10 | 🔴 |
| RF-MOV-012 | Registro de retiros (PU) | La app debe permitir emitir orden de flete, nominar, confirmar POD y anular retiros. | [v1.5] IF-MOV-12..16 | 🔴 |
| RF-MOV-013 | Excepción de retiro | La app debe permitir registrar motivo de no retiro. | [v1.5] IF-MOV-16 | 🔴 |
| RF-MOV-014 | Ruta del día | La app debe mostrar la ruta asignada del día con capacidad de reordenar puntos. | [v1.5] IF-MOV-33 | 🔴 |
| RF-MOV-015 | Consulta de orden | La app debe permitir consultar el detalle de una orden de servicio. | [v1.5] IF-MOV-19 | 🟠 |
| RF-MOV-016 | Guía de traslado | La app debe gestionar transferencias entre puntos. | [v1.5] IF-MOV-20 | 🟡 |
| RF-MOV-017 | Fiscalización/inventario | La app debe soportar modo de inspección/fiscalización con captura de datos. | [v1.5] IF-MOV-30 | 🟡 |

### 9.3 Pagos y DTE

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-MOV-018 | Generación DTE | La app debe generar documentos tributarios electrónicos. | [v1.5] IF-MOV-18 | 🟠 |
| RF-MOV-019 | POS físico | La app debe soportar pagos con terminal POS físico. | [v1.5] IF-MOV-21 | 🟡 |
| RF-MOV-020 | Pago en terreno | La app debe procesar cobros por retiros/compras en campo. | [GAP] G-06 | 🟡 |

### 9.4 Tracking y GPS

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-MOV-021 | GPS tracking activo | La app debe enviar ubicación en tiempo real durante una ruta activa. | [v1.5] IF-MOV-25 | 🔴 |
| RF-MOV-022 | GPS tracking pasivo | La app debe enviar ubicación periódica cuando no haya ruta activa. | [v1.5] IF-MOV-26 | 🟠 |
| RF-MOV-023 | Antispoofing GPS | La app debe detectar y reportar ubicaciones imposibles (GPS falso). | [v1.5] IF-MOV-27 | 🟠 |
| RF-MOV-024 | Notificaciones push | La app debe recibir notificaciones push del backend. | [GAP] | 🟠 |
| RF-MOV-025 | Mensajería interna | La app debe permitir enviar y recibir mensajes entre el transportista y el backend (mensajería operacional). | [v1.5] Diagrama modular | 🟠 |
| RF-MOV-026 | Validación de dispositivo de pago | La app debe validar el dispositivo POS antes de habilitar operaciones de pago en terreno. | [v1.5] Diagrama modular | 🟡 |
| RF-MOV-027 | Sincronización de parámetros offline | La app debe descargar parámetros operacionales (formas de pago, motivos, usuarios, nóminas) para operar offline. | [v1.5] Diagrama modular | 🔴 |
| RF-MOV-028 | GPS diferenciado por cliente | La app debe soportar diferentes modos de tracking GPS según configuración del cliente (background continuo vs actualización via Firebase). | [MOD] Diagrama modular | 🟠 |
| RF-MOV-029 | Consulta de nóminas | La app debe permitir consultar las nóminas asignadas al transportista. | [v1.5] Diagrama modular | 🟠 |

### 9.5 Navegación Externa

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-MOV-030 | Deep links de navegación | La app debe permitir abrir la ruta del punto en Waze y Google Maps con un botón desde el detalle del punto. | [v1.5] Manuales | 🟠 |
| RF-MOV-031 | Geocerca configurable | La app debe validar que el gestor esté dentro de un radio configurable (100-500m) del punto para gestionar. Debe usar la precisión GPS del teléfono para mitigar falsos negativos. | [MOD] Inducción | 🔴 |
| RF-MOV-032 | Marca de agua en fotos | Las fotos de POD deben incluir membrete con empresa + fecha/hora al ser capturadas. | [v1.5] Videos inducción | 🟠 |
| RF-MOV-033 | Reglas de negocio configurables por cliente | Cada cliente debe poder definir qué datos exige la app al transportista: firma, fotos, campos visibles/requeridos. | [v1.5] Inducción | 🔴 |
| RF-MOV-034 | Libreta de direcciones de terceros | El sistema debe soportar direcciones de terceros (clientes del cliente) además de las propias. | [v1.5] Inducción | 🟠 |
| RF-MOV-035 | Indicador de precisión GPS | La app debe mostrar visualmente cuando la posición GPS no es válida (precisión > umbral). | [v1.5] Videos inducción | 🟠 |

---

## 10. Tracking y Monitoreo (TRK)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-TRK-001 | Tracking público web | El sistema debe exponer un portal de seguimiento público (sin autenticación) via URL firmada. | [v1.5] IF-TRK-01 | 🔴 |
| RF-TRK-002 | Tracking extendido | El portal de tracking debe mostrar información adicional configurable por tenant. | [v1.5] IF-TRK-02 | 🟠 |
| RF-TRK-003 | Timeline de eventos | El sistema debe mantener un timeline completo de eventos por gestión con evidencia. | [v1.5] IF-TRK-04 | 🔴 |
| RF-TRK-004 | Mapa en vivo | El sistema debe mostrar transportistas en un mapa en tiempo real. | [v1.5] IF-TRK-07 | 🔴 |
| RF-TRK-005 | Tracking tipo Waze | El sistema debe permitir al cliente final ver el progreso del transportista en mapa, con ETA. | [GAP] G-04 | 🟠 |
| RF-TRK-006 | Centro de ayuda | El sistema debe exponer un help center público. | [v1.5] IF-TRK-03 | 🟡 |
| RF-TRK-007 | Solicitud de ubicación | El sistema debe permitir solicitar la ubicación de un gestor en campo on-demand. | [GAP] G-07 | 🟡 |

---

## 11. Notificaciones (NOT)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-NOT-001 | Email transaccional | El sistema debe enviar emails transaccionales (confirmaciones de entrega/retiro, notificaciones, passwords) con templates por dominio. | [v1.5] IF-NOT-01..08 | 🔴 |
| RF-NOT-002 | Failover SMTP | El sistema debe rotar automáticamente entre múltiples proveedores SMTP ante fallo. | [v1.5] IF-NOT-09 | 🟠 |
| RF-NOT-003 | Envío masivo | El sistema debe soportar dispatch masivo de emails (array de destinatarios). | [v1.5] IF-NOT-10 | 🟠 |
| RF-NOT-004 | WhatsApp | El sistema debe enviar mensajes de WhatsApp a destinatarios. | [v1.5] IF-NOT-14 | 🟠 |
| RF-NOT-005 | SMS | El sistema debe enviar SMS. | [v1.5] IF-NOT-16 | 🟡 |
| RF-NOT-006 | Notificaciones push móviles | El sistema debe enviar push notifications a dispositivos móviles. | [GAP] | 🟠 |
| RF-NOT-007 | Templates configurables | El sistema debe permitir configurar y editar templates de notificación por tenant. | [MOD] | 🟠 |
| RF-NOT-008 | Auditoría de notificaciones | El sistema debe registrar cada notificación enviada (destinatario, contenido, estado). | [v1.5] IF-NOT-12 | 🟠 |
| RF-NOT-009 | Blacklist de destinatarios | El sistema debe excluir automáticamente destinatarios en lista negra. | [v1.5] IF-NOT-11 | 🟡 |
| RF-NOT-010 | Asistente IA para soporte | El sistema debe proporcionar un chatbot o asistente IA para el help center. | [GAP] G-18 | 🟡 |

---

## 12. Integraciones (INT)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-INT-001 | API REST de integración | El sistema debe exponer una API REST documentada (OpenAPI) para integradores externos. | [v1.5] IF-API-01..11 | 🔴 |
| RF-INT-002 | Webhooks configurables | El sistema debe enviar webhooks a URLs configurables por tenant ante eventos operacionales. | [v1.5] IF-INT-23 | 🔴 |
| RF-INT-003 | Framework de integraciones | El sistema debe proporcionar un framework extensible (plug-in) para agregar nuevas integraciones carrier/sistema. | [GAP] G-16 | 🔴 |
| RF-INT-004 | Integración carrier SOAP | El sistema debe mantener integración con carriers via SOAP (emisión + DLS). | [v1.5] IF-INT-01..10 | 🟠 |
| RF-INT-005 | Integración BeeTrack/Walmart | El sistema debe procesar webhooks de BeeTrack y fetch de rutas. | [v1.5] IF-INT-11..15 | 🟠 |
| RF-INT-006 | Geocoding / normalización de direcciones | El sistema debe geocodificar direcciones via servicio externo (OPV u otro). | [v1.5] IF-INT-20 | 🔴 |
| RF-INT-007 | Conector ERP/SAP | El sistema debe proporcionar conectores para integración con ERP (SAP y otros). | [GAP] G-13, G-14 | 🟡 |
| RF-INT-008 | Plugin e-commerce | El sistema debe proporcionar conector para plataformas de e-commerce. | [GAP] G-15 | 🟡 |
| RF-INT-009 | Integración MercadoPago | El sistema debe mantener integración OAuth con MercadoPago para POS. | [v1.5] IF-INT-17..19 | 🟡 |
| RF-INT-010 | Middleware de eventos asíncrono | El sistema debe procesar eventos de forma asíncrona via colas (no cron). | [MOD] IF-EVT-01..07 | 🔴 |

---

## 13. Reportes y Analítica (REP)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-REP-001 | Constructor de reportes | El sistema debe proporcionar un constructor de reportes con filtros configurables (fecha, cliente, ruta, transportista, estado, zona). | [v1.5] IF-REP-01 | 🔴 |
| RF-REP-002 | Reportes estándar | El sistema debe incluir reportes predefinidos: cumplimiento, eficiencia, excepciones, volumen operativo. | [v1.5] IF-REP-02..05 | 🔴 |
| RF-REP-003 | Exportación CSV/Excel | El sistema debe permitir exportar reportes en formato CSV y Excel. | [v1.5] IF-REP-06 | 🔴 |
| RF-REP-004 | KPIs y métricas | El sistema debe calcular y mostrar KPIs agregados (volumen, cumplimiento, eficiencia, tiempo promedio). | [v1.5] IF-REP-07..08 | 🟠 |
| RF-REP-005 | Reportes por zona geográfica | El sistema debe agrupar reportes por zona. | [v1.5] IF-REP-03 | 🟡 |
| RF-REP-006 | Reportes con rangos extensos | El sistema debe soportar reportes con rangos de fechas prolongados sin degradación de performance. | [GAP] G-20 | 🟡 |
| RF-REP-007 | Dashboard operacional | El sistema debe proporcionar un dashboard con métricas en tiempo real. | [MOD] | 🟠 |
| RF-REP-008 | API de reportes | El sistema debe exponer endpoints REST para consulta de reportes programáticamente. | [v1.5] IF-REP-09 | 🟠 |

---

## 14. Inteligencia Artificial (IA)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-IA-001 | Predicción de fallos de entrega | El sistema debe predecir la probabilidad de fallo de cada gestión al momento de su creación. | [v1.5] IF-IA-02 | 🟠 |
| RF-IA-002 | Panel de predicciones | El sistema debe mostrar un panel con las predicciones y factores de riesgo. | [v1.5] IF-IA-01 | 🟠 |
| RF-IA-003 | Predicción de retrasos | El sistema debe predecir retrasos en las entregas basándose en patrones históricos. | [GAP] G-17 | 🟡 |
| RF-IA-004 | ACL por perfil | El sistema debe restringir el acceso a datos predictivos a perfiles autorizados. | [v1.5] IF-IA-05 | 🟡 |

---

## 15. Billing y Suscripciones (BIL)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-BIL-001 | Facturación recurrente | El sistema debe procesar cobros mensuales automáticos via Transbank OneClick Mall en el primer día hábil del mes. | [v1.5] IF-BIL-02 | 🔴 |
| RF-BIL-002 | Precio dinámico | El sistema debe calcular el precio mensual en base a: precio base (UF) + licencias × factor + prorrata + valor UF del día. | [v1.5] IF-BIL-01 | 🔴 |
| RF-BIL-003 | Inscripción de tarjeta | El sistema debe permitir inscribir tarjetas via Transbank OneClick Mall (flujo callback). | [v1.5] IF-BIL-03 | 🔴 |
| RF-BIL-004 | Pago manual (Webpay) | El sistema debe permitir pago único via Webpay Plus para recuperación de morosidad. | [v1.5] IF-BIL-05 | 🟠 |
| RF-BIL-005 | Gestión de morosidad | El sistema debe gestionar reintentos (máximo configurable), suspendiendo tras N fallos con notificación. | [v1.5] IF-BIL-07 | 🔴 |
| RF-BIL-006 | Gestión de UF | El sistema debe obtener el valor diario de la UF de múltiples fuentes con validación de consistencia. | [v1.5] IF-BIL-08 | 🟠 |
| RF-BIL-007 | Calendario hábil | El sistema debe calcular días hábiles según feriados oficiales del país. | [v1.5] IF-BIL-09 | 🟠 |
| RF-BIL-008 | Catálogo de productos | El sistema debe mantener un catálogo de productos/planes con precios. | [v1.5] IF-BIL-13 | 🟠 |
| RF-BIL-009 | Historial de pagos | El sistema debe mantener un historial completo de transacciones. | [v1.5] IF-BIL-11 | 🟠 |
| RF-BIL-010 | Exclusión de cobro automático | El sistema debe permitir marcar clientes como facturación manual (OC). | [v1.5] IF-BIL-18 | 🟡 |

---

## 16. Gestión de Flota y Gestores de Terreno (FLO)

> **Nota de abstracción:** En v1.5, "Transportista" es el gestor de terreno (conductor de vehículo). En v2.0, el gestor puede ser: conductor, técnico, cobrador, inspector, instalador, lector de medidores, etc. — según el modelo de operación del tenant.

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-FLO-001 | CRUD Gestores de terreno | El sistema debe gestionar gestores de terreno (conductores, técnicos, inspectores, etc.) con identificador, agencia/base, estado, datos del conductor, datos del vehículo y capacidades. La denominación "Transportista" es un caso particular del modelo logístico. | [v1.5] IF-FLO-01 | 🔴 |
| RF-FLO-002 | CRUD Agencias / Bases | El sistema debe gestionar agencias/bases/sucursales del operador como puntos de origen y retorno. | [v1.5] IF-FLO-02 | 🟠 |
| RF-FLO-003 | Capacidades vehiculares | El sistema debe gestionar capacidades (peso, dimensiones, volumen) por vehículo. Relevantes cuando se usa optimizador VRP. | [v1.5] IF-FLO-03 | 🟠 |
| RF-FLO-004 | Soporte de múltiples roles de gestor | El sistema debe soportar distintos roles de gestor de terreno: conductores, peonetas/peatones, técnicos, inspectores, cobradores, etc. | [GAP] G-05 | 🟡 |
| RF-FLO-005 | Estado de optimización | El sistema debe marcar gestores como disponibles/seleccionados para optimización VRP. | [v1.5] IF-FLO-05 | 🟡 |

---

## 17. Zonas y Cobertura Geográfica (ZON)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-ZON-001 | CRUD Zonas (polígonos) | El sistema debe permitir gestionar zonas geográficas definidas como polígonos. | [v1.5] IF-ZON-01 | 🔴 |
| RF-ZON-002 | Asignación automática de zona | El sistema debe asignar zona automáticamente según la comuna/dirección de la gestión. | [v1.5] IF-ZON-02 | 🔴 |
| RF-ZON-003 | Jerarquía geográfica | El sistema debe mantener jerarquía de 3 niveles geográficos (área level 1/2/3). | [v1.5] IF-ZON-05 | 🟠 |
| RF-ZON-004 | Reportes por zona | El sistema debe agrupar métricas y reportes por zona. | [v1.5] IF-ZON-03 | 🟡 |

---

## 18. Impresión (PRT)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-PRT-001 | Generación PDF | El sistema debe generar documentos PDF (credenciales, reportes, etiquetas). | [v1.5] IF-PRT-01 | 🟠 |
| RF-PRT-002 | Etiquetas ZPL (Zebra) | El sistema debe generar comandos ZPL para impresoras térmicas Zebra. | [v1.5] IF-PRT-02 | 🟡 |
| RF-PRT-003 | Impresión móvil | La app móvil debe imprimir etiquetas en terminales POS compatibles. | [v1.5] IF-MOV-31 | 🟡 |

---

## 19. Gestión de Retorno / Logística Inversa (LI)

> **Nota de abstracción:** La logística inversa es un **tipo de gestión de retorno** específico del modelo logístico. En otros modelos de operación, el "retorno" puede ser: retiro de equipo dañado, devolución de material, canje de producto, etc.

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-LI-001 | Gestión de retorno | El sistema debe permitir crear gestiones de retorno/devolución con los campos definidos por el modelo de operación (ej: orden de flete en logística, motivo de retiro en servicio técnico). | [v1.5] IF-PTO-14..15 | 🔴 |
| RF-LI-002 | Programación de retornos | El sistema debe proporcionar un programador dedicado para gestiones de retorno. | [v1.5] IF-PTO-16 | 🟠 |
| RF-LI-003 | Gestión de viajes de retorno | El sistema debe gestionar viajes de retorno entre puntos. | [v1.5] IF-PTO-18 | 🟠 |
| RF-LI-004 | Ingesta de eventos de retorno | El sistema debe recibir eventos de retorno desde el campo. | [v1.5] IF-PTO-19 | 🟠 |

---

## 20. Salud del Sistema (HEALTH)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-HEA-001 | Health check | El sistema debe exponer un endpoint de health check con métricas de infraestructura (DB, colas, servicios). | [v1.5] IF-TRK-08 | 🟠 |
| RF-HEA-002 | Observabilidad (traces) | El sistema debe implementar distributed tracing (OpenTelemetry o similar). | [MOD] | 🟠 |
| RF-HEA-003 | Métricas de uso | El sistema debe recolectar estadísticas de uso por opción/feature para guiar decisiones de producto. | [v1.5] Canvas calidad | 🟡 |
| RF-HEA-004 | SLA 99,95% | El sistema debe diseñarse para garantizar 99,95% de disponibilidad. | [v1.5] Canvas calidad | 🟠 |

---

## 21. Modelo de Operación y Gestión Multi-Tipo (OPS)

> **Origen:** Deseables del equipo — nuevo paradigma para 2.0: PlaniTrack deja de ser solo logística para soportar múltiples modelos operacionales configurables.

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-OPS-001 | Modelo de operación configurable | El sistema debe permitir definir el modelo de operación del tenant: Logística, Distribución, Servicio Técnico, Cobranza, Lectura de Medidores, Instalaciones, u otros. | [DESEO] | 🔴 |
| RF-OPS-002 | Modelo de datos flexible | El modelo de datos debe ser extensible para incorporar nuevos tipos de operación sin cambios de esquema disruptivos (campos dinámicos, entidades configurables). | [DESEO] | 🔴 |
| RF-OPS-003 | Gestión multi-tipo ampliada | El sistema debe soportar gestiones de: retiro, entrega, logística inversa, servicio técnico, instalaciones, cobranza, lectura de medidores y tipos personalizados por tenant. | [DESEO] | 🔴 |
| RF-OPS-004 | MotoEncargo (on-demand) | El sistema debe incluir un módulo de MotoEncargo para gestiones no programadas de ejecución inmediata (ej: Motoboy, mensajería exprés). Incluye asignación instantánea sin ruta previa. | [DESEO] | 🟠 |
| RF-OPS-005 | Sucursales, Agencias y PUDO | El sistema debe soportar múltiples tipos de puntos de servicio: sucursales, agencias, PUDO (Pickup/DropOff), bodegas, locales, centros de distribución. | [DESEO] | 🟠 |
| RF-OPS-006 | Pre-venta / Auto-venta | El sistema debe soportar flujos de preventa (tomador de pedidos en terreno) y autoventa (vendedor transportando mercadería para venta directa). | [DESEO] | 🟠 |

---

## 22. Módulo Tarifario (TAR)

> **Origen:** Deseable — nuevo para 2.0.

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-TAR-001 | Cálculo tarifario | El sistema debe calcular tarifas de gestión considerando: ventanas de atención, acuerdos con clientes, prioridad de la gestión, peso, medidas, volumen, distancia y zonas de riesgo. | [DESEO] | 🟠 |
| RF-TAR-002 | Acuerdos por cliente | El sistema debe mantener tarifarios configurables por cliente con reglas y excepciones. | [DESEO] | 🟠 |
| RF-TAR-003 | Zonas de riesgo tarifarias | El sistema debe soportar clasificación tarifaria de zonas por nivel de riesgo. | [DESEO] | 🟡 |

---

## 23. Expansión de Integraciones (INT-EXP)

> **Origen:** Deseables — amplía las integraciones existentes.

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-INTX-001 | SAP vía Service Layer | El sistema debe integrarse con SAP mediante Service Layers y posteriormente webhooks para sincronización bidireccional. | [DESEO] | 🟡 |
| RF-INTX-002 | Múltiples motores de ruteo | El sistema debe soportar múltiples motores de ruteo externos (HERE, OPV, otros) de forma intercambiable. | [DESEO] | 🟠 |
| RF-INTX-003 | API abierta a terceros | El sistema debe exponer una API REST documentada (OpenAPI/Swagger) para integración de terceros con gestión de credenciales y rate limiting. | [DESEO] | 🟠 |
| RF-INTX-004 | TCC — requerimientos transversales | El sistema debe considerar los requerimientos funcionales y técnicos de TCC que puedan aplicarse transversalmente a otros clientes. | [DESEO] | 🟡 |

---

## 24. Comercial y Contratación (COM)

> **Origen:** Deseables — expande el módulo de billing.

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-COM-001 | Contratación con enrolamiento de tarjeta | El sistema debe permitir contratación del servicio con enrolamiento inmediato de tarjeta de crédito (oneclick). | [DESEO] | 🟠 |
| RF-COM-002 | Notificación de documentos pendientes de pago | El sistema debe notificar automáticamente en pantalla y por email al cliente cuando existan documentos pendientes de pago (cobro a mes vencido). | [DESEO] | 🟠 |
| RF-COM-003 | Bloqueo automático (3 strikes) | El sistema debe bloquear automáticamente el servicio tras 3 intentos fallidos de pago, con notificación al cliente. | [DESEO] | 🔴 |

---

## 25. Movil Avanzado (MOV-ADV)

> **Origen:** Deseables — nuevas capacidades móviles para 2.0.

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| RF-MVA-001 | Autenticación biométrica | La app debe soportar autenticación con huella dactilar (TouchID/FaceID/Fingerprint). | [DESEO] | 🟠 |
| RF-MVA-002 | Descarga desde stores | La app debe estar disponible en Google Play y Apple App Store (no sideload). | [DESEO] | 🔴 |
| RF-MVA-003 | Escaneo 2D y 3D | La app debe usar la cámara para escanear documentos 2D (códigos de barras, QR) y 3D (reconocimiento de objetos/volumen). | [DESEO] | 🟡 |
| RF-MVA-004 | IA local para validación de fotos | La app debe soportar modelos de IA locales (on-device) para validar que las fotos cumplan con los requerimientos del cliente (ej: producto correcto, paquete visible). | [DESEO] | 🟡 |
| RF-MVA-005 | Múltiples pasarelas de pago | La app debe funcionar sobre múltiples POS: MercadoPago, GetNet, TransBank, y pasarelas internacionales. | [DESEO] | 🟠 |
| RF-MVA-006 | Llamadas telefónicas integradas | La app debe permitir realizar llamadas telefónicas al contacto del punto directamente desde la gestión. | [DESEO] | 🟡 |
| RF-MVA-007 | Entrega contactless | La app debe soportar un modo de entrega sin contacto (sin firma presencial del receptor). | [DESEO] | 🟠 |
| RF-MVA-008 | Botón de pánico | La app debe incluir un botón de pánico que notifique al supervisor web con posicionamiento GPS en tiempo real. | [DESEO] | 🟠 |
| RF-MVA-009 | PBX con auditoría IA | La app debe soportar llamadas vía PBX hacia clientes con respaldo de grabación y auditoría por IA, a solicitud del tenant. | [DESEO] | 🟡 |
| RF-MVA-010 | Upload de fotos optimizado | La app debe enviar fotos en segundo plano con reintentos automáticos y compresión inteligente para minimizar tiempo de llegada al servidor. | [DESEO] | 🟠 |
| RF-MVA-011 | App multihilo | La app debe ser multihilo para permitir operación concurrente (ej: gestionar mientras sube fotos mientras trackea GPS). | [DESEO] | 🟠 |
| RF-MVA-012 | Pre-venta / Auto-venta móvil | La app debe soportar flujos de toma de pedidos y venta directa desde el terreno (inventario en camioneta). | [DESEO] | 🟠 |
| RF-MVA-013 | Recuperación de contraseña móvil | La app debe permitir al usuario recuperar su contraseña desde el dispositivo móvil (flujo self-service via email/token). | [DESEO] | 🔴 |
| RF-MVA-014 | Cambio de contraseña móvil | La app debe permitir al usuario cambiar su contraseña desde el dispositivo móvil con validación de contraseña actual. | [DESEO] | 🟠 |

---

## 26. Requisitos No Funcionales (NFR)

> **Origen:** Doc 02 sección 3 (Modernización Técnica) — requisitos de arquitectura, seguridad, infraestructura y calidad que condicionan la viabilidad de todos los RF anteriores.

### 26.1 Arquitectura (NFR-ARCH)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| NFR-ARCH-001 | Framework moderno | La app principal debe migrarse de CodeIgniter 3 (EOL) a un framework moderno con soporte activo (Laravel/NestJS). | [MOD] Doc 02 §3.1 | 🔴 |
| NFR-ARCH-002 | Frontend SPA | El frontend debe migrarse de AdminLTE 2 + jQuery + PHP templates a una SPA con design system (React/Vue) para mejorar UX y mantenibilidad. | [MOD] Doc 02 §3.1 | 🔴 |
| NFR-ARCH-003 | Multi-tenant unificado | La arquitectura multi-tenant debe reemplazar el modelo DB-per-tenant por uno unificado (Row-Level Security o schema-per-tenant) para escalabilidad. | [MOD] Doc 02 §3.1 | 🔴 |
| NFR-ARCH-004 | Queue real | El sistema de colas debe reemplazar cron+flock por un sistema de colas robusto (Redis/SQS) con retry policy y observabilidad. | [MOD] Doc 02 §3.1 | 🔴 |
| NFR-ARCH-005 | API Gateway | El sistema debe implementar un API Gateway unificado que provea routing, rate limiting, autenticación centralizada y observabilidad de todos los microservicios. | [MOD] Doc 02 §3.1 | 🟠 |
| NFR-ARCH-006 | Eliminar hardcoded cliente logic | El código debe eliminar las ramificaciones hardcodeadas (`if clienteinfositioid == 1`) reemplazándolas por patrones de estrategia/plugin basados en configuración. | [MOD] Doc 02 §3.1 | 🔴 |

### 26.2 Seguridad (NFR-SEC)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| NFR-SEC-001 | Auth unificada OAuth2/OIDC | Todos los canales (web, móvil, API) deben usar un único mecanismo de autenticación OAuth2/OIDC. | [MOD] Doc 02 §3.2 | 🔴 |
| NFR-SEC-002 | CSRF habilitado | El sistema debe tener protección CSRF habilitada en todos los formularios web. | [MOD] Doc 02 §3.2 | 🔴 |
| NFR-SEC-003 | CSP + sanitización XSS | El sistema debe implementar Content Security Policy y sanitización de inputs para prevenir XSS. | [MOD] Doc 02 §3.2 | 🔴 |
| NFR-SEC-004 | Gestión de secretos | Los secretos (API keys, JWT secrets, passwords de BD) deben almacenarse en un Vault/Key Management, nunca en código fuente. | [MOD] Doc 02 §3.2 | 🔴 |
| NFR-SEC-005 | Passwords sin legacy MD5 | El sistema debe usar exclusivamente bcrypt/argon2, eliminando cualquier fallback a MD5. | [MOD] Doc 02 §3.2 | 🔴 |
| NFR-SEC-006 | CORS restrictivo | El sistema debe configurar CORS con allowlist explícito por origen, eliminando el wildcard `*`. | [MOD] Doc 02 §3.2 | 🔴 |

### 26.3 Infraestructura (NFR-INFRA)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| NFR-INFRA-001 | Contenerización con Docker Swarm | El sistema debe migrar de Azure Web Apps (PaaS) a Docker Swarm para escalabilidad y portabilidad, con opción de evolucionar hacia Azure App Services o equivalente. | [MOD] Doc 02 §3.4 | 🟡 |
| NFR-INFRA-002 | PostgreSQL unificado | La base de datos debe consolidarse de multi-DB manual a PostgreSQL unificado con RLS o connection pooling. | [MOD] Doc 02 §3.4 | 🔴 |
| NFR-INFRA-003 | Redis unificado | El cache debe unificarse en Redis, eliminando Memcached de la app principal. | [MOD] Doc 02 §3.4 | 🟠 |
| NFR-INFRA-004 | Object storage para archivos | Las fotos y archivos deben migrarse de Google Drive a object storage (S3/Azure Blob) con políticas de retención y acceso controlado. | [MOD] Doc 02 §3.4 | 🔴 |

### 26.4 Calidad y Observabilidad (NFR-QUAL)

| ID | Requisito | Descripción | Origen | Prioridad |
|---|---|---|---|---|
| NFR-QUAL-001 | Cobertura de tests ≥70% | El sistema debe alcanzar cobertura de tests automatizados ≥70% (unit + integration + e2e). | [MOD] Doc 02 §3.3 | 🔴 |
| NFR-QUAL-002 | Structured logging centralizado | El sistema debe implementar structured logging centralizado (ELK/Datadog o similar), eliminando archivos sueltos. | [MOD] Doc 02 §3.3 | 🟠 |
| NFR-QUAL-003 | CI/CD con gates de calidad | El pipeline CI/CD debe incluir gates de calidad (tests, security scanning, linting) antes de cada deploy. | [MOD] Doc 02 §3.3 | 🔴 |
| NFR-QUAL-004 | Backups con retención extendida | El sistema debe mantener backups automáticos con retención ≥30 días y backups diferenciales intradía. | [MOD] Doc 02 §3.3 | 🟠 |

---

## 27. Límites de Scope — Lo que PlaniTrack NO ES

> **Origen:** Doc 02 sección 2.2 (Quadrant NO ES). Estos límites definen explícitamente lo que está fuera del alcance del producto y no debe ser abordado en el 2.0.

| # | Límite | Justificación |
|---|---|---|
| L-01 | **No es TMS** (Transportation Management System) | No gestiona flotas de camiones de larga distancia ni bodegas de tránsito |
| L-02 | **No es ERP** | No gestiona contabilidad, inventarios, RRHH ni procesos empresariales integrales |
| L-03 | **No es CRM** | No gestiona embudos de ventas, campañas de marketing ni pipeline comercial |
| L-04 | **No es un optimizador de rutas puro** | El optimizador es un módulo adicional, no el core. El core es la gestión operacional completa |
| L-05 | **No es un desarrollo a medida** | Las customizaciones por cliente no deben atentar contra la generalidad del producto |
| L-06 | **No hace fulfillment** | No gestiona picking/packing/envío desde bodega al consumidor final |
| L-07 | **No gestiona inventarios ni bodegas** | No hay control de stock ni gestión de almacenes |
| L-08 | **No es un sistema monopropósito** | Debe soportar múltiples tipos de operación (logística, distribución, servicio técnico, etc.) |

---

## Resumen Estadístico

| Dominio | Requisitos | Críticos (🔴) | Altos (🟠) | Medios (🟡) | Nuevos (GAP) |
|---|---|---|---|---|---|
| AUTH | 11 | 6 | 1 | 3 | 2 |
| TENANT | 8 | 3 | 3 | 2 | 2 |
| MOD | 6 | 4 | 2 | 0 | 0 |
| CLI | 12 | 4 | 4 | 4 | 0 |
| PTO | 12 | 8 | 2 | 1 | 2 |
| PLAN | 13 | 6 | 4 | 3 | 1 |
| VRP | 13 | 5 | 5 | 3 | 0 |
| EXEC | 8 | 3 | 3 | 2 | 1 |
| MOV | 35 | 14 | 12 | 5 | 3 |
| TRK | 7 | 3 | 2 | 2 | 2 |
| NOT | 10 | 1 | 5 | 4 | 2 |
| INT | 10 | 4 | 3 | 3 | 3 |
| REP | 8 | 2 | 3 | 3 | 1 |
| IA | 4 | 0 | 2 | 2 | 1 |
| BIL | 10 | 3 | 4 | 3 | 0 |
| FLO | 5 | 1 | 1 | 3 | 1 |
| ZON | 4 | 2 | 1 | 1 | 0 |
| PRT | 3 | 0 | 1 | 2 | 0 |
| LI | 4 | 1 | 3 | 0 | 0 |
| HEALTH | 4 | 0 | 2 | 2 | 0 |
| OPS | 6 | 3 | 3 | 0 | 0 |
| TAR | 3 | 0 | 2 | 1 | 0 |
| INTX | 4 | 0 | 2 | 2 | 0 |
| COM | 3 | 1 | 2 | 0 | 0 |
| MOV-ADV | 14 | 2 | 8 | 4 | 0 |
| **TOTAL RF** | **222** | **75** | **84** | **58** | **21** |
| NFR-ARCH | 6 | 5 | 1 | 0 | — |
| NFR-SEC | 6 | 6 | 0 | 0 | — |
| NFR-INFRA | 4 | 2 | 1 | 1 | — |
| NFR-QUAL | 4 | 2 | 2 | 0 | — |
| **TOTAL NFR** | **20** | **15** | **4** | **1** | — |
| **GRAN TOTAL** | **242** | **90** | **88** | **59** | — |

- **222 requisitos funcionales** (RF)
- **20 requisitos no funcionales** (NFR)
- **242 requisitos totales**
- **90 críticos** (MVP bloqueante)
- **88 alta prioridad** (MVP deseable)
- **59 media/baja prioridad** (Roadmap/Backlog)
- **8 límites de scope** explícitos (lo que el producto NO es)
- **21 net-nuevos** de gaps de visión (docs/02)
- **30 net-nuevos** de deseables del equipo (sección 21-25)

### Origen de los requisitos

| Origen | Cantidad | Descripción |
|---|---|---|
| `[v1.5]` | 154 | Funcionalidad existente a preservar |
| `[MOD]` | 35 | Modernización de funcionalidad o arquitectura existente (incluye NFRs) |
| `[GAP]` | 21 | Brecha de visión 2.0 (no existente en v1.5) |
| `[DESEO]` | 30 | Deseable explícito del equipo de producto |

---

*Este catálogo es el input principal para la generación del SRS. Cada RF-NNN puede trazarse al inventario funcional (docs/01) o a la visión de brechas (docs/02).*
