# Visión de Producto PlaniTrack 2.0

> **Propósito:** Consolidar la visión estratégica de PlaniTrack 2.0 a partir del trabajo colaborativo del equipo (OCR PTK 1.5) y el análisis técnico del ecosistema actual. Este documento define el delta entre lo que hay (v1.5) y lo que se quiere (v2.0), y servirá como input para el catálogo de requisitos funcionales.
>
> **Fecha:** 2026-08-13
> **Fuentes:** OCR_Planitrack_PTK1.5_Consolidado.md + análisis de código (docs/00 y docs/01)

---

## 1. Declaración de Visión (Geoffrey Moore)

> Para cualquier empresa que realice gestiones en terreno — sea logística, distribución, lectura de medidores, cobranza, servicio técnico o instalaciones — que necesita mejorar el control y la gestión de la operación, el producto **All New PlaniTrack** es un **SaaS multi-industria** que es **fiable, entregando excelentes condiciones técnicas y comerciales**. A diferencia de BeeTrack y SimpliRoute, nuestro producto es **Flexible, Adaptativo y Evolutivo**.

### Pilares estratégicos del 2.0

| Pilar | Significado | Implicancia técnica |
|---|---|---|
| **Flexible** | Acepta modificaciones por cliente sin perder generalidad | Arquitectura plug-in, configuración sobre customización |
| **Adaptativo** | Se ajusta a necesidades cambiantes sin comprometer rendimiento | Multi-tenant escalable, módulos activables |
| **Evolutivo** | Mejora constante, incorpora nuevas capacidades | CI/CD robusto, arquitectura modular, tests |
| **Multi-industria** | No es un sistema logístico — es una plataforma de gestión de terreno aplicable a cualquier industria que gestione visitas en campo | Modelo de datos configurable, tipos de gestión definidos por tenant |

---

## 2. Análisis del Quadrant ES / NO ES / HACE / NO HACE

### 2.1 Confirmaciones del 1.5 que se mantienen (ES + HACE)

El inventario funcional (docs/01) confirma que PlaniTrack 1.5 **es y hace** lo que el equipo definió:

- ✅ SaaS web + móvil para gestión de terreno
- ✅ Gestor y planificador de rutas
- ✅ Trazabilidad de flota en tiempo real
- ✅ Automatizador de tareas administrativas
- ✅ Integraciones con sistemas externos (carriers, Walmart, Casa Ideas)
- ✅ Notificaciones multicanal (email, SMS, WhatsApp)
- ✅ Reportes y analítica operacional
- ✅ Predicción ML (Infopredictor)
- ✅ Certifica lo que ocurre en la calle (POD: firma + foto + GPS)
- ✅ Sistema multipropósito (entrega, retiro, LI, fiscalización, visitas)

### 2.2 Lo que hoy NO ES y debe seguir sin serlo

El equipo fue claro en los límites del producto:

- ❌ **No es TMS** (no gestiona camiones ni bodegas)
- ❌ **No es ERP ni CRM**
- ❌ **No es un optimizador de rutas puro** (es más amplio)
- ❌ **No es un desarrollo a medida** que atente contra su generalidad
- ❌ **No hace fulfillment**
- ❌ **No gestiona inventarios ni bodegas**

### 2.3 Gap Analysis — "NO HACE" hoy pero aspira a HACER en 2.0

Estos son los items que el equipo identificó como limitaciones actuales y que representan **oportunidades de mejora para el 2.0**. Se cruzan con la realidad técnica del código:

#### 2.3.1 Expansión de Plataforma

| # | Gap (NO HACE hoy) | Aspiración 2.0 | Viabilidad técnica | Prioridad sugerida |
|---|---|---|---|---|
| G-01 | No se puede usar offline (web) | PWA con capacidades offline limitadas | Alta — la app móvil ya es offline-first | Media |
| G-02 | App móvil solo Android | App iOS nativa o cross-platform (Flutter) | Alta — ya mencionado en canvas "iOS a futuro" | **Alta** |
| G-03 | Actualmente solo Chile | Multi-país con configuración de cobertura | Media — requiere refactor de cobertura geográfica | Media |
| G-04 | No tracking tipo Waze en tiempo real | Seguimiento en vivo del transportista en mapa | Alta — Firebase RTDB ya soporta locations/ | **Alta** |

#### 2.3.2 Expansión Funcional

| # | Gap (NO HACE hoy) | Aspiración 2.0 | Viabilidad técnica | Prioridad sugerida |
|---|---|---|---|---|
| G-05 | No asignación a peonetas | Soporte de peonetas/peatones como tipo de gestor | Media — requiere modelo de transportista ampliado | Media |
| G-06 | No procesa pagos de retiros | Cobro en terreno por retiros/compras | Alta — POS Redelcom ya integrado en pos_mp | Media |
| G-07 | No solicitud de ubicación | Push de solicitud de ubicación al gestor | Alta — infraestructura GPS ya existe | Media |
| G-08 | No incorpora variables de perecibles/congelados | Gestión de cadena de frío | Media — requiere campos adicionales + sensores IoT | Baja |
| G-09 | La gestión no escapa del concepto "ruta" | Gestiones sin ruta (on-demand, visitas libres) | Media — requiere desacoplar gestión de ruta | **Alta** |
| G-10 | Entregas OnDemand | Modo on-demand fuera de planificación | Relacionada con G-09 | Media |
| G-11 | No sorting de paquetería | Clasificación y sorting de paquetes | Baja — se acerca a fulfillment | Baja |
| G-12 | No gestiona sucursales Pickup/DropOff | Puntos de pickup y dropoff | Media | Baja |

#### 2.3.3 Integraciones

| # | Gap (NO HACE hoy) | Aspiración 2.0 | Viabilidad técnica | Prioridad sugerida |
|---|---|---|---|---|
| G-13 | No integración SAP | Conector SAP ERP | Media — vía API layer existente | Media |
| G-14 | No integración ERP genérico | Framework de integración ERP | Alta — microapi ya es API de integración | Media |
| G-15 | No plugin para ecommerce | Plugin/conector para plataformas e-commerce | Alta | Media |
| G-16 | Múltiples integraciones | Framework ampliable de integraciones | Alta — events-services ya es middleware | **Alta** |

#### 2.3.4 Inteligencia y Automatización

| # | Gap (NO HACE hoy) | Aspiración 2.0 | Viabilidad técnica | Prioridad sugerida |
|---|---|---|---|---|
| G-17 | No predice elementos de atraso | Predicción de retrasos en entrega | Alta — Infopredictor ya existe, ampliar modelo | **Alta** |
| G-18 | Asistente IA para soporte | Chatbot/IA para help center | Alta — mentioned en canvas de interfaces | Media |

#### 2.3.5 Comercial y Reportes

| # | Gap (NO HACE hoy) | Aspiración 2.0 | Viabilidad técnica | Prioridad sugerida |
|---|---|---|---|---|
| G-19 | No integrado a Transbank para cobros de clientes nuevos | Onboarding + cobro integrado desde el producto | Alta — api-suscripciones ya existe | **Alta** |
| G-20 | No reportería con fechas muy prolongadas | Reportes con rangos extensos sin degradación | Alta — requiere optimización de queries/BI | Media |
| G-21 | No es plataforma modular (quitar/agregar con un click) | Modularidad real: features activables on-demand | Alta — modules-api ya existe | **Alta** |

---

## 3. Modernización Técnica (Deuda del 1.5 → 2.0)

El análisis del código revela áreas que NO son funcionales pero que **condicionan la viabilidad de todo lo anterior**:

### 3.1 Arquitectura

| Área | v1.5 (Actual) | v2.0 (Objetivo) | Motivación |
|---|---|---|---|
| **Framework app principal** | CodeIgniter 3 (EOL) | Framework moderno (Laravel/NestJS) | Seguridad, soporte, comunidad |
| **Frontend** | AdminLTE 2 + jQuery + PHP templates | SPA con design system (React/Vue) | UX, mantenibilidad, componentes |
| **Multi-tenant** | DB-per-tenant (N migraciones) | Multi-tenant unificado (RLS o schema) | Escalabilidad, maintenance |
| **Sistema de colas** | Cron + flock (infotrack) | Queue real (Redis/SQS) | Fiabilidad, observabilidad |
| **API Gateway** | Sin gateway, servicios directos | API Gateway unificado | Rate limiting, auth, routing |
| **Hardcoded cliente logic** | `if clienteinfositioid == 1` | Strategy/Plugin pattern por configuración | Flexibilidad, generalidad |

### 3.2 Seguridad

| Área | v1.5 (Actual) | v2.0 (Objetivo) |
|---|---|---|
| **Auth** | Sesiones + JWT hardcoded | OAuth2 / OIDC unificado |
| **CSRF** | Deshabilitado | Habilitado |
| **XSS** | Filtering OFF | CSP + sanitización |
| **Secrets** | Hardcoded en código | Vault / Key Management |
| **Passwords** | MD5 legacy | bcrypt/argon2 sin fallback |
| **CORS** | Wildcard `*` | Allowlist explícito por origen |

### 3.3 Observabilidad y Calidad

| Área | v1.5 (Actual) | v2.0 (Objetivo) |
|---|---|---|
| **Tests** | Mínimos (solo NestJS services) | Cobertura ≥ 70% (unit + integration + e2e) |
| **Logging** | Archivos sueltos | Structured logging centralizado (ELK/Datadog) |
| **Monitoreo** | APM parcial (Inspector) | Observabilidad completa (traces, metrics, logs) |
| **SLA** | Sin SLA formal | 99,95% uptime (objetivo del equipo) |
| **CI/CD** | GitHub Actions básico | Pipeline con gates de calidad, security scanning, deploys automáticos |

### 3.4 Infraestructura

| Área | v1.5 (Actual) | v2.0 (Objetivo) |
|---|---|---|
| **Hosting** | Azure Web Apps (PaaS) | Docker Swarm (con opción a migrar a Azure App Services o equivalente) |
| **DB** | PostgreSQL (multi-DB manual) | PostgreSQL unificado + RLS o pooling |
| **Cache** | Memcached (infotrack) / Redis (microservicios) | Redis unificado |
| **File storage** | Google Drive (fotos) | Object storage (S3/Azure Blob) |
| **Nube híbrida** | No | Mencionado por equipo en canvas |

---

## 4. Stakeholders y Usuarios (del Canvas de Límites)

### 4.1 Tipos de Usuario

| Tipo | Sub-rol | Plataforma |
|---|---|---|
| **Operaciones en terreno** | Supervisor, Analista de datos, Jefatura/Gerencia, Community Manager | Web |
| | Repartidor/Transportista, Gestor en terreno, Visitante, Recaudador | Móvil |
| | Ejecutivo, Operador, Call Center/Soporte | Web |
| **Backoffice/Comercial** | Usuario oficina, Gerencia/Supervisión | Web |
| | Clientes de nuestros clientes | API/Tracking |
| **Interno/Técnico** | Soporte, Secretaria, Operador, Administrador, Admin Cliente | Web |
| | Gestor, Lector Peatón, Recaudador, Courier | Móvil |
| | Sistemas externos, Partner, Dispositivos | API |

### 4.2 Interfaces del Producto

| Canal | Estado v1.5 | Estado v2.0 |
|---|---|---|
| **Web (AdminLTE)** | ✅ Operativo | → Modernizar (SPA) |
| **App Android** | ✅ Operativo | → Mantener + sumar iOS |
| **API REST** | ✅ Operativo (microapi) | → Unificar y documentar |
| **Email** | ✅ Operativo (mail-api) | → Mantener |
| **SMS** | ✅ Operativo (LaNube) | → Mantener |
| **WhatsApp** | ✅ Operativo (Chat-API) | → Mantener |
| **Chatbot** | ❌ No existe | → Nuevo |
| **IoT** | ❌ No existe | → Explorar |
| **Tracking público** | ✅ Operativo | → Mejorar UX |

---

## 5. Restricciones y Supuestos

### Restricciones del producto

1. **No es offline (web):** La plataforma web requiere conectividad. La app móvil sí es offline-first.
2. **Android-first:** iOS es aspiracional para 2.0 pero no bloqueante del MVP.
3. **Conectado a internet:** Requiere conectividad para sincronización.
4. **Acotado a país:** Actualmente Chile. Multi-país es roadmap.
5. **Concepto "ruta":** Las gestiones se organizan en rutas. Escapar de este concepto es aspiracional.

### Supuestos para el 2.0

1. **Migración gradual:** No es rewrite from scratch. Se moderniza por módulos.
2. **Datos existentes:** Se debe migrar toda la data de tenants actuales.
3. **Clientes actuales continúan:** Starken, Samex, Macan y otros siguen en 2.0.
4. **Infraestructura cloud:** Se mantiene en Azure con opción a migrar a Docker Swarm, pudiendo evolucionar hacia Azure App Services o equivalente.
5. **Equipo de desarrollo:** Se cuenta con equipo para ejecutar la modernización.

---

## 6. Resumen de Brechas Prioritarias

### 🔴 Alta Prioridad (MVP 2.0)

| # | Brecha | Impacto |
|---|---|---|
| G-02 | App iOS / cross-platform | Expande mercado, retiene clientes |
| G-04 | Tracking tipo Waze en tiempo real | Diferenciador competitivo clave |
| G-09 | Gestiones sin ruta (on-demand) | Flexibilidad operacional |
| G-16 | Framework de múltiples integraciones | Escalabilidad comercial |
| G-17 | Predicción de retrasos | Valor analítico |
| G-19 | Onboarding Transbank integrado | Conversión comercial |
| G-21 | Modularidad real (activar/desactivar features) | Diferenciador vs competencia |

### 🟡 Media Prioridad (Roadmap 2.0)

| # | Brecha | Impacto |
|---|---|---|
| G-01 | PWA offline | Continuidad operacional |
| G-03 | Multi-país | Expansión geográfica |
| G-05 | Soporte peonetas | Nuevo tipo de gestor |
| G-06 | Pagos de retiros en terreno | Monetización |
| G-07 | Solicitud de ubicación | Control operacional |
| G-10 | Entregas on-demand | Flexibilidad |
| G-13/G-14 | Integración SAP/ERP | Mercado enterprise |
| G-15 | Plugin e-commerce | Mercado retail |
| G-18 | Asistente IA soporte | Eficiencia soporte |
| G-20 | Reportería extensos rangos | Analítica avanzada |

### 🟢 Baja Prioridad (Backlog)

| # | Brecha | Impacto |
|---|---|---|
| G-08 | Cadena de frío | Nicho específico |
| G-11 | Sorting de paquetería | Cerca de fulfillment (fuera de scope) |
| G-12 | Pickup/DropOff | Nuevo modelo operativo |

---

*Este documento se actualizará a medida que se incorporen nuevos documentos de entrada del equipo.*
