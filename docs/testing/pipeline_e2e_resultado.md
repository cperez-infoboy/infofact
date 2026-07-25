# Pipeline end-to-end — 1. Requerimientos tecnicos- funcionales.xlsx

Cadena completa: ingest → extract → consolidate → critique → classify → persist.

## Deltas por etapa

- Extraídos (crudos): **576**
- Tras consolidate: **534** (27 grupos duplicados, 27 fusiones propuestas)
- Tras critique: **421** (113 rechazados por alucinación, 95 marcados)
- Persistidos (padres): **421** + **240** sub-items derivados
- Contradicciones detectadas: **0**

## Distribución de clasificación

- type: `{'functional': 302, 'security': 56, 'usability': 11, 'data': 21, 'reliability': 11, 'constraint': 7, 'maintainability': 5, 'performance': 4, 'compliance': 2, 'process': 2}`
- priority: `{'must': 419, 'should': 2}`
- GT (col Descripción): **296**

Leyenda: ✓ span verificado · ⚠ span NO verificado · NOISE = sin match contra GT.

## Aplicación ruta / Req funcionales  (6)

### ✓ REQ-486 · security/must p.2
**Statement:** El sistema debe contar con un módulo de autenticación (login) para el control de acceso de usuarios a la aplicación.

> **Source:** Funcionalidad /Login

### ✓ REQ-487 · functional/must p.2
**Statement:** El sistema debe permitir la visualización y gestión de los servicios asignados para el día en curso.

> **Source:** Funcionalidad/ Servicios del dia

### ✓ REQ-490 · functional/must p.2
**Statement:** El sistema debe soportar un flujo operativo para el servicio de recogidas.

> **Source:** Flujo de aplicación/ Servicio de recogidas

### ✓ REQ-494 · functional/must p.2
**Statement:** El sistema debe incluir una funcionalidad de cotización de servicios accesible desde la aplicación.

> **Source:** Funcionalidad/ Cotización de servicio

### ✓ REQ-500 · functional/must p.2
**Statement:** El sistema debe generar un reporte de recaudo.

> **Source:** Reporte de recaudo

### ✓ REQ-504 · functional/must p.2
**Statement:** El sistema debe soportar la funcionalidad de cierre de ruta.

> **Source:** Funcionalidad /Cierre de ruta

## Arquitectura - Back End  (19)

### ✓ REQ-351 · functional/must p.3
**Statement:** Visualización de ejecución de servicios en línea (doble vía), evidenciando el cambio de estado de forma automática para los diferentes usuarios conectados al vehículo.

> **Source:** Visualización de ejecución de servicios en línea (doble vía), evidenciando el cambio de estado de forma automática para los diferentes usuarios conectados al vehículo.

### ✓ REQ-382 · constraint/must p.3
**Statement:** Aplicación con una arquitectura basada en eventos.

> **Source:** Aplicación con una arquitectura basada en eventos.

### ✓ REQ-384 · security/must p.3
**Statement:** Si existen logs, en estos no se debe colocar información sensible en claro.

> **Source:** Si existen logs, en estos no se debe colocar información sensible en claro.

### ✓ REQ-399 · performance/must p.3
**Statement:** La lectura de los códigos de barra no debe ser mayor a 300 milisegundos.

> **Source:** La lectura de los códigos de barra no debe ser mayor a 300 milisegundos.

### ✓ REQ-400 · functional/must p.3
**Statement:** La aplicación debe permitir la integración por medio de APIs para el envío de eventos.

> **Source:** La aplicación debe permitir la integración por medio de APIs (Envío de eventos, pagos, notificaciones, entre otros.)

### ✓ REQ-401 · functional/must p.3
**Statement:** Esta integración debe ser en ambas direcciones: enviar info. desde la aplicación a sistemas internos y al revés.

> **Source:** Esta integración debe ser en ambas direcciones: enviar info. desde la aplicación a sistemas internos y al revés.

### ✓ REQ-402 · performance/must p.3
**Statement:** La arquitectura debe permitir el procesamiento simultáneo de múltiples eventos generados por la aplicación y actualizados en tiempo real con el backend.

> **Source:** La arquitectura debe permitir el procesamiento simultáneo de múltiples eventos generados por la aplicación y actualizados en tiempo real con el backend.

### ✓ REQ-406 · performance/must p.3
**Statement:** La aplicación debe tener una arquitectura escalable.

> **Source:** La aplicación debe tener una arquitectura flexible y escalable.

### ✓ REQ-410 · reliability/must p.3
**Statement:** La aplicación debe tener una arquitectura resiliente a fallos.

> **Source:** La aplicación debe tener una arquitectura resiliente a fallos.

### ✓ REQ-415 · reliability/must p.3
**Statement:** La aplicación debe tener una arquitectura de alta disponibilidad mediante el uso de Microservicios y Kubernetes.

> **Source:** La aplicación debe tener una arquitectura de alta disponibilidad y que garantice su comunicación en tiempo real (uso de Microservicios, Kubernetes y WebSockets).

### ✓ REQ-419 · security/must p.3
**Statement:** La aplicación debe usar conexiones seguras HTTPS para la comunicación.

> **Source:** Implementación de protocolos seguros para la comunicación (conexiones seguras HTTPS, cifrado para datos sensibles como datos bancarios, validación de certificados de servidor SSL, inclusión de Headers de Seguridad(CSP)).

### ✓ REQ-420 · security/must p.3
**Statement:** Uso de firmas digitales en las transacciones usando claves asimétricas.

> **Source:** Uso de firmas digitales en las transacciones usando claves asimétricas.

### ✓ REQ-421 · security/must p.3
**Statement:** Implementar el concepto de Rate Limit que prevengan ataques de fuerza bruta.

> **Source:** Implementar el concepto de Rate Limit que prevengan ataques de fuerza bruta.

### ✓ REQ-422 · security/must p.3
**Statement:** Si hay almacenamiento de contraseñas estas deben estar encriptadas

> **Source:** Si hay almacenamiento de contraseñas estas deben estar encriptadas,

### ✓ REQ-423 · reliability/must p.3
**Statement:** El sistema debe implementar balanceo de cargas.

> **Source:** Implementación de balanceo de Cargas. Indicar como se garantiza.

### ⚠ REQ-613 · constraint/must p.3
**Statement:** El backend debe soportar una arquitectura basada en eventos que permita el procesamiento simultáneo de múltiples eventos generados por la aplicación y actualizados en tiempo real con el backend.

> **Source:** Aplicación con una arquitectura basada en eventos. [...] La arquitectura debe permitir el procesamiento simultáneo de múltiples eventos generados por la aplicación y actualizados en tiempo real con el backend.

### ✓ REQ-624 · maintainability/must p.3
**Statement:** La solución debe contar con despliegue automático y continuo (CI/CD) que mitigue el impacto en el despliegue de nuevas versiones.

> **Source:** Despliegue automático y continuo de la solución (CI/CD) mitigando el impacto en el despliegue de nuevas versiones.

### ⚠ REQ-625 · security/must p.3
**Statement:** El sistema debe implementar el concepto de Rate Limit que prevenga ataques de fuerza bruta.

> **Source:** Implementar el concepto de Rate Limit que prevengan ataques de fuerza bruta. [...] uso de Microservicios, Kubernetes y WebSockets [...] Implementación de balanceo de Cargas.

### ✓ REQ-626 · performance/must p.3
**Statement:** La lectura de los códigos de barra no debe ser mayor a 300 milisegundos.

> **Source:** La lectura de los códigos de barra no debe ser mayor a 300 milisegundos.

## Arquitectura - Back End - Despliegues  (1)

### ✓ REQ-426 · maintainability/must p.3
**Statement:** Despliegue automático y continuo de la solución (CI/CD) mitigando el impacto en el despliegue de nuevas versiones.

> **Source:** Despliegue automático y continuo de la solución (CI/CD) mitigando el impacto en el despliegue de nuevas versiones.

## Arquitectura- Front End  (15)

### ✓ REQ-350 · security/must p.3
**Statement:** La aplicación debe garantizar solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC).

> **Source:** La aplicación debe garantizar solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC).

### ✓ REQ-372 · constraint/must p.3
**Statement:** Aplicación nativa en Android ejecutándose en versiones Android 13 y superiores.

> **Source:** Aplicación nativa en Android ejecutándose en versiones Android 13 y superiores.

### ✓ REQ-373 · maintainability/must p.3
**Statement:** Se debe garantizar la homologación con nuevas versiones del sistema operativo (Android) evitando la obsolescencia tecnológica.

> **Source:** Se debe garantizar la homologación con nuevas versiones del sistema operativo (Android) evitando la obsolescencia tecnológica.

### ✓ REQ-377 · functional/must p.3
**Statement:** Aplicación optimizada para un funcionamiento "online / offline".

> **Source:** Aplicación optimizada para un funcionamiento "online / offline".

### ✓ REQ-383 · security/must p.3
**Statement:** Si hay necesidad de almacenar información en el dispositivo, todos los datos almacenados en el dispositivo deben estar cifrados.

> **Source:** Si hay necesidad de almacenar información en el dispositivo, todos los datos almacenados en el dispositivo deben estar cifrados.

### ✓ REQ-385 · security/must p.3
**Statement:** La aplicación debe permitir la integración con servicios para la autenticación de usuarios. Con el DA.

> **Source:** La aplicación debe permitir la integración con servicios para la autenticación de usuarios. Con el DA.

### ✓ REQ-388 · security/must p.3
**Statement:** La aplicación deberá garantizar la autenticación al momento de usar los diferentes servicios WEB haciendo uso de tokens de Acceso Seguro.

> **Source:** La aplicación deberá garantizar la autenticación al momento de usar los diferentes servicios WEB haciendo uso de tokens de Acceso Seguro y garantizar su renovación.

### ✓ REQ-389 · security/must p.3
**Statement:** La aplicación debe permitir integración con servicios OTP.

> **Source:** La aplicación debe permitir integración con servicios OTP.

### ✓ REQ-390 · security/must p.3
**Statement:** La aplicación debe validar la entrada de datos del usuario previniendo inyecciones de SQL y XSS.

> **Source:** Validación de entrada de datos por el usuario, previniendo inyecciones de  SQL, XSS, actualizaciones de de librerías,

### ✓ REQ-391 · security/must p.3
**Statement:** Implementación de reglas contra ingeniería inversa.

> **Source:** Implementación de reglas contra ingeniería inversa.

### ✓ REQ-396 · constraint/must p.3
**Statement:** La aplicación no debe ejecutar reglas de negocio, se debe garantizar que estas estén del lado del servidor.

> **Source:** La aplicación no debe ejecutar reglas de negocio, se debe garantizar que estas estén del lado del servidor.

### ✓ REQ-397 · constraint/must p.3
**Statement:** La aplicación debe estar disponible en la Play Store.

> **Source:** La aplicación debe estar disponible en la Play Store y se debe permitir actualizarse automáticamente (ejemplo en el Splash Screen).

### ✓ REQ-398 · functional/must p.3
**Statement:** La aplicación debe tener drivers para leer códigos de barra lineal (garantizando mínimamente Code-128, Code-39) y 2D (garantizando mínimamente Data Matrix y QR).

> **Source:** La aplicación debe tener drivers para leer códigos de barra lineal (garantizando mínimamente Code-128, Code-39) y 2D ( garantizando mínimamente Data Matrix  y QR).

### ✓ REQ-608 · functional/must p.3
**Statement:** La aplicación debe estar optimizada para un funcionamiento 'online / offline'.

> **Source:** Aplicación optimizada para un funcionamiento "online / offline".

### ✓ REQ-622 · security/must p.3
**Statement:** La aplicación deberá garantizar la autenticación al momento de usar los diferentes servicios WEB haciendo uso de tokens de Acceso Seguro y garantizar su renovación.

> **Source:** La aplicación deberá garantizar la autenticación al momento de usar los diferentes servicios WEB haciendo uso de tokens de Acceso Seguro y garantizar su renovación.

## Arquitectura- Front End / Arquitectura - Back End  (1)

### ⚠ REQ-617 · functional/must p.3
**Statement:** La aplicación debe estar optimizada para funcionamiento online/offline con arquitectura basada en eventos, permitiendo gestionar los datos generados en modo offline y sincronizarlos con el backend al recuperar la conexión.

> **Source:** Aplicación optimizada para un funcionamiento "online / offline". [...] Aplicación con una arquitectura basada en eventos.

## Ciberseguridad - Desarrollo Externo de software y sitios WEB  (2)

### ✓ REQ-647 · process/must p.3
**Statement:** Previo a la implementación y antes de cualquier liberación de nuevas versiones de software, se deben suministrar los resultados generales y la gestión de la remediación realizada de las pruebas de tipo SAST, IAST y DAST.

> **Source:** El aliado o proveedor debe suministrar previo a la implementación y antes de cualquier liberación de nuevas versiones de software, los resultados generales y la gestión de la remediación realizada de las siguientes pruebas:1. Pruebas de tipo (SAST, IAST y DAST )2. Pruebas de Penetración o Ética Hacking

### ✓ REQ-658 · compliance/must p.3
**Statement:** El desarrollo del software debe basarse en los principios y buenas prácticas del desarrollo seguro establecidas por OWASP.

> **Source:** El aliado o proveedor garantiza que se basa en los principios y buenas prácticas del desarrollo seguro establecidas por OWASP.

## Ciberseguridad - Req Técnicos  (5)

### ✓ REQ-627 · security/must p.3
**Statement:** El sistema debe garantizar que los procesos sostenidos de intercambio de información o archivos con la empresa se realicen mediante mecanismos seguros como SFTP o FTPS.

> **Source:** El aliado o proveedor debe garantizar que los procesos sostenidos del Intercambio de Información o archivos con la empresa se realice mediante mecanismos seguros como SFTP, FTPS.

### ✓ REQ-648 · security/must p.3
**Statement:** El sistema debe permitir la gestión, notificación y auditabilidad de incidentes de ciberseguridad que puedan afectar el servicio y la información del Grupo Logístico TCC.

> **Source:** Cláusula sobre la gestión, notificación de información ante Incidentes de Ciberseguridad que puedan afectar el servicio y la información del Grupo Logístico TCC.2. Cláusula que posibilite la auditabilidad hacia el servicio ofrecido por el proveedor.

### ✓ REQ-652 · reliability/must p.3
**Statement:** El sistema debe garantizar backups o copias de respaldo, indicando el tiempo de resguardo y la periodicidad de las copias.

> **Source:** El aliado o proveedor debe garantizar Backup o copias de respaldo (indicando el tiempo de resguardo y periodicidad de las copias).

### ✓ REQ-656 · security/must p.3
**Statement:** El sistema debe disponer de procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios, a la finalización del contrato con el Grupo Logístico TCC.

> **Source:** El aliado o proveedor debe disponer a la finalización del contrato que cuenta con los procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios con el Grupo Logístico TCC.

### ✓ REQ-657 · security/must p.3
**Statement:** El sistema debe gestionar las vulnerabilidades presentadas en el servicio y cumplir con los ANS de remediación según la severidad de las vulnerabilidades.

> **Source:** El aliado o proveedor se compromete a la gestión de vulnerabilidades presentadas en su servicio, cumplimiento con nuestros ANS para gestión de remediación según la severidad de las vulnerabilidades

## Ciberseguridad - Requerimientos generales de Ciberseguridad  (1)

### ✓ REQ-447 · security/must p.3
**Statement:** El aliado o proveedor debe garantizar que toda la información almacenada del Grupo Logístico TCC cumple con los principios de disponibilidad, integridad y confidencialidad.

> **Source:** El aliado o proveedor debe garantizar que toda la información almacenada del Grupo Logístico TCC cumple con los principios de disponibilidad, integridad y confidencialidad. Se debe adjuntar evidencia.

## Ciberseguridad - Seguridad de la información  (1)

### ✓ REQ-442 · compliance/must p.3
**Statement:** Notificación a los usuarios de la aplicación sobre recolección de datos.

> **Source:** Notificación a los usuarios de la aplicación sobre recolección de datos.

## Ciberseguridad - Servicios en nube  (5)

### ✓ REQ-628 · security/must p.3
**Statement:** La aplicación debe permitir la administración y definición de roles y perfiles.

> **Source:** Administración definición de roles y perfiles.

### ✓ REQ-633 · security/must p.3 · NOISE
**Statement:** El sistema debe implementar controles de protección contra la manipulación de datos.

> **Source:** El aliado o proveedor garantiza que cuenta con la protección para Seguridad en la Ejecución y Protección Contra: anexar evidencia1.Manipulación de Datos

### ✓ REQ-637 · security/must p.3 · NOISE
**Statement:** El sistema debe incorporar mecanismos de prevención de exfiltración de información.

> **Source:** 2Prevención de Exfiltración de Información

### ✓ REQ-642 · security/must p.3
**Statement:** El sistema debe soportar restricciones de acceso por geolocalización que permitan bloquear o permitir conexiones según el país o región de origen.

> **Source:** El aliado o proveedor debe garantizar la restricción de conexiones por Geolocalización.

### ✓ REQ-643 · maintainability/must p.3 · NOISE
**Statement:** El sistema debe estar diseñado con una arquitectura escalable que permita actualizaciones y versionamiento.

> **Source:** El aliado o proveedor debe garantizar escalabilidad: Actualizaciones y versionamiento.

## Ciberseguridad, Proyecto diseño de aplicación ruta -Req Técnicos  (6)

### ✓ REQ-452 · security/must p.3
**Statement:** El aliado o proveedor debe garantizar que los procesos sostenidos del Intercambio de Información o archivos con la empresa se realice mediante mecanismos seguros como SFTP, FTPS.

> **Source:** El aliado o proveedor debe garantizar que los procesos sostenidos del Intercambio de Información o archivos con la empresa se realice mediante mecanismos seguros como SFTP, FTPS.

### ✓ REQ-453 · security/must p.3
**Statement:** El aliado o proveedor debe permitir pruebas de ciberseguridad en el servicio ofrecido.

> **Source:** El aliado o proveedor debe permitir  pruebas de ciberseguridad en el servicio ofrecido.

### ✓ REQ-454 · reliability/must p.3
**Statement:** El aliado o proveedor garantiza y evidencia anualmente que el servicio a prestar incluye procedimientos y tecnologías que aseguran la alta disponibilidad y continuidad de la operación de los servicios del Grupo Logístico TCC.

> **Source:** El aliado o proveedor garantiza y evidencia que el servicio a prestar incluye procedimientos y tecnologías que aseguran la alta disponibilidad y continuidad de la operación de los servicios del Grupo Logístico TCC. La evidencia debe ser presentada anualmente.

### ✓ REQ-459 · security/must p.3
**Statement:** El aliado o proveedor debe disponer a la finalización del contrato que cuenta con los procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios con el Grupo Logístico TCC.

> **Source:** El aliado o proveedor debe disponer a la finalización del contrato que cuenta con los procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios con el Grupo Logístico TCC.

### ✓ REQ-460 · reliability/must p.3
**Statement:** El aliado o proveedor debe garantizar Backup o copias de respaldo indicando el tiempo de resguardo y periodicidad de las copias.

> **Source:** El aliado o proveedor debe garantizar Backup o copias de respaldo (indicando el tiempo de resguardo y periodicidad de las copias).

### ✓ REQ-461 · security/must p.3
**Statement:** El aliado o proveedor debe cumplir con los lineamientos de Ciberseguridad del Grupo Logístico TCC.

> **Source:** El aliado o proveedor debe cumplir con los lineamientos de Ciberseguridad del Grupo Logístico TCC.

## Ciberseguridad, Proyecto diseño de aplicación ruta -Req Técnicos / Desarrollo Externo de software y sitios WEB  (1)

### ✓ REQ-465 · security/must p.3
**Statement:** El aliado o proveedor garantiza incorporar en el desarrollo mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0.

> **Source:** El aliado o proveedor garantiza incorporar en el desarrollo mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0. Anexar evidencia.

## Ciberseguridad, Proyecto diseño de aplicación ruta -Req Técnicos / Servicio pruebas de Ciberseguridad  (1)

### ✓ REQ-485 · process/must p.3
**Statement:** El aliado o proveedor debe garantizar la ejecución de retest para verificación del cierre de los hallazgos encontrados en las pruebas y análisis ejecutados.

> **Source:** El aliado o proveedor debe garantizar la ejecución de retest para verificación del cierre de los hallazgos encontrados en las pruebas y análisis ejecutados.

## Ciberseguridad, Proyecto diseño de aplicación ruta -Req Técnicos / Servicios en nube  (7)

### ✓ REQ-466 · security/must p.3
**Statement:** El aliado o proveedor dispone de un lineamiento o procedimiento para reportar la ocurrencia de Incidentes de ciberseguridad que se puedan presentar sobre la información del Grupo Logístico TCC almacenada en la nube administrada por el proveedor.

> **Source:** El aliado o proveedor dispone de un lineamiento o procedimiento para reportar la ocurrencia de Incidentes de ciberseguridad que se puedan presentar sobre la información del Grupo Logístico TCC almacenada en la nube administrada por el proveedor. Anexar lineamientos.

### ✓ REQ-467 · security/must p.3
**Statement:** El aliado o proveedor garantiza que cuenta con protección contra la Manipulación de Datos.

> **Source:** 1.Manipulación de Datos

### ✓ REQ-471 · security/must p.3
**Statement:** El aliado o proveedor garantiza que cuenta con prevención de la Exfiltración de Información.

> **Source:** 2Prevención de Exfiltración de Información

### ✓ REQ-475 · security/must p.3
**Statement:** El aliado o proveedor garantiza que cuenta con la protección contra Escalamiento de Privilegios.

> **Source:** 3.Protección Contra Escalamiento de Privilegios.

### ✓ REQ-479 · security/must p.3
**Statement:** El aliado o proveedor garantiza que cuenta con administración, definición de roles y perfiles.

> **Source:** 4.Administración definición de roles y perfiles.

### ✓ REQ-480 · security/must p.3
**Statement:** El aliado o proveedor debe garantizar la restricción de conexiones por Geolocalización.

> **Source:** El aliado o proveedor debe garantizar la restricción de conexiones por Geolocalización. Anexar evidencia o explicar como se garantiza.

### ✓ REQ-481 · maintainability/must p.3
**Statement:** El aliado o proveedor debe garantizar escalabilidad: Actualizaciones y versionamiento.

> **Source:** El aliado o proveedor debe garantizar escalabilidad: Actualizaciones y versionamiento. Anexar evidencia o explicar como se garantiza.

## Ejecucción de servicio de recolección -Creación de servicios  (2)

### ✓ REQ-551 · functional/must p.2
**Statement:** El sistema debe consultar automáticamente la base de datos interna para identificar si el remitente o destinatario ya existen.

> **Source:** El sistema debe consultar automáticamente la base de datos interna para identificar si el remitente o destinatario ya existen.

### ✓ REQ-555 · functional/must p.2
**Statement:** Al ingresar un nuevo servicio, la aplicación debe consumir un servicio de integración que devuelva el valor estimado del flete.

> **Source:** Al ingresar un nuevo servicio, la aplicación debe consumir un servicio de integración que devuelva el valor estimado del flete.

## Ejecucción de servicios de recolección-Registro de motivo de no recolección  (1)

### ✓ REQ-553 · functional/must p.2
**Statement:** El sistema debe permitir al usuario seleccionar un motivo de no recolección desde un catálogo predefinido, desplegado mediante búsqueda predictiva por palabra clave.

> **Source:** La aplicación debe permitir al usuario seleccionar un motivo de no recolección desde un catálogo predefinido, desplegado mediante búsqueda predictiva por palabra clave.

## Ejecución de servicio de recolección -Lectura de unidades  (2)

### ✓ REQ-550 · functional/must p.2
**Statement:** El sistema debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR en el dispositivo móvil.

> **Source:** La aplicación debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR.

### ✓ REQ-563 · functional/must p.2
**Statement:** La aplicación debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR.

> **Source:** La aplicación debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR.

## Ejecución de servicio de recolección-Conceptos de recaudo  (1)

### ✓ REQ-556 · functional/must p.2
**Statement:** El sistema debe registrar cada transacción relacionando información de pago e integrarse con el collection management.

> **Source:** La aplicación registre cada transacción relacionando información de pago e integre con el collection management.

## Ejecución de servicios de recolección-Funcionalidad de seguridad  (1)

### ✓ REQ-552 · security/must p.2
**Statement:** El sistema debe enviar al cliente el código de seguridad desde la planeación del servicio para su validación durante la recolección.

> **Source:** El cliente debe validar la recolección ingresando este código en el dispositivo del operador, para contrastarlo con el que se le envía desde la planeación del servicio.

## Experiencia de usuario  (8)

### ✓ REQ-353 · usability/must p.2
**Statement:** La aplicación deberá estar diseñada bajo principios de experiencia de usuario (UX) que prioricen la simplicidad, claridad y eficiencia, permitiendo a los usuarios completar tareas clave con el mínimo número de interacciones posibles (pocos clics).

> **Source:** La aplicación deberá estar diseñada bajo principios de experiencia de usuario (UX) que prioricen la simplicidad, claridad y eficiencia, permitiendo a los usuarios completar tareas clave con el mínimo número de interacciones posibles (pocos clics).

### ✓ REQ-358 · usability/must p.2
**Statement:** La aplicación debe permitir a los usuarios completar tareas clave con el mínimo número de interacciones posibles (pocos clics).

> **Source:** La aplicación deberá estar diseñada bajo principios de experiencia de usuario (UX) que prioricen la simplicidad, claridad y eficiencia, permitiendo a los usuarios completar tareas clave con el mínimo número de interacciones posibles (pocos clics).

### ✓ REQ-362 · usability/must p.2 · NOISE
**Statement:** La aplicación deberá presentar elementos interactivos accesibles.

> **Source:** Diseño centrado en el usuario, con interfaces limpias, jerarquía visual clara y elementos interactivos accesibles.

### ✓ REQ-367 · usability/must p.2
**Statement:** Flujos de navegación optimizados, que permitan realizar tareas frecuentes (como cumplir una entrega, una recogida) en máximo 4 pantallas con pocos clics.

> **Source:** Flujos de navegación optimizados, que permitan realizar tareas frecuentes (como cumplir una entrega, una recogida) en máximo 4 pantallas con pocos clics.

### ✓ REQ-368 · usability/must p.2
**Statement:** Accesos directos contextuales y botones de acción rápida para tareas recurrentes.

> **Source:** Accesos directos contextuales y botones de acción rápida para tareas recurrentes.

### ✓ REQ-369 · usability/must p.2
**Statement:** Retroalimentación inmediata al usuario ante cada acción (confirmaciones, errores, estados de carga).

> **Source:** Retroalimentación inmediata al usuario ante cada acción (confirmaciones, errores, estados de carga).

### ✓ REQ-370 · usability/must p.2
**Statement:** El sistema debe ser compatible con dispositivos móviles mediante diseño responsivo.

> **Source:** Compatibilidad móvil con diseño responsivo y gestos táctiles intuitivos.

### ✓ REQ-371 · functional/must p.2
**Statement:** Personalización de vistas según el rol del usuario para mostrar solo la información relevante.

> **Source:** Personalización de vistas según el rol del usuario para mostrar solo la información relevante.

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicio de recolección - Conceptos de recaudo  (2)

### ✓ REQ-157 · functional/must p.2
**Statement:** La aplicación registre cada transacción relacionando información de pago.

> **Source:** La aplicación registre cada transacción relacionando información de pago e integre con el collection management.

### ✓ REQ-158 · functional/must p.2
**Statement:** La aplicación integre con el collection management.

> **Source:** La aplicación registre cada transacción relacionando información de pago e integre con el collection management.

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicio de recolección - Creación de servicios  (8)

### ✓ REQ-167 · functional/must p.2
**Statement:** El sistema debe permitir la creación de nuevos servicios de remesa desde el dispositivo móvil, incluyendo: datos del remitente y destinatario, características de las unidades (peso, volumen, tipo de mercancía), captura de evidencia fotográfica y observaciones.

> **Source:** Creación de Servicios de remesa desde un servicio de Recolección1)Creación Ágil de Servicio de Remesa:El sistema debe permitir la creación de nuevos servicios desde el dispositivo móvil, incluyendo:Datos del remitente y destinatario.Características de las unidades (peso, volumen, tipo de mercancía).Captura de evidencia fotográfica y observaciones..

### ✓ REQ-168 · functional/must p.2 · NOISE
**Statement:** El sistema debe validar campos obligatorios como correo electrónico y número de contacto.

> **Source:** El flujo debe ser intuitivo, con navegación simplificada y validación de campos obligatorios como correo electrónico y número de contacto.

### ✓ REQ-169 · functional/must p.2
**Statement:** El sistema debe consultar automáticamente la base de datos interna para identificar si el remitente o destinatario ya existen.

> **Source:** Optimización mediante Herencia de Datos Existentes1) El sistema debe consultar automáticamente la base de datos interna para identificar si el remitente o destinatario ya existen..

### ✓ REQ-170 · functional/must p.2
**Statement:** En caso afirmativo, debe precargar los siguientes datos: Nombre o razón social, Número de identificación (NIT/CC), Dirección y ciudad, Forma de pago habitual, Cuenta asociada (si aplica).

> **Source:** En caso afirmativo, debe precargar los siguientes datos:Nombre o razón social.Número de identificación (NIT/CC).Dirección y ciudad.Forma de pago habitual.Cuenta asociada (si aplica).Esta funcionalidad debe estar disponible tanto para remitente como para destinatario, permitiendo que el usuario solo deba ingresar los datos de la unidad logística, reduciendo tiempos de digitación y errores operativos.

### ✓ REQ-171 · functional/must p.2 · NOISE
**Statement:** Esta funcionalidad de herencia de datos debe estar disponible tanto para remitente como para destinatario.

> **Source:** Esta funcionalidad debe estar disponible tanto para remitente como para destinatario, permitiendo que el usuario solo deba ingresar los datos de la unidad logística, reduciendo tiempos de digitación y errores operativos.

### ✓ REQ-172 · functional/must p.2
**Statement:** Al ingresar un nuevo servicio, la aplicación debe consumir un servicio de integración que devuelva el valor estimado del flete.

> **Source:** Cotizar en Línea1)Al ingresar un nuevo servicio, la aplicación debe consumir un servicio de integración que devuelva el valor estimado del flete.El cotizador debe considerar variables como origen, destino, tipo de unidad, peso y volumen..

### ✓ REQ-173 · functional/must p.2 · NOISE
**Statement:** El cotizador debe considerar variables como origen, destino, tipo de unidad, peso y volumen.

> **Source:** Cotizar en Línea1)Al ingresar un nuevo servicio, la aplicación debe consumir un servicio de integración que devuelva el valor estimado del flete.El cotizador debe considerar variables como origen, destino, tipo de unidad, peso y volumen..

### ✓ REQ-174 · usability/must p.2
**Statement:** La interfaz debe estar diseñada para facilitar la creación del servicio en menos de 2 minutos, con campos auto completables, búsqueda predictiva y botones de acción visibles.

> **Source:** La interfaz debe estar diseñada para facilitar la creación del servicio en menos de 2 minutos, con campos auto completables, búsqueda predictiva y botones de acción visibles..

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicio de recolección - Inteligencia contextual para escenarios de recolección  (2)

### ✓ REQ-165 · functional/must p.2
**Statement:** La aplicación debe ser capaz de identificar automáticamente el tipo de recolección al escanear una unidad: Recolección programada, Recolección no programada, Recolección de devolución.

> **Source:** Inteligencia Contextual para Escenarios de RecolecciónLa aplicación debe ser capaz de identificar automáticamente el tipo de recolección al escanear una unidad:Recolección programada.Recolección no programada.Recolección de devolución.Según el tipo identificado, debe activar el flujo correspondiente de forma automática..

### ✓ REQ-166 · functional/must p.2 · NOISE
**Statement:** Según el tipo identificado, debe activar el flujo correspondiente de forma automática.

> **Source:** Inteligencia Contextual para Escenarios de RecolecciónLa aplicación debe ser capaz de identificar automáticamente el tipo de recolección al escanear una unidad:Recolección programada.Recolección no programada.Recolección de devolución.Según el tipo identificado, debe activar el flujo correspondiente de forma automática..

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicio de recolección - Lectura de unidades  (3)

### ✓ REQ-162 · functional/must p.2
**Statement:** La aplicación debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR.

> **Source:** Lectura de Unidades desde un Servicio de Recolección:1) La aplicación debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR..

### ✓ REQ-163 · functional/must p.2
**Statement:** Debe validar que las unidades escaneadas correspondan al servicio asignado.

> **Source:** Lectura de Unidades desde un Servicio de Recolección:2) Debe validar que las unidades escaneadas correspondan al servicio asignado y registrar la hora y ubicación del escaneo..

### ✓ REQ-164 · functional/must p.2
**Statement:** Debe registrar la hora y ubicación del escaneo.

> **Source:** Lectura de Unidades desde un Servicio de Recolección:2) Debe validar que las unidades escaneadas correspondan al servicio asignado y registrar la hora y ubicación del escaneo..

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicio de recolección - Lectura de unidades anónimas  (3)

### ✓ REQ-180 · functional/must p.2
**Statement:** La aplicación debe permitir escanear unidades que no estén previamente asociadas a un servicio (unidades anónimas).

> **Source:** Lectura de Códigos de Unidades Anónimas1)La aplicación debe permitir escanear unidades que no estén previamente asociadas a un servicio (unidades anónimas)..

### ✓ REQ-181 · functional/must p.2
**Statement:** Al escanear una unidad anónima, debe capturar: Código de la unidad, Fecha hora y ubicación, Observaciones del operador.

> **Source:** Al escanear, debe capturar:Código de la unidad.Fecha, hora y ubicación.Observaciones del operador.Estas unidades deben quedar registradas para trazabilidad y posterior asociación..

### ✓ REQ-182 · functional/must p.2
**Statement:** Estas unidades anónimas deben quedar registradas para trazabilidad y posterior asociación.

> **Source:** Estas unidades deben quedar registradas para trazabilidad y posterior asociación..

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicio de recolección - Sincronización de servicios  (5)

### ✓ REQ-175 · functional/must p.2
**Statement:** La aplicación debe permitir sincronizar los servicios asignados por diferentes criterios: Por ruta, por zona geográfica, por fecha, por tipo de cliente, por ID de servicio.

> **Source:** La aplicación debe permitir sincronizar los servicios asignados por diferentes criterios:Por ruta, por zona geográfica, por fecha, por tipo de cliente, por ID de servicio.

### ✓ REQ-176 · data/must p.2
**Statement:** La sincronización debe incluir: Datos del servicio, Información del remitente, Estado actual del servicio.

> **Source:** La sincronización debe incluir:Datos del servicio.Información del remitente.Estado actual del servicio..

### ✓ REQ-177 · functional/must p.2
**Statement:** El flujo de sincronización debe contemplar: 1) Consulta al servidor central. 2) Sincronización de datos al dispositivo. 3) Confirmación de sincronización exitosa.

> **Source:** El flujo debe contemplar:1)Consulta al servidor central.2)Sincronización de datos al dispositivo.3)Confirmación de sincronización exitosa..

### ✓ REQ-178 · functional/must p.2
**Statement:** El flujo de sincronización debe contemplar: 2) Sincronización de datos al dispositivo.

> **Source:** El flujo debe contemplar:1)Consulta al servidor central.2)Sincronización de datos al dispositivo.3)Confirmación de sincronización exitosa..

### ✓ REQ-179 · functional/must p.2
**Statement:** El flujo de sincronización debe contemplar: 3) Confirmación de sincronización exitosa.

> **Source:** El flujo debe contemplar:1)Consulta al servidor central.2)Sincronización de datos al dispositivo.3)Confirmación de sincronización exitosa..

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicios de recolección - Funcionalidad de seguridad  (3)

### ✓ REQ-187 · security/must p.2
**Statement:** El sistema debe generar un código de seguridad único por servicio.

> **Source:** El sistema debe generar un código de seguridad único por servicio.

### ✓ REQ-188 · security/must p.2
**Statement:** El cliente debe validar la recolección ingresando este código en el dispositivo del operador, para contrastarlo con el que se le envía desde la planeación del servicio.

> **Source:** El cliente debe validar la recolección ingresando este código en el dispositivo del operador, para contrastarlo con el que se le envía desde la planeación del servicio.

### ✓ REQ-189 · security/must p.2
**Statement:** Esta validación debe quedar registrada como evidencia de cumplimiento, relacionando usuario, fecha, hora y ID de servicio.

> **Source:** Esta validación debe quedar registrada como evidencia de cumplimiento, relacionando usuario, fecha, hora y ID de servicio.

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicios de recolección - Registro de motivo de no recolección  (3)

### ✓ REQ-190 · functional/must p.2
**Statement:** La aplicación debe permitir al usuario seleccionar un motivo de no recolección desde un catálogo predefinido, desplegado mediante búsqueda predictiva por palabra clave.

> **Source:** La aplicación debe permitir al usuario seleccionar un motivo de no recolección desde un catálogo predefinido, desplegado mediante búsqueda predictiva por palabra clave.

### ✓ REQ-191 · functional/must p.2
**Statement:** El motivo seleccionado debe quedar asociado al servicio y visible en la trazabilidad del mismo.

> **Source:** El motivo seleccionado debe quedar asociado al servicio y visible en la trazabilidad del mismo.

### ✓ REQ-192 · functional/must p.2
**Statement:** El motivo seleccionado debe quedar asociado al servicio y visible en la trazabilidad del mismo.

> **Source:** El motivo seleccionado debe quedar asociado al servicio y visible en la trazabilidad del mismo.

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicios de recolección - Registro de servicios no programados  (3)

### ✓ REQ-183 · functional/must p.2
**Statement:** La aplicación debe permitir registrar servicios de recolección no programados durante la ejecución de una ruta u orden de trabajo.

> **Source:** La aplicación debe permitir registrar servicios de recolección no programados durante la ejecución de una ruta u orden de trabajo..

### ✓ REQ-184 · functional/must p.2
**Statement:** Estos servicios no programados deben quedar identificados como "no programados".

> **Source:** Estos servicios deben quedar identificados como “no programados” y seguir el mismo flujo de validación y sincronización..

### ✓ REQ-185 · functional/must p.2
**Statement:** Estos servicios no programados deben seguir el mismo flujo de validación y sincronización.

> **Source:** Estos servicios deben quedar identificados como “no programados” y seguir el mismo flujo de validación y sincronización..

## Flujo de aplicación / Servicio de recogidas / Ejecución de servicios de recolección - Resumen servicio  (1)

### ✓ REQ-186 · functional/must p.2
**Statement:** La aplicación debe mostrar un resumen en tiempo real del estado de la recolección: Total de unidades recolectadas, Variables por tipo de unidad, cliente, estado, Alertas por inconsistencias o unidades faltantes.

> **Source:** La aplicación debe mostrar un resumen en tiempo real del estado de la recolección:Total de unidades recolectadas.Variables por tipo de unidad, cliente, estado.Alertas por inconsistencias o unidades faltantes..

## Flujo de aplicación / Servicio de recogidas / Pantalla inicio de ejecución de recolección - Validaciones de servicio  (1)

### ✓ REQ-154 · functional/must p.2
**Statement:** Es necesario que realice validaciones antes de ejecutar un servicio tales como: Franjas horarias para ejecutar el servicio.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  Franjas horarias para ejecutar el servicio.

## Flujo de aplicación/ Servicio de recogidas  (13)

### ✓ REQ-238 · functional/must p.2
**Statement:** Si el servicio ya fue recolectado, debe mostrarse un mensaje informativo y bloquear la edición.

> **Source:** Si el servicio ya fue recolectado, debe mostrarse un mensaje informativo y bloquear la edición.

### ✓ REQ-239 · functional/must p.2
**Statement:** Si el servicio ya fue recolectado, debe bloquearse la edición.

> **Source:** Si el servicio ya fue recolectado, debe mostrarse un mensaje informativo y bloquear la edición.

### ✓ REQ-240 · security/must p.2
**Statement:** Toda modificación realizada debe quedar registrada en el sistema con: Usuario que realizó la edición, Fecha y hora del cambio, Valores anteriores y nuevos de cada campo modificado.

> **Source:** Toda modificación realizada debe quedar registrada en el sistema con:Usuario que realizó la edición.Fecha y hora del cambio.Valores anteriores y nuevos de cada campo modificado.

### ✓ REQ-241 · functional/must p.2
**Statement:** Esta trazabilidad debe estar disponible para consulta desde el módulo de auditoría o trazabilidad del servicio.

> **Source:** Esta trazabilidad debe estar disponible para consulta desde el módulo de auditoría o trazabilidad del servicio.

### ✓ REQ-242 · functional/must p.2
**Statement:** Una vez editadas las características, el sistema debe recalcular automáticamente las condiciones del servicio, incluyendo la liquidación si aplica.

> **Source:** Una vez editadas las características, el sistema debe:1) Recalcular automáticamente las condiciones del servicio, incluyendo la liquidación si aplica.

### ✓ REQ-243 · functional/must p.2
**Statement:** Una vez editadas las características, el sistema debe actualizar la información en todos los módulos relacionados (por ejemplo, trazabilidad, módulo de cliente, módulo de liquidación).

> **Source:** Una vez editadas las características, el sistema debe:2) Actualizar la información en todos los módulos relacionados (por ejemplo, trazabilidad, módulo de cliente, módulo de liquidación).

### ✓ REQ-244 · functional/must p.2
**Statement:** Una vez editadas las características, el sistema debe sincronizar los cambios con el servidor central en tiempo real o en cuanto haya conectividad.

> **Source:** Una vez editadas las características, el sistema debe:3) Sincronizar los cambios con el servidor central en tiempo real o en cuanto haya conectividad.

### ✓ REQ-245 · functional/must p.2
**Statement:** Debe existir una pantalla donde se evidencie la culminación exitosa, y evidencie opciones de navegar a la próxima parada, ver la próxima parada, e ir a servicios del día (Ir a ruta).

> **Source:** Pantalla donde se evidencie la culminación exitosa, y evidencie opciones de navegar a la próxima parada, ver la próxima parada, e ir a servicios del día (Ir a ruta).

### ✓ REQ-576 · functional/must p.2
**Statement:** El sistema debe sincronizar los cambios con el servidor central en tiempo real o en cuanto haya conectividad.

> **Source:** Sincronizar los cambios con el servidor central en tiempo real o en cuanto haya conectividad.

### ✓ REQ-586 · functional/must p.2 · NOISE
**Statement:** El sistema debe disponer de un módulo de auditoría o trazabilidad accesible desde la aplicación para consulta del histórico de modificaciones.

> **Source:** Esta trazabilidad debe estar disponible para consulta desde el módulo de auditoría o trazabilidad del servicio.

### ✓ REQ-587 · functional/must p.2
**Statement:** El sistema debe registrar los valores anteriores y nuevos de cada campo modificado.

> **Source:** Toda modificación realizada debe quedar registrada en el sistema con:Usuario que realizó la edición.Fecha y hora del cambio.Valores anteriores y nuevos de cada campo modificado.

### ✓ REQ-588 · functional/must p.2
**Statement:** El sistema debe actualizar la información editada en todos los módulos relacionados (por ejemplo, trazabilidad, módulo de cliente, módulo de liquidación).

> **Source:** Actualizar la información en todos los módulos relacionados (por ejemplo, trazabilidad, módulo de cliente, módulo de liquidación).

### ✓ REQ-591 · functional/must p.2
**Statement:** Si el servicio ya fue recolectado, el sistema debe mostrar un mensaje informativo y bloquear la edición de sus características.

> **Source:** Si el servicio ya fue recolectado, debe mostrarse un mensaje informativo y bloquear la edición.

## Flujo de aplicación/ Servicio de recogidas - Captura de Datos de Geolocalización y Temporalidad  (2)

### ✓ REQ-209 · functional/must p.2
**Statement:** El sistema debe registrar automáticamente la latitud y longitud del dispositivo al momento de la certificación.

> **Source:** El sistema debe registrar automáticamente los siguientes datos al momento de la certificación:Latitud y longitud del dispositivo.

### ✓ REQ-210 · functional/must p.2
**Statement:** El sistema debe registrar automáticamente la fecha y hora exacta del evento al momento de la certificación.

> **Source:** El sistema debe registrar automáticamente los siguientes datos al momento de la certificación:Latitud y longitud del dispositivo.Fecha y hora exacta del evento.

## Flujo de aplicación/ Servicio de recogidas - Captura de Evidencia Fotográfica  (4)

### ✓ REQ-196 · functional/must p.2
**Statement:** La aplicación debe permitir tomar una o más fotografías como evidencia del intento de recolección fallido.

> **Source:** La aplicación debe permitir tomar una o más fotografías como evidencia del intento de recolección fallido.

### ✓ REQ-197 · data/must p.2
**Statement:** Las imágenes deben almacenarse junto con el motivo y las coordenadas.

> **Source:** Las imágenes deben almacenarse junto con el motivo y las coordenadas, y estar disponibles para consulta posterior.

### ✓ REQ-198 · functional/must p.2
**Statement:** Las imágenes deben estar disponibles para consulta posterior.

> **Source:** Las imágenes deben almacenarse junto con el motivo y las coordenadas, y estar disponibles para consulta posterior.

### ✓ REQ-199 · functional/must p.2
**Statement:** La aplicación debe permitir la validación de la fotografía tomada, si es coherente con el motivo de no recolección planteado.

> **Source:** La aplicación debe permitir la validación de la fotografía tomada, si es coherente con el motivo de no recolección planteado.

## Flujo de aplicación/ Servicio de recogidas - Cumplimiento y Certificación de Recogida  (6)

### ✓ REQ-204 · functional/must p.2
**Statement:** La aplicación debe capturar evidencia fotográfica del paquete o entorno como uno de los elementos requeridos para certificar la ejecución de una recogida.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:1)Evidencia fotográfica del paquete o entorno.

### ✓ REQ-205 · functional/must p.2
**Statement:** La aplicación debe capturar firma digital del remitente o responsable de entrega como uno de los elementos requeridos para certificar la ejecución de una recogida.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:2)Firma digital del remitente o responsable de entrega.

### ✓ REQ-206 · functional/must p.2
**Statement:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura del registro del nombre completo, número de documento de identidad y número telefónico de la persona que entrega.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:3)Registro del nombre completo, número de documento de identidad y número telefónico de la persona que entrega.

### ✓ REQ-207 · functional/must p.2
**Statement:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de campo de texto libre para observaciones adicionales.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:4)Campo de texto libre para observaciones adicionales.

### ✓ REQ-208 · data/must p.2
**Statement:** Esta información debe quedar asociada al número de servicio de recogida.

> **Source:** Esta información debe quedar asociada al número de servicio de recogida y almacenarse de forma segura en el sistema.

### ✓ REQ-211 · reliability/must p.2
**Statement:** En caso de no contar con conexión a red, la aplicación debe almacenar esta información localmente y sincronizarla con el servidor una vez se restablezca la conectividad.

> **Source:** En caso de no contar con conexión a red, la aplicación debe almacenar esta información localmente y sincronizarla con el servidor una vez se restablezca la conectividad.

## Flujo de aplicación/ Servicio de recogidas - Funcionalidad de Edición de Servicio  (5)

### ✓ REQ-231 · functional/must p.2
**Statement:** La aplicación debe permitir la edición de las características del servicio de recolección únicamente si el servicio aún no ha sido recolectado.

> **Source:** La aplicación debe permitir la edición de las características del servicio de recolección únicamente si el servicio aún no ha sido recolectado.

### ✓ REQ-232 · functional/must p.2
**Statement:** Las características editables incluyen la cantidad de unidades.

> **Source:** Las características editables incluyen:Cantidad de unidades.

### ✓ REQ-233 · functional/must p.2
**Statement:** Las características editables incluyen las dimensiones de las unidades: alto, largo, ancho.

> **Source:** Las características editables incluyen:Cantidad de unidades.Dimensiones de las unidades: alto, largo, ancho.

### ✓ REQ-234 · functional/must p.2 · NOISE
**Statement:** Las características editables incluyen el peso estimado (si aplica).

> **Source:** Las características editables incluyen:Cantidad de unidades.Dimensiones de las unidades: alto, largo, ancho.Peso estimado (si aplica).

### ✓ REQ-235 · functional/must p.2
**Statement:** La edición debe estar disponible desde la vista detallada del servicio, con validación previa del estado operativo del mismo.

> **Source:** La edición debe estar disponible desde la vista detallada del servicio, con validación previa del estado operativo del mismo.

## Flujo de aplicación/ Servicio de recogidas - Generación del Servicio con Información del Comprobante Digital  (5)

### ✓ REQ-215 · functional/must p.2
**Statement:** La aplicación debe permitir la creación de un servicio de recogida que incluya automáticamente la generación del comprobante digital (POC).

> **Source:** La aplicación debe permitir la creación de un servicio de recogida que incluya automáticamente la generación del comprobante digital (POC).

### ✓ REQ-216 · data/must p.2
**Statement:** El POC debe contener el número de servicio o guía.

> **Source:** El POC debe contener los siguientes datos mínimos:Número de servicio o guía.

### ✓ REQ-217 · data/must p.2 · NOISE
**Statement:** El POC debe contener la fecha y hora de la recogida.

> **Source:** El POC debe contener los siguientes datos mínimos:Número de servicio o guía.Fecha y hora de la recogida.

### ✓ REQ-218 · functional/must p.2
**Statement:** El POC debe contener como datos mínimos los datos del remitente y del punto de origen.

> **Source:** El POC debe contener los siguientes datos mínimos:Número de servicio o guía.Fecha y hora de la recogida.Datos del remitente y del punto de origen.

### ✓ REQ-219 · functional/must p.2
**Statement:** El POC debe contener firma digital.

> **Source:** Firma digital, fotografías, observaciones y validaciones de seguridad (como código de verificación).

## Flujo de aplicación/ Servicio de recogidas - Identificación del Punto de Parada  (1)

### ✓ REQ-212 · functional/must p.2
**Statement:** La aplicación debe indicar si la certificación se realizó en línea (punto de parada programado) o fuera de línea (punto no programado).

> **Source:** La aplicación debe indicar si la certificación se realizó en línea (punto de parada programado) o fuera de línea (punto no programado).

## Flujo de aplicación/ Servicio de recogidas - Integración del POC con Sistemas de Información  (4)

### ✓ REQ-224 · data/must p.2
**Statement:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo consulta desde el módulo de trazabilidad de servicios.

> **Source:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo:Consulta desde el módulo de trazabilidad de servicios.

### ✓ REQ-225 · data/must p.2
**Statement:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo visualización desde el portal web para clientes (remitente y destinatario).

> **Source:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo:Visualización desde el portal web para clientes (remitente y destinatario).

### ✓ REQ-226 · data/must p.2
**Statement:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo asociación con el expediente digital del servicio.

> **Source:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo:Asociación con el expediente digital del servicio.

### ✓ REQ-227 · data/must p.2
**Statement:** La integración debe contemplar interoperabilidad con sistemas de terceros si aplica (por ejemplo, plataformas regulatorias o de cumplimiento).

> **Source:** La integración debe contemplar interoperabilidad con sistemas de terceros si aplica (por ejemplo, plataformas regulatorias o de cumplimiento).

## Flujo de aplicación/ Servicio de recogidas - Notificación del POC al Cliente Remitente  (4)

### ✓ REQ-220 · functional/must p.2
**Statement:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente por correo electrónico con enlace al comprobante digital.

> **Source:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de:Correo electrónico con enlace al comprobante digital.

### ✓ REQ-221 · functional/must p.2
**Statement:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de correo electrónico con enlace al comprobante digital y por mensaje de texto (SMS) con resumen y enlace de consulta.

> **Source:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de:Correo electrónico con enlace al comprobante digital.Mensaje de texto (SMS) con resumen y enlace de consulta.

### ✓ REQ-222 · functional/must p.2 · NOISE
**Statement:** El contenido del mensaje debe ser configurable.

> **Source:** El contenido del mensaje debe ser configurable y debe incluir el logo institucional actualizado, según lineamientos regulatorios (por ejemplo, Supertransporte).

### ✓ REQ-223 · functional/must p.2
**Statement:** El enlace debe dirigir al portal web o repositorio donde el cliente pueda visualizar y descargar el comprobante, con validación de acceso mediante número de documento o código de seguridad.

> **Source:** El enlace debe dirigir al portal web o repositorio donde el cliente pueda visualizar y descargar el comprobante, con validación de acceso mediante número de documento o código de seguridad.

## Flujo de aplicación/ Servicio de recogidas - Pantalla inicio de ejecución de recolección  (1)

### ✓ REQ-546 · functional/must p.2
**Statement:** La aplicación debe incluir una opción de ubicación en mapa en la pantalla de inicio del proceso de recolección.

> **Source:** Se cuente con una pantalla de inicio proceso de recolección donde tenga las opciones de realizar recogida, no realizar, información detalle del servicio, unidades, opción ubicación mapa.

## Flujo de aplicación/ Servicio de recogidas - Pantalla inicio de ejecución de recolección - Validaciones de servicio  (1)

### ✓ REQ-547 · functional/must p.2
**Statement:** La aplicación debe realizar validaciones antes de ejecutar un servicio tales como verificar si tiene conceptos de recaudo de dinero asociados al servicio.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como: si tiene conceptos de recaudo de dinero asociados al servicio.

## Flujo de aplicación/ Servicio de recogidas - Trazabilidad y Consulta  (2)

### ✓ REQ-213 · functional/must p.2
**Statement:** Toda la información capturada debe estar disponible para consulta en el módulo de trazabilidad de servicios desde los sistemas fuente.

> **Source:** Toda la información capturada debe estar disponible para consulta en el módulo de trazabilidad de servicios desde los sistemas fuente.

### ✓ REQ-214 · functional/must p.2
**Statement:** La información debe poder exportarse como parte del comprobante digital de recogida, incluyendo firma, fotos, geolocalización y observaciones.

> **Source:** Debe poder exportarse como parte del comprobante digital de recogida, incluyendo firma, fotos, geolocalización y observaciones.

## Flujo de aplicación/ Servicio de recogidas - Validación de Estado del Servicio  (2)

### ✓ REQ-236 · functional/must p.2
**Statement:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio no haya sido marcado como recolectado.

> **Source:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio:No haya sido marcado como recolectado.

### ✓ REQ-237 · functional/must p.2
**Statement:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio no esté en estado de cierre de ruta o liquidación.

> **Source:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio:No haya sido marcado como recolectado.No esté en estado de cierre de ruta o liquidación.

## Flujo de aplicación/ Servicio de recogidas - Validación de Reglas Operativas  (3)

### ✓ REQ-193 · functional/must p.2
**Statement:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar que el intento de recolección se haya realizado dentro de la franja horaria asignada al servicio.

> **Source:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar:Que el intento de recolección se haya realizado dentro de la franja horaria asignada al servicio.

### ✓ REQ-194 · functional/must p.2
**Statement:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar que la ubicación del dispositivo móvil coincida con las coordenadas geográficas del punto de recolección.

> **Source:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar:Que la ubicación del dispositivo móvil coincida con las coordenadas geográficas del punto de recolección.

### ✓ REQ-195 · functional/must p.2
**Statement:** Si alguna de las condiciones no se cumple, debe mostrarse un mensaje de advertencia y bloquear el registro hasta que se corrija la condición.

> **Source:** Si alguna de estas condiciones no se cumple, debe mostrarse un mensaje de advertencia y bloquear el registro hasta que se corrija la condición.

## Flujo de aplicación/ Servicio entregas  (23)

### ✓ REQ-089 · functional/must p.2
**Statement:** Es necesario que al ejecutar la opción lo lleve al planteamiento de novedad (Motivo de no entrega).

> **Source:** Es necesario que al ejecutar la opción lo lleve al  planteamiento de novedad (Motivo de no entrega).

### ✓ REQ-090 · functional/must p.2
**Statement:** Opción que al ejecutar lo lleve a observar información detalle del servicio relacionando la siguiente información: Nombre del remitente, ID remitente, Dirección remitente, Nombre destinatario, Id de destinatario, Ciudad destino, Ciudad origen, Cantidad de unidades, Forma de pago, Subproducto servicio, Telefono remitente, Telefono destinatario, Ultima novedad, Solución novedad, Observación novedad, Unidad de negocio, Licencia.

> **Source:** Opción que al ejecutar lo lleve a observar información detalle del servicio relacionando la siguiente información:1.Nombe del remitente2.ID remitente 3.Dirección remitente4-Nombre destinatario5.Id de destinatario6.Ciudad destino7.Ciudad origen8.Cantidad de unidades 9.Forma de pago 10, Subproducto servicio 11.Telefono remitente12, Telefono destinatario13, Ultima novedad14, Solución novedad15.Observación novedad16, Unidad de negocio17, Licencia.

### ✓ REQ-092 · functional/must p.2
**Statement:** Al ejecutar la opción de realizar entrega espero que lo lleve a la opción de lectura de unidades a entregar.

> **Source:** Al ejecutar la opción de realizar entrega espero que lo lleve a la opción de lectura de unidades a entregar. Donde visualice el listado de unidades asociadas a la remesa relacionando la siguiente información por unidad:1, Código IUP de la unidad 2.UEN 3.Peso y volumen.

### ✓ REQ-093 · functional/must p.2
**Statement:** Donde visualice el listado de unidades asociadas a la remesa relacionando la siguiente información por unidad: Código IUP de la unidad, UEN, Peso y volumen.

> **Source:** Donde visualice el listado de unidades asociadas a la remesa relacionando la siguiente información por unidad:1, Código IUP de la unidad 2.UEN 3.Peso y volumen.

### ✓ REQ-094 · functional/must p.2
**Statement:** Se cuente con una pantalla de inicio proceso de entrega que incluya la opción de realizar entrega.

> **Source:** Se cuente con una pantalla de inicio proceso de entrega donde tenga las opciones de realizar entrega, no realizar, información detalle del servicio, unidades, opción ubicación mapa.

### ✓ REQ-095 · functional/must p.2
**Statement:** Es necesario que realice validaciones antes de ejecutar un servicio tales como: si tiene conceptos de recaudo de dinero asociados al servicio.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  si tiene conceptos de recaudo de dinero asociados al servicio.

### ✓ REQ-096 · functional/must p.2
**Statement:** Es necesario que realice validaciones antes de ejecutar un servicio tales como: Si el servicio tiene una novedad activa.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  Si el servicio tiene una novedad activa.

### ✓ REQ-097 · functional/must p.2
**Statement:** Opción de recaudo de dinero, que evidencie los detalles del cobro es decir los conceptos de liquidación del servicio.

> **Source:** Opción de recaudo de dinero, que evidencie los detalles del cobro es decir los conceptos de liquidación del servicio.

### ✓ REQ-098 · functional/must p.2
**Statement:** Que la opción permita el recaudo de dinero mediante pagos en Efectivo.

> **Source:** Que la opción permita el recaudo de dinero pagos en  Efectivo o  Integración con pasarelas de pago para procesar pagos con tarjetas..

### ✓ REQ-099 · functional/must p.2
**Statement:** La aplicación registre cada transacción relacionando información de pago.

> **Source:** La aplicación registre cada transacción relacionando información de pago e integre con el collection management.

### ✓ REQ-100 · functional/must p.2
**Statement:** Implementar un sistema de generación de alertas en tiempo real que permita confirmar pagos recibidos.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:1) Confirmar pagos recibidos y detectar pagos pendientes..

### ✓ REQ-104 · functional/must p.2
**Statement:** Implementar un sistema de generación de alertas en tiempo real que permita: Recibir automáticamente la información del recaudo.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:2) Recibir automáticamente la información del recaudo..

### ✓ REQ-108 · functional/must p.2
**Statement:** La aplicación debe validar el estado del servicio para novedad (Motivo de no entrega) activa antes de ejecutar la lectura de unidades.

> **Source:** La aplicación tenga la opción de realizar validaciones de reglas, en este caso que realice validación del estado del servicio si tiene novedad (Motivo de no entrega) activa antes de ejecutar la lectura de unidades, y que asuma acciones en caso de estar o no activa..

### ✓ REQ-109 · functional/should p.2
**Statement:** La aplicación tenga la opción de realizar validaciones, para este caso realice la validación del servicio si tiene conceptos de recaudo de dinero.

> **Source:** La aplicación tenga la opción de realizar validaciones, para este caso realice la validación del servicio si tiene conceptos de recaudo de dinero.

### ✓ REQ-110 · functional/must p.2
**Statement:** Al ejecutar la lectura de la unidad se actualice el contador de unidades a leer.

> **Source:** Al ejecutar la lectura de la unidad se actualice el contador de unidades a leer.

### ✓ REQ-111 · functional/must p.2
**Statement:** Al ejecutar la captura de la unidad la aplicación debe capturar usuario, hora, latitud, longitud, vehículo, capturar si leyó o no unidades, si leyó la totalidad de las mismas.

> **Source:** Al ejecutar la captura de la unidad la aplicación debe capturar usuario, hora, latitud, longitud, vehículo, capturar si leyó o no unidades, si leyó la totalidad de las mismas.

### ✓ REQ-112 · functional/must p.2
**Statement:** Es necesario que la lectura de unidades se pueda realizar por Código de lectura (códigos de barras o QR) o utilizando la cámara del dispositivo.

> **Source:** Es necesario que la lectura de unidades se pueda realizar por Código de lectura o utilizando la cámara del dispositivo. Los códigos de lectura pueden ser de barras o QR.

### ✓ REQ-113 · functional/must p.2 · NOISE
**Statement:** Los códigos de lectura pueden ser de barras o QR.

> **Source:** Los códigos de lectura pueden ser de barras o QR.

### ✓ REQ-114 · functional/must p.2
**Statement:** Opción que tenga los campos de captura de información y características relacionado a lo siguiente: Nombre de quien recibe, Documento de identidad, Numero de contacto (Celular), Correo electrónico.

> **Source:** Opción que tenga los campos de captura de información y características relacionado a lo siguiente:1) Nombre de quien recibe2)Documento de identidad3)Numero de contacto (Celular)4)Correo electrónico

### ✓ REQ-528 · functional/must p.2
**Statement:** La aplicación debe permitir la lectura de unidades utilizando la cámara del dispositivo móvil, admitiendo códigos de lectura de barras o QR.

> **Source:** Es necesario que la lectura de unidades se pueda realizar por Código de lectura o utilizando la cámara del dispositivo. Los códigos de lectura pueden ser de barras o QR.

### ✓ REQ-529 · functional/must p.2
**Statement:** La aplicación debe permitir el recaudo de dinero mediante pago en efectivo o integración con pasarelas de pago para procesar pagos con tarjeta.

> **Source:** Que la opción permita el recaudo de dinero pagos en Efectivo o Integración con pasarelas de pago para procesar pagos con tarjetas.

### ✓ REQ-532 · functional/must p.2
**Statement:** La aplicación debe registrar cada transacción relacionando información de pago e integrarse con el collection management.

> **Source:** La aplicación registre cada transacción relacionando información de pago e integre con el collection management.

### ⚠ REQ-536 · constraint/must p.2
**Statement:** El dispositivo móvil debe contar con cámara funcional para la lectura de códigos de barras o QR.

> **Source:** utilizando la cámara del dispositivo. Los códigos de lectura pueden ser de barras o QR... la aplicación debe capturar usuario, hora, latitud, longitud

## Flujo de aplicación/ Servicio entregas - Certificación de Servicios Agrupados por Destinatario en Parada Única  (1)

### ✓ REQ-549 · functional/must p.2
**Statement:** El sistema debe validar que todos los servicios agrupados hayan sido correctamente certificados antes de permitir el cierre de la parada.

> **Source:** El sistema debe validar que todos los servicios agrupados hayan sido correctamente certificados antes de permitir el cierre de la parada.

## Flujo de aplicación/ Servicio entregas - Ejecución cumplimiento de entregas- Certificación de la entrega a destinatario  (5)

### ✓ REQ-129 · functional/must p.2
**Statement:** La aplicación debe tener acceso a la cámara del dispositivo móvil para la captura y almacenamiento de fotografías de evidencia, con capacidad de capturar máx. 10 fotografías.

> **Source:** La pantalla debe de tener la opción de captura y almacenamiento de fotografía, la aplicación debe tener la capacidad de capturar máx. 10 fotografías.

### ✓ REQ-538 · functional/must p.2
**Statement:** La aplicación debe capturar latitud y longitud durante la certificación de entregas.

> **Source:** Que la aplicación capture relacionado a la certificación, el usuario, fecha, hora, latitud, longitud y vehículo.

### ✓ REQ-539 · functional/must p.2
**Statement:** La aplicación debe tener la opción de pick to voice para capturar información en el campo observaciones.

> **Source:** La aplicación tenga la opción de pick to voice para capturar información en el campo observaciones.

### ✓ REQ-544 · functional/must p.2
**Statement:** La aplicación debe capturar relacionado a la certificación el usuario, fecha, hora, latitud, longitud y vehículo.

> **Source:** Que la aplicación capture relacionado a la certificación, el usuario, fecha, hora, latitud, longitud y vehículo.

### ✓ REQ-545 · functional/must p.2
**Statement:** La aplicación debe tener la opción de captura y almacenamiento de firma digital.

> **Source:** La pantalla debe de tener la opción de captura y almacenamiento de firma digital.

## Flujo de aplicación/ Servicio entregas - Ejecución cumplimiento de entregas- Envío de prueba digital (POD)  (1)

### ✓ REQ-542 · functional/must p.2
**Statement:** La aplicación debe generar el servicio con los datos capturados en la certificación para la estructuración del POD (Comprobante de prueba digital).

> **Source:** La aplicación genere el servicio con los datos capturados en la certificación, para la estructuración del POD (Comprobante de prueba digital).

## Flujo de aplicación/ Servicio entregas - Ejecución cumplimiento de entregas- Finalización de certificación con éxito  (2)

### ✓ REQ-540 · functional/must p.2
**Statement:** La aplicación debe emitir una notificación automática al destinatario al seleccionar próximo servicio desde el cierre en la certificación del servicio.

> **Source:** La aplicación debe emitir una notificación automática al destinatario, relacionado al seleccionar próximo servicio desde el cierre en la certificación del servicio.

### ✓ REQ-543 · functional/must p.2
**Statement:** Al ejecutar la opción navegar próxima parada, la aplicación debe llevarlo a ejecutar el próximo servicio que sugiere la planeación.

> **Source:** Al ejecutar la opción navegar próxima parada, la aplicación debe llevarlo a ejecutar el próximo servicio que sugiere la planeación.

## Flujo de aplicación/ Servicio entregas - Ejecución cumplimiento de entregas- Registro de motivos de no entrega (Novedad)  (1)

### ✓ REQ-541 · functional/must p.2
**Statement:** La aplicación debe realizar validaciones para dejar plantear motivos de no entrega, tal como validación de ubicación (cercanía a parada).

> **Source:** La aplicación debe realizar validaciónes para dejar plantear motivos de no entrega, tal como validación de ubicación (cercania a parada).

## Flujo de aplicación/ Servicio entregas - Ejecución cumplimiento de entregas- Registro de motivos de no entrega a nivel de IUP (Novedad)  (1)

### ✓ REQ-548 · functional/must p.2
**Statement:** La aplicación debe permitir el registro de novedades a nivel de IUP (Unidad).

> **Source:** La aplicación permita el registro de novedades a nivel de IUP (Unidad).

## Flujo de aplicación/Servicio de recogidas - Captura de Datos de Geolocalización y Temporalidad  (1)

### ✓ REQ-566 · functional/must p.2
**Statement:** El sistema debe registrar automáticamente la fecha y hora exacta del evento al momento de la certificación.

> **Source:** El sistema debe registrar automáticamente los siguientes datos al momento de la certificación:Latitud y longitud del dispositivo.Fecha y hora exacta del evento.

## Flujo de aplicación/Servicio de recogidas - Captura de Evidencia Fotográfica  (1)

### ✓ REQ-572 · data/must p.2
**Statement:** El sistema debe almacenar las imágenes junto con el motivo y las coordenadas, y mantenerlas disponibles para consulta posterior.

> **Source:** Las imágenes deben almacenarse junto con el motivo y las coordenadas, y estar disponibles para consulta posterior.

## Flujo de aplicación/Servicio de recogidas - Cumplimiento y Certificación de Recogida  (2)

### ✓ REQ-554 · functional/must p.2
**Statement:** En caso de no contar con conexión de red, la aplicación debe almacenar la información localmente y sincronizarla con el servidor una vez se restablezca la conectividad.

> **Source:** En caso de no contar con conexión a red, la aplicación debe almacenar esta información localmente y sincronizarla con el servidor una vez se restablezca la conectividad.

### ✓ REQ-565 · functional/must p.2
**Statement:** El sistema debe capturar la firma digital del remitente o responsable de entrega.

> **Source:** 2)Firma digital del remitente o responsable de entrega.

## Flujo de aplicación/Servicio de recogidas - Descripción de validaciones y reglas de campos obligatorios  (1)

### ✓ REQ-567 · functional/must p.2
**Statement:** La aplicación debe permitir parametrizar campos de planteamiento de obligación tales como observación, fotografía y cantidad de caracteres.

> **Source:** La aplicación debe permitir parametrizar campos de planteamiento de obligación tal como observacion, fotografia y cantidad de caracteres.

## Flujo de aplicación/Servicio de recogidas - Integración del POC con Sistemas de Información  (1)

### ✓ REQ-573 · data/must p.2
**Statement:** El sistema debe contemplar interoperabilidad con sistemas de terceros si aplica (por ejemplo, plataformas regulatorias o de cumplimiento).

> **Source:** La integración debe contemplar interoperabilidad con sistemas de terceros si aplica (por ejemplo, plataformas regulatorias o de cumplimiento).

## Flujo de aplicación/Servicio de recogidas - Notificación del POC al Cliente Remitente  (3)

### ✓ REQ-568 · functional/must p.2
**Statement:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente por correo electrónico con enlace al comprobante digital y por mensaje de texto (SMS) con resumen y enlace de consulta.

> **Source:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de:Correo electrónico con enlace al comprobante digital.Mensaje de texto (SMS) con resumen y enlace de consulta.

### ✓ REQ-569 · functional/must p.2
**Statement:** El sistema debe contar con un portal web o repositorio donde el cliente pueda visualizar y descargar el comprobante digital (POC), con validación de acceso mediante número de documento o código de seguridad.

> **Source:** El enlace debe dirigir al portal web o repositorio donde el cliente pueda visualizar y descargar el comprobante, con validación de acceso mediante número de documento o código de seguridad.

### ✓ REQ-571 · security/must p.2
**Statement:** El sistema debe contar con un mecanismo de autenticación de clientes para validar acceso al comprobante digital mediante número de documento o código de seguridad.

> **Source:** con validación de acceso mediante número de documento o código de seguridad

## Flujo de aplicación/Servicio de recogidas - Pantalla inicio de ejecución de recolección  (1)

### ✓ REQ-151 · functional/must p.2
**Statement:** Se cuente con una pantalla de inicio proceso de recolección donde tenga las opciones de realizar recogida, no realizar, información detalle del servicio, unidades, opción ubicación mapa.

> **Source:** Se cuente con una pantalla de inicio proceso de recolección donde tenga las opciones de realizar recogida, no realizar, información detalle del servicio, unidades, opción ubicación mapa.

## Flujo de aplicación/Servicio de recogidas - Pantalla inicio de ejecución de recolección - Opción de no realizar entrega  (1)

### ✓ REQ-152 · functional/must p.2
**Statement:** Es necesario que al ejecutar la opción de no realizar recolección lo lleve al planteamiento de novedad (Motivo de no recolección).

> **Source:** Es necesario que al ejecutar la opción lo lleve al  planteamiento de novedad (Motivo de no recolección).

## Flujo de aplicación/Servicio de recogidas - Pantalla inicio de ejecución de recolección - Opción información detalle  (1)

### ✓ REQ-153 · functional/must p.2
**Statement:** Opción que al ejecutar lo lleve a observar información detalle del servicio relacionando la siguiente información: 1.Nombre del remitente, 2.ID remitente, 3.Dirección remitente, 4.Nombre solicitante, 5.Id de solicitante, 6.Ciudad solicitante, 7.Ventana horaria, 8.Cantidad de unidades, 9.Forma de pago, 10.Subproducto servicio, 11.Telefono remitente, 12.Telefono solicitante, 13.Unidad de negocio, 14.Licencia, 15.Cuenta, 16.Valor de la mercancía, 17.Servicios asociados a la recolección.

> **Source:** Opción que al ejecutar lo lleve a observar información detalle del servicio relacionando la siguiente información:1.Nombe del remitente2.ID remitente 3.Dirección remitente4-Nombre solicitante5.Id de solicitante6.Ciudad solicitante7.Ventana horaria8.Cantidad de unidades 9.Forma de pago 10, Subproducto servicio 11.Telefono remitente12, Telefono solicitante13, Unidad de negocio14, Licencia15, Cuenta16, Valor de la mercancía 17.Servicios asociados a la recolección.

## Flujo de aplicación/Servicio de recogidas - Pantalla inicio de ejecución de recolección - Opción información detalle unidades relacionadas a la recolección  (1)

### ✓ REQ-091 · functional/must p.2
**Statement:** Al ejecutar la opción de ver las unidades asociadas a la recolección, se evidencie por unidad: 1.Codigo IUP de la unidad, 2.Tipo de unidad, 3.Clase de empaque, 4.Peso, 5.Volumen, 6.Notas.

> **Source:** Necesitamos que, al ejecutar la opción de ver las unidades asociadas a la recolección, se evidencie la siguiente información por unidad:1.Codigo IUP de la unidad 2.Tipo de unidad3.Clase de empaque4.Peso5.Volumen6.Notas.

## Flujo de aplicación/Servicio de recogidas - Validación de Estado del Servicio  (1)

### ✓ REQ-570 · functional/must p.2
**Statement:** El sistema debe validar, antes de permitir cualquier modificación del servicio, que este no haya sido marcado como recolectado ni esté en estado de cierre de ruta o liquidación.

> **Source:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio:No haya sido marcado como recolectado.No esté en estado de cierre de ruta o liquidación.

## Flujo de aplicación/Servicio de recogidas - Validación de Reglas Operativas  (1)

### ✓ REQ-564 · functional/must p.2
**Statement:** La aplicación debe validar que el intento de recolección se haya realizado dentro de la franja horaria asignada al servicio antes de permitir el registro de un motivo de no recolección.

> **Source:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar:Que el intento de recolección se haya realizado dentro de la franja horaria asignada al servicio.

## Flujo de aplicación/Servicio entregas - Certificación de Servicios Agrupados por Destinatario en Parada Única - Agrupación automática de servicios  (1)

### ✓ REQ-146 · functional/must p.2
**Statement:** Al sincronizar los servicios del día, el sistema debe identificar aquellos que comparten el mismo destinatario y punto de entrega, agrupándolos en una única parada.

> **Source:** Al sincronizar los servicios del día, el sistema debe identificar aquellos que comparten el mismo destinatario y punto de entrega, agrupándolos en una única parada.

## Flujo de aplicación/Servicio entregas - Certificación de Servicios Agrupados por Destinatario en Parada Única - Interfaz unificada de certificación  (3)

### ✓ REQ-147 · functional/must p.2
**Statement:** La aplicación debe presentar una vista consolidada que permita al auxiliar certificar todos los servicios agrupados mediante captura de firma del destinatario.

> **Source:** La aplicación debe presentar una vista consolidada que permita al auxiliar certificar todos los servicios agrupados mediante:Captura de firma del destinatario.

### ✓ REQ-148 · functional/must p.2
**Statement:** La aplicación debe presentar una vista consolidada que permita al auxiliar certificar todos los servicios agrupados mediante registro de observaciones generales o específicas por servicio.

> **Source:** Registro de observaciones generales o específicas por servicio.

### ✓ REQ-149 · functional/must p.2
**Statement:** La aplicación debe presentar una vista consolidada que permita al auxiliar certificar todos los servicios agrupados mediante toma de evidencia fotográfica única o por servicio, según configuración.

> **Source:** Toma de evidencia fotográfica única o por servicio, según configuración.

## Flujo de aplicación/Servicio entregas - Certificación de Servicios Agrupados por Destinatario en Parada Única - Validación de certificación  (1)

### ✓ REQ-150 · functional/must p.2
**Statement:** El sistema debe validar que todos los servicios agrupados hayan sido correctamente certificados antes de permitir el cierre de la parada.

> **Source:** El sistema debe validar que todos los servicios agrupados hayan sido correctamente certificados antes de permitir el cierre de la parada.

## Flujo de aplicación/Servicio entregas - Ejecucción cumplimiento de entregas - Registro de motivos de no entrega a nivel de IUP (Novedad)  (1)

### ✓ REQ-145 · functional/must p.2
**Statement:** La aplicación permita el registro de novedades a nivel de IUP (Unidad).

> **Source:** La aplicación permita el registro de novedades a nivel de IUP  (Unidad).

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Certificación de la entrega a destinatario  (6)

### ✓ REQ-115 · functional/must p.2
**Statement:** La pantalla debe de tener la opción de captura y almacenamiento de fotografía.

> **Source:** La pantalla debe de tener la opción de captura y almacenamiento de fotografía

### ✓ REQ-116 · functional/must p.2
**Statement:** La aplicación debe tener la capacidad de capturar máx. 10 fotografías.

> **Source:** la aplicación debe tener la capacidad de capturar máx. 10 fotografías.

### ✓ REQ-117 · functional/must p.2
**Statement:** La pantalla debe de tener la opción de captura y almacenamiento de firma digital.

> **Source:** La pantalla debe de tener la opción de captura y almacenamiento de firma digital.

### ✓ REQ-118 · functional/must p.2
**Statement:** Opción que permita seleccionar de acuerdo con el tipo de destinario, relacionando las siguientes opciones: 1.Unidad residencial 2.Domicilio 3.Empresa 4.Tercero autorizado.

> **Source:** Opción que permita seleccionar de acuerdo con el tipo de destinario, relacionando las siguientes opciones:1.Unidad residencial 2.Domicilio3.Empresa4.Tercero autorizado.

### ✓ REQ-119 · functional/must p.2
**Statement:** Que la aplicación capture relacionado a la certificación, el usuario, fecha, hora, latitud, longitud y vehículo.

> **Source:** Que la aplicación capture relacionado a la certificación, el usuario, fecha, hora, latitud, longitud y vehículo.

### ✓ REQ-120 · functional/must p.2
**Statement:** La aplicación tenga la opción de pick to voice para capturar información en el campo observaciones.

> **Source:** La aplicación tenga la opción de pick to voice para capturar información en el campo observaciones.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Envión de prueba digital (POD)  (2)

### ✓ REQ-124 · functional/must p.2
**Statement:** La aplicación genere el servicio con los datos capturados en la certificación, para la estructuración del POD (Comprobante de prueba digital).

> **Source:** La aplicación genere el servicio con los datos capturados en la certificación,  para la estructuración del POD (Comprobante de prueba digital).

### ✓ REQ-125 · functional/must p.2
**Statement:** La aplicación genere el evento de notificación para el cliente destinatario con el POD.

> **Source:** La aplicación genere el evento de notificación para el cliente destinatario con el POD.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Finalización de certificación con éxito  (3)

### ✓ REQ-121 · functional/must p.2
**Statement:** La aplicación debe evidenciar la culminación exitosa de la certificación.

> **Source:** Opción donde se evidencie la culminación exitosa, y evidencie las opciones de navegar a la próxima parada, o ver servicios del día.

### ✓ REQ-122 · functional/must p.2
**Statement:** Se debe evidenciar la culminación exitosa, y evidenciar las opciones de navegar a la próxima parada, o ver servicios del día.

> **Source:** Opción donde se evidencie la culminación exitosa, y evidencie las opciones de navegar a la próxima parada, o ver servicios del día.

### ✓ REQ-123 · functional/must p.2
**Statement:** Al ejecutar la opción navegar próxima parada, la aplicación debe llevarlo a ejecutar el próximo servicio que sugiere la planeación.

> **Source:** Al ejecutar la opción navegar próxima parada, la aplicación debe llevarlo a ejecutar el próximo servicio que sugiere la planeación.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Registro de motivos de no entrega (Novedad)  (2)

### ✓ REQ-127 · functional/must p.2
**Statement:** La aplicación debe permitir al usuario seleccionar un motivo de no entrega desde una lista predefinida (por ejemplo: destinatario ausente, dirección incorrecta, mercancía averiada, rechazo del cliente, entre otros).

> **Source:** La aplicación debe permitir al usuario seleccionar un motivo de no entrega desde una lista predefinida (por ejemplo: destinatario ausente, dirección incorrecta, mercancía averiada, rechazo del cliente, entre otros).

### ✓ REQ-128 · functional/must p.2
**Statement:** La aplicación debe permitir al usuario registrar por pick to voice la descripción del evento.

> **Source:** La aplicación debe permitir al usuario registrar por pick to voice la descripción del evento, registrarlo e integrarlo con servicio de planteamiento.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Registro de motivos de no entrega (Novedad) - Asociación con el Servicio  (2)

### ✓ REQ-136 · functional/must p.2
**Statement:** Cada motivo de no entrega, fotografía y observación debe estar vinculado al número de guía o servicio correspondiente.

> **Source:** Cada motivo de no entrega, fotografía y observación debe estar vinculado al número de guía o servicio correspondiente.

### ✓ REQ-137 · functional/must p.2
**Statement:** Debe existir trazabilidad completa de cada intento de entrega fallido.

> **Source:** Debe existir trazabilidad completa de cada intento de entrega fallido.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Registro de motivos de no entrega (Novedad) - Captura de Evidencia Fotográfica  (2)

### ✓ REQ-130 · functional/must p.2
**Statement:** La aplicación debe tener la capacidad de validar que la fotografía tomada sea tomada de manera correcta y coherente con el motivo de no entrega planteado.

> **Source:** La aplicación debe tener la capacidad de validar que la fotografía tomada sea tomada de manera correcta y coherente con el motivo de no entrega planteado

### ✓ REQ-133 · functional/must p.2
**Statement:** Las imágenes deben almacenarse junto con la información del servicio y estar disponibles para consulta posterior.

> **Source:** Las imágenes deben almacenarse junto con la información del servicio y estar disponibles para consulta posterior.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Registro de motivos de no entrega (Novedad) - Descripción de validaciones y reglas  (2)

### ✓ REQ-142 · functional/must p.2
**Statement:** La aplicación debe realizar validaciónes para dejar plantear motivos de no entrega, tal como validación de ubicación (cercania a parada).

> **Source:** La aplicación debe realizar validaciónes para dejar plantear motivos de no entrega, tal como validación de ubicación (cercania a parada).

### ✓ REQ-143 · functional/must p.2
**Statement:** La aplicación debe realizar validaciones para dejar plantear motivos de no entrega, tal como validación de franja horaria.

> **Source:** La aplicación debe realizar validaciones para dejar plantear motivos de no entrega, tal como validación de franja horaria.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Registro de motivos de no entrega (Novedad) - Descripción de validaciones y reglas de campos obligatorios  (1)

### ✓ REQ-144 · functional/must p.2
**Statement:** La aplicación debe permitir parametrizar el campo de observación del planteamiento de obligación, incluyendo si requiere fotografía y la cantidad máxima de caracteres.

> **Source:** La aplicación debe permitir parametrizar campos de planteamiento de obligación tal como observación, fotografía y cantidad de caracteres.

## Flujo de aplicación/Servicio entregas - Ejecución cumplimiento de entregas - Registro de motivos de no entrega (Novedad) - Ingreso de Observaciones  (2)

### ✓ REQ-134 · functional/must p.2
**Statement:** El usuario debe poder ingresar observaciones adicionales en texto libre para complementar la información del motivo de no entrega.

> **Source:** El usuario debe poder ingresar observaciones adicionales en texto libre para complementar la información del motivo de no entrega.

### ✓ REQ-135 · functional/must p.2
**Statement:** Las observaciones deben incluir campos como: nombre de la persona que informa, hora del intento de entrega, y cualquier detalle relevante.

> **Source:** Las observaciones deben incluir campos como: nombre de la persona que informa, hora del intento de entrega, y cualquier detalle relevante.

## Flujo de aplicación/Servicio entregas - Funcionalidad de diligenciamiento de documentos digitales  (1)

### ✓ REQ-126 · functional/must p.2
**Statement:** Permita capturar documentos digitales, tales como Boomerang y dardos.

> **Source:** Permita capturar, parametrizar y enviar documentos digitales. Tales como Boomerang y dardos.

## Funcionalidad / Login  (9)

### ✓ REQ-509 · security/must p.2 · NOISE
**Statement:** La aplicación debe verificar que el usuario exista y se encuentre activo en el directorio corporativo de TCC.

> **Source:** Verificar que el usuario exista y se encuentre activo en el directorio corporativo de TCC.

### ✓ REQ-510 · functional/must p.2 · NOISE
**Statement:** El sistema debe confirmar que el identificador del vehículo ingresado exista en el maestro de vehículos y esté en estado activo.

> **Source:** Confirmar que el identificador del vehículo ingresado exista en el maestro de vehículos y esté en estado activo.

### ✓ REQ-511 · functional/must p.2 · NOISE
**Statement:** El sistema debe validar que el centro de operación asignado al usuario coincida con el centro de operación del medio logístico al que intenta acceder.

> **Source:** Validar que el centro de operación asignado al usuario coincida con el centro de operación del medio logístico al que intenta acceder.

### ✓ REQ-512 · functional/must p.2
**Statement:** El sistema debe gestionar sesiones activas por usuario para impedir que un usuario esté autenticado simultáneamente en más de un medio logístico, garantizando control de concurrencia.

> **Source:** la aplicación realice la validación, de que actualmente no este logeado en otro medio logístico (vehículo). Para evitar simultaneidades en 2 órdenes de trabajo.

### ✓ REQ-513 · security/must p.2
**Statement:** El sistema debe registrar un log de auditoría que asocie cada usuario con el medio logístico al que se vinculó y con las órdenes de trabajo ejecutadas, para permitir trazabilidad de la tripulación.

> **Source:** es necesario capturar el registro de los usuarios que se logean y se relacionan a un medio logístico (Vehículo). Para que este en capacidad de mirar por trazabilidad que tripulación desarrollo la orden de trabajo.

### ✓ REQ-514 · security/must p.2
**Statement:** La aplicación debe enviar un código de verificación de un solo uso (OTP) al canal seleccionado y validarlo antes de permitir el restablecimiento de la contraseña.

> **Source:** Envío de un código de verificación de un solo uso (OTP) al canal seleccionado. Solicitud y validación del código OTP antes de permitir el restablecimiento de la contraseña.

### ✓ REQ-515 · security/must p.2 · NOISE
**Statement:** El sistema debe validar al usuario mediante un canal alternativo previamente registrado (correo electrónico corporativo o número de teléfono móvil).

> **Source:** Validación del usuario mediante un canal alternativo previamente registrado (correo electrónico corporativo o número de teléfono móvil).

### ✓ REQ-516 · usability/must p.2
**Statement:** El módulo de onboarding digital debe ser guiado, auditable y adaptable según el rol asignado.

> **Source:** Este proceso debe ser guiado, auditable y adaptable según el rol asignado.

### ✓ REQ-525 · data/must p.2
**Statement:** El modelo de datos de servicios debe soportar el cálculo del valor total a recaudar durante la ejecución de la ruta, por producto y forma de pago.

> **Source:** Valor total de dinero a recaudar durante la ejecución de la ruta, por producto y forma de pago.

## Funcionalidad / Servicios del día  (5)

### ✓ REQ-520 · functional/must p.2
**Statement:** Los mensajes deben quedar parametrizables desde el módulo de parámetros de notificaciones.

> **Source:** estos mensajes deben quedar parametrizables desde el módulo de parámetros de notificaciones.

### ✓ REQ-521 · functional/must p.2
**Statement:** La aplicación debe permitir enviar mensajes parametrizables a demanda por el usuario, al cliente destinatario o remitente por medio del celular relacionado al servicio.

> **Source:** Opción de enviar mensajes parametrizables a demanda por el usuario, al cliente destinatario o remitente por medio del celular relacionado al servicio.

### ✓ REQ-522 · functional/must p.2
**Statement:** El sistema debe disponer de capacidades de geocodificación para convertir las direcciones de entrega y despacho en coordenadas geográficas que permitan su representación en el mapa.

> **Source:** es necesaria la visualización de los servicios del día georreferenciados en el mapa, de acuerdo a la dirección de destino para las entregas, y de despacho para las recogidas.

### ✓ REQ-523 · functional/must p.2
**Statement:** La aplicación debe permitir, a partir de la dirección de cada parada, ejecutar la navegación en mapa accionando la funcionalidad de 'cómo llegar'.

> **Source:** opción que permita a partir desde la dirección de la parada, ejecutar la navegación en mapa accionando la funcionalidad de cómo llegar

### ✓ REQ-526 · functional/must p.2 · NOISE
**Statement:** La aplicación móvil debe permitir la ejecución directa de llamada al número de contacto del destinatario.

> **Source:** Ícono de mapa: acceso a la funcionalidad de navegación (cómo llegar) hacia la dirección del destinatario. Ícono de teléfono: ejecución directa de llamada al número de contacto del destinatario.

## Funcionalidad /Login - Autenticación de login de ingreso  (7)

### ✓ REQ-018 · security/must p.2
**Statement:** La aplicación debe permitir la implementación de un mecanismo de doble factor de autenticación (2FA) como medida de seguridad adicional en el proceso de recuperación de contraseña.

> **Source:** La aplicación debe permitir la implementación de un mecanismo de doble factor de autenticación (2FA) como medida de seguridad adicional en el proceso de recuperación de contraseña.

### ✓ REQ-019 · functional/must p.2
**Statement:** Esta funcionalidad debe activarse cuando un usuario indique que ha olvidado su contraseña

> **Source:** Esta funcionalidad debe activarse cuando un usuario indique que ha olvidado su contraseña, y debe contemplar al menos los siguientes elementos:

### ✓ REQ-020 · security/must p.2 · NOISE
**Statement:** Validación del usuario mediante un canal alternativo previamente registrado (correo electrónico corporativo o número de teléfono móvil).

> **Source:** Validación del usuario mediante un canal alternativo previamente registrado (correo electrónico corporativo o número de teléfono móvil).

### ✓ REQ-021 · functional/must p.2 · NOISE
**Statement:** Envío de un código de verificación de un solo uso (OTP) al canal seleccionado.

> **Source:** Envío de un código de verificación de un solo uso (OTP) al canal seleccionado.

### ✓ REQ-022 · security/must p.2
**Statement:** Solicitud y validación del código OTP antes de permitir el restablecimiento de la contraseña.

> **Source:** Solicitud y validación del código OTP antes de permitir el restablecimiento de la contraseña.

### ✓ REQ-023 · functional/must p.2
**Statement:** La aplicación deberá incluir un módulo de onboarding digital que permita registrar a nuevos usuarios logísticos (conductores, auxiliares, coordinadores) antes de habilitar su acceso operativo.

> **Source:** La aplicación deberá incluir un módulo de onboarding digital que permita registrar, validar y capacitar a nuevos usuarios logísticos (conductores, auxiliares, coordinadores) antes de habilitar su acceso operativo.

### ✓ REQ-027 · security/must p.2 · NOISE
**Statement:** Este proceso debe ser auditable.

> **Source:** Este proceso debe ser guiado, auditable y adaptable según el rol asignado.

## Funcionalidad /Login - Login de varios usuarios a un mismo ID de vehiculo  (3)

### ✓ REQ-001 · functional/must p.2 · NOISE
**Statement:** Se requiere habilitar la posibilidad de que múltiples usuarios puedan iniciar sesión y operar simultáneamente sobre un mismo medio logístico (Vehiculo de ultima milla), permitiendo la ejecución paralela de varios servicios (entregas y recogidas) desde dicho medio.

> **Source:** Se requiere habilitar la posibilidad de que múltiples usuarios puedan iniciar sesión y operar simultáneamente sobre un mismo medio logístico (Vehiculo de ultima milla), permitiendo la ejecución paralela de varios servicios (entregas y recogidas) desde dicho medio.

### ✓ REQ-005 · functional/must p.2 · NOISE
**Statement:** Cualquier miembro de la tripulación autorizado podrá iniciar sesión y ejecutar uno o varios servicios asignados a ese ID de móvil, sin restricciones por usuario.

> **Source:** En consecuencia, cualquier miembro de la tripulación autorizado podrá iniciar sesión y ejecutar uno o varios servicios asignados a ese ID de móvil, sin restricciones por usuario.

### ✓ REQ-006 · functional/must p.2 · NOISE
**Statement:** Al logearse 1 o n usuarios en la aplicación deben relacionar el ID del vehículo sobre el cual van a operar.

> **Source:** Un ejemplo de la necesidad, se realiza la planeación al vehículo 13478, entonces al logearse 1 o n usuarios en la aplicación deben relacionar el ID del vehículo sobre el cual van a ingresar, visualizar y ejecutar la orden de trabajo.

## Funcionalidad /Login - Validaciones de inicio de sesión  (8)

### ✓ REQ-007 · functional/must p.2
**Statement:** Se requiere que la aplicación implemente validaciones de inicio de sesión basadas en los parámetros ingresados por el usuario, con el fin de garantizar la integridad operativa y la correcta asociación entre usuarios, ciudad de operación y medios logísticos (vehículo).

> **Source:** Se requiere que la aplicación implemente validaciones de inicio de sesión basadas en los parámetros ingresados por el usuario, con el fin de garantizar la integridad operativa y la correcta asociación entre usuarios, ciudad de operación y medios logísticos (vehículo).

### ✓ REQ-011 · functional/must p.2 · NOISE
**Statement:** Verificar que el usuario exista y se encuentre activo en el directorio corporativo de TCC.

> **Source:** 1) Usuario válido: Verificar que el usuario exista y se encuentre activo en el directorio corporativo de TCC..

### ✓ REQ-012 · functional/must p.2 · NOISE
**Statement:** Confirmar que el identificador del vehículo ingresado exista en el maestro de vehículos y esté en estado activo.

> **Source:** 2) Medio logístico asociado: Confirmar que el identificador del vehículo ingresado exista en el maestro de vehículos y esté en estado activo..

### ✓ REQ-013 · functional/must p.2 · NOISE
**Statement:** Validar que el centro de operación asignado al usuario coincida con el centro de operación del medio logístico al que intenta acceder.

> **Source:** 3 )Consistencia operativa: Validar que el centro de operación asignado al usuario coincida con el centro de operación del medio logístico al que intenta acceder..

### ✓ REQ-014 · functional/must p.2 · NOISE
**Statement:** Que el usuario logeado, en el momento de relacionar el ID del medio logístico (vehículo), la aplicación realice la validación, de que actualmente no este logeado en otro medio logístico (vehículo). Para evitar simultaneidades en 2 órdenes de trabajo.

> **Source:** 4 )Que el usuario logeado, en el momento de relacionar el ID del medio logístico (vehículo), la aplicación realice la validación, de que actualmente no este logeado en otro medio logístico (vehículo). Para evitar simultaneidades en 2 órdenes de trabajo.

### ✓ REQ-015 · functional/must p.2 · NOISE
**Statement:** Se requiere que consecuente a la validaciones del login, que evidencie el inicio exitoso de la orden de trabajo, mediante una mensaje de confirmación.

> **Source:** Se requiere que consecuente a la validaciones del login,  que evidencie el inicio exitoso de la orden de trabajo, mediante una mensaje de confirmación.

### ✓ REQ-016 · functional/must p.2 · NOISE
**Statement:** Se requiere que consecuente a la validaciones del login, en caso de no ser exitosa que se evidencie un mensaje explicando porque no fue posible el ingreso. Relacionando cual fue la validación que fallo.

> **Source:** Se requiere que consecuente a la validaciones del login, en caso de no ser exitosa que se evidencie un mensaje explicando porque no fue posible el ingreso. Relacionando cual fue la validación que fallo.

### ✓ REQ-017 · functional/must p.2
**Statement:** A partir del login exitoso, es necesario capturar el registro de los usuarios que se logean y se relacionan a un medio logístico (Vehículo). Para que este en capacidad de mirar por trazabilidad que tripulación desarrollo la orden de trabajo.

> **Source:** A partir del login exitoso, es necesario capturar el registro de los usuarios que se logean y se relacionan a un medio logístico (Vehículo). Para que este en capacidad de mirar por trazabilidad que tripulación desarrollo la orden de trabajo.

## Funcionalidad /Login - Visualización de orden de trabajo  (7)

### ✓ REQ-031 · functional/must p.2
**Statement:** Permitir la visualización de una E-Card o tarjeta digital informativa que consolide los principales datos operativos de la ruta asignada.

> **Source:** Permitir la visualización de una E-Card o tarjeta digital informativa que consolide los principales datos operativos de la ruta asignada.

### ✓ REQ-035 · usability/must p.2
**Statement:** Esta E-Card debe presentarse de forma clara, accesible y en tiempo real para los usuarios autorizados

> **Source:** Esta E-Card debe presentarse de forma clara, accesible y en tiempo real para los usuarios autorizados

### ✓ REQ-040 · functional/must p.2
**Statement:** La E-Card debe incluir como mínimo el Nombre de la ruta asignada.

> **Source:** Nombre de la ruta asignada.

### ✓ REQ-041 · functional/must p.2 · NOISE
**Statement:** La E-Card debe incluir como mínimo la Cantidad total de paradas programadas.

> **Source:** Cantidad total de paradas programadas.

### ✓ REQ-042 · functional/must p.2 · NOISE
**Statement:** La E-Card debe incluir como mínimo el Número de servicios de entrega asociados a la ruta.

> **Source:** Número de servicios de entrega asociados a la ruta.

### ✓ REQ-043 · functional/must p.2 · NOISE
**Statement:** La E-Card debe incluir como mínimo el Número de servicios de recogida asignados.

> **Source:** Número de servicios de recogida asignados.

### ✓ REQ-044 · functional/must p.2
**Statement:** La E-Card debe incluir el Valor total de dinero a recaudar durante la ejecución de la ruta, por producto y forma de pago.

> **Source:** Valor total de dinero a recaudar durante la ejecución de la ruta, por producto y forma de pago.

## Funcionalidad/ Consulta de unidad  (2)

### ✓ REQ-280 · functional/must p.2
**Statement:** La funcionalidad deberá permitir al usuario acceder a una vista consolidada de los datos clave de una unidad, incluyendo Datos del remitente (nombre, dirección, contacto), Datos del destinatario (nombre, dirección, contacto), Información de liquidación (valor del servicio, estado de cobro, forma de pago), Datos operativos (ciudad de origen, ciudad de destino, fecha y hora de recolección y entrega, estado actual del servicio), e Identificadores asociados (número de guía, número de orden de trabajo, placa del vehículo (si aplica), entre otros).

> **Source:** La funcionalidad deberá permitir al usuario acceder a una vista consolidada de los datos clave de una unidad, incluyendo:Datos del remitente: nombre, dirección, contacto.Datos del destinatario: nombre, dirección, contacto.Información de liquidación: valor del servicio, estado de cobro, forma de pago.Datos operativos: ciudad de origen, ciudad de destino, fecha y hora de recolección y entrega, estado actual del servicio.Identificadores asociados: número de guía, número de orden de trabajo, placa del vehículo (si aplica), entre otros.

### ✓ REQ-281 · usability/must p.2
**Statement:** La información de la unidad deberá presentarse de forma ordenada, agrupada por secciones (ej. remitente, destinatario, trazabilidad, liquidación), permitiendo al usuario identificar rápidamente los datos relevantes sin necesidad de desplazamientos extensos o múltiples clics.

> **Source:** La información deberá presentarse de forma ordenada, agrupada por secciones (ej. remitente, destinatario, trazabilidad, liquidación), permitiendo al usuario identificar rápidamente los datos relevantes sin necesidad de desplazamientos extensos o múltiples clics.

## Funcionalidad/ Cotización de servicio  (13)

### ✓ REQ-265 · functional/must p.2
**Statement:** La funcionalidad deberá permitir al usuario ingresar los siguientes datos mínimos del remitente: nombre, dirección, ciudad de origen, tipo de cliente (si aplica) para generar una cotización.

> **Source:** La funcionalidad deberá permitir al usuario ingresar los siguientes datos mínimos para generar una cotización:Datos del remitente: nombre, dirección, ciudad de origen, tipo de cliente (si aplica).

### ✓ REQ-266 · functional/must p.2
**Statement:** La funcionalidad deberá permitir al usuario ingresar los datos del destinatario: nombre, dirección, ciudad de destino para generar una cotización.

> **Source:** Datos del destinatario: nombre, dirección, ciudad de destino.

### ✓ REQ-267 · functional/must p.2
**Statement:** La funcionalidad deberá permitir al usuario ingresar las características de la unidad o envío: número de unidades, peso, volumen, tipo de mercancía, condiciones especiales (si aplica) para generar una cotización.

> **Source:** Características de la unidad o envío: número de unidades, peso, volumen, tipo de mercancía, condiciones especiales (si aplica).

### ✓ REQ-268 · functional/must p.2
**Statement:** Con base en los datos ingresados, el sistema deberá calcular automáticamente el valor estimado del servicio, considerando las reglas tarifarias vigentes, zonas de cobertura, condiciones comerciales y restricciones operativas.

> **Source:** Con base en los datos ingresados, el sistema deberá calcular automáticamente el valor estimado del servicio, considerando las reglas tarifarias vigentes, zonas de cobertura, condiciones comerciales y restricciones operativas.

### ✓ REQ-274 · functional/must p.2
**Statement:** El resultado de la cotización deberá presentarse de forma clara, incluyendo Valor total estimado y Detalle de componentes (flete base, recargos, impuestos, etc.).

> **Source:** El resultado deberá presentarse de forma clara, incluyendo:Valor total estimado.Detalle de componentes (flete base, recargos, impuestos, etc.).

### ✓ REQ-275 · functional/must p.2
**Statement:** El sistema deberá validar que los datos ingresados sean coherentes y completos antes de generar la cotización (Integración).

> **Source:** El sistema deberá validar que los datos ingresados sean coherentes y completos antes de generar la cotización (Integración).

### ✓ REQ-276 · functional/must p.2
**Statement:** En caso de inconsistencias (por ejemplo, ciudad de destino fuera de cobertura), deberá notificar al usuario y sugerir alternativas viables.

> **Source:** En caso de inconsistencias (por ejemplo, ciudad de destino fuera de cobertura), deberá notificar al usuario y sugerir alternativas viables.

### ✓ REQ-277 · functional/must p.2
**Statement:** Si el usuario acepta la cotización, el sistema deberá permitir la conversión directa a una orden de servicio, heredando automáticamente toda la información ingresada (remitente, destinatario, unidades, origen, destino, etc.) para evitar reprocesos.

> **Source:** Si el usuario acepta la cotización, el sistema deberá permitir la conversión directa a una orden de servicio, heredando automáticamente toda la información ingresada (remitente, destinatario, unidades, origen, destino, etc.) para evitar reprocesos.

### ✓ REQ-278 · functional/must p.2
**Statement:** Esta acción deberá redirigir al módulo de grabación de servicios, permitiendo al usuario completar los datos adicionales requeridos para formalizar el servicio.

> **Source:** Esta acción deberá redirigir al módulo de grabación de servicios, permitiendo al usuario completar los datos adicionales requeridos para formalizar el servicio.

### ✓ REQ-279 · data/must p.2
**Statement:** Toda cotización generada deberá quedar registrada en el sistema con un identificador único, incluyendo: Usuario que la generó, Fecha y hora, Datos de entrada, Resultado de la cotización, Estado (cotizada, convertida en servicio, descartada).

> **Source:** Toda cotización generada deberá quedar registrada en el sistema con un identificador único, incluyendo:Usuario que la generó.Fecha y hora.Datos de entrada.Resultado de la cotización.Estado (cotizada, convertida en servicio, descartada).

### ✓ REQ-580 · functional/must p.2
**Statement:** El sistema deberá calcular automáticamente el valor estimado del servicio considerando las reglas tarifarias vigentes, zonas de cobertura, condiciones comerciales y restricciones operativas.

> **Source:** Con base en los datos ingresados, el sistema deberá calcular automáticamente el valor estimado del servicio, considerando las reglas tarifarias vigentes, zonas de cobertura, condiciones comerciales y restricciones operativas.

### ✓ REQ-585 · functional/must p.2
**Statement:** El sistema debe notificar al usuario y sugerir alternativas viables cuando se detecten inconsistencias, como una ciudad de destino fuera de cobertura.

> **Source:** En caso de inconsistencias (por ejemplo, ciudad de destino fuera de cobertura), deberá notificar al usuario y sugerir alternativas viables.

### ✓ REQ-592 · functional/must p.2
**Statement:** El sistema debe registrar toda cotización generada con un identificador único.

> **Source:** Toda cotización generada deberá quedar registrada en el sistema con un identificador único...

## Funcionalidad/ Funcionalidad Copilot  (1)

### ✓ REQ-346 · functional/must p.2
**Statement:** Funcionalidad que guía e indica al colaborador para ejecutar procesos de la aplicación.

> **Source:** Funcionalidad que guía e indica al colaborador para ejecutar procesos de la aplicación.

## Funcionalidad/ Funcionalidad Pick to voice  (2)

### ✓ REQ-328 · functional/must p.2
**Statement:** Funcionalidad que permite ejecutar procesos mediante las indicaciones de la voz, relacionado a eventos importantes durante el desarrollo de flujos de entregas y recogidas.

> **Source:** Funcionalidad que permite ejecutar procesos mediante las indicaciones de la voz, relacionado a eventos importantes durante el desarrollo de flujos de entregas y recogidas.

### ✓ REQ-332 · functional/must p.2
**Statement:** La funcionalidad Pick to voice debe permitir programarle al flujo de la aplicación tips de a ejecutar del proceso.

> **Source:** Es decir que permita programarle al flujo de la aplicación tips de a ejecutar del proceso.

## Funcionalidad/ Lectura de codigos  (1)

### ✓ REQ-352 · functional/must p.2
**Statement:** Lectura de códigos de barras lineales y QR por cámara.

> **Source:** Lectura de códigos de barras lineales y QR por cámara.

## Funcionalidad/ Notificaciónes  (2)

### ✓ REQ-593 · security/must p.2
**Statement:** El sistema debe solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC).

> **Source:** Solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC)

### ✓ REQ-604 · functional/must p.2
**Statement:** La aplicación debe soportar comunicación en línea bidireccional para evidenciar automáticamente el cambio de estado de los servicios a los diferentes usuarios conectados al medio logístico.

> **Source:** Visualización de ejecución de servicios en línea (doble vía), evidenciando el cambio de estado de forma automática para los diferentes usuarios conectados al medio logístico

## Funcionalidad/ Reporte de recaudo  (11)

### ✓ REQ-282 · functional/must p.2
**Statement:** La funcionalidad deberá permitir la generación de un reporte que consolide la información financiera relacionada con los servicios ejecutados, incluyendo Monto recaudado (total de dinero efectivamente cobrado por el usuario), Monto pendiente (dinero correspondiente a servicios entregados, pero aún no recaudado), y Monto por recaudar (servicios programados o en tránsito con expectativa de recaudo).

> **Source:** La funcionalidad deberá permitir la generación de un reporte que consolide la información financiera relacionada con los servicios ejecutados, incluyendo:Monto recaudado: total de dinero efectivamente cobrado por el usuario.Monto pendiente: dinero correspondiente a servicios entregados, pero aún no recaudado.Monto por recaudar: servicios programados o en tránsito con expectativa de recaudo.

### ✓ REQ-283 · functional/must p.2
**Statement:** Cada registro del reporte deberá incluir información detallada del servicio asociado: Número de guía u orden, Fecha y hora del servicio, Ciudad de origen y destino, Estado del servicio (entregado, en tránsito, pendiente), Valor del servicio, valor recaudado y medio de recaudo.

> **Source:** Cada registro del reporte deberá incluir información detallada del servicio asociado, como:Número de guía u orden.Fecha y hora del servicio.Ciudad de origen y destino.Estado del servicio (entregado, en tránsito, pendiente).Valor del servicio, valor recaudado y medio de recaudo.

### ✓ REQ-284 · functional/must p.2
**Statement:** El reporte deberá identificar el método de recaudo utilizado en cada caso, tales como: Efectivo, Medio electrónico, Segmentación por tipo de medio electrónico, Recaudo Flete contra entrega, Recaudo contado en origen, Recaudo de producto.

> **Source:** El reporte deberá identificar el método de recaudo utilizado en cada caso, tales como:Efectivo.Medio electrónico.Segmentación por tipo de medio electrónicoRecaudo Flete contra entrega.Recaudo contado en origen Recaudo de producto.

### ✓ REQ-285 · functional/must p.2
**Statement:** El sistema deberá registrar y mostrar el usuario que realizó el recaudo, incluyendo: Nombre del usuario, Identificador de sesión, Fecha y hora del registro de recaudo.

> **Source:** El sistema deberá registrar y mostrar el usuario que realizó el recaudo, incluyendo:Nombre del usuario.Identificador de sesión.Fecha y hora del registro de recaudo.

### ✓ REQ-286 · functional/must p.2
**Statement:** El reporte deberá permitir aplicar filtros para facilitar la consulta, tales como: Rango de fechas, Estado del recaudo (recaudado, pendiente, por recaudar), Usuario, Ciudad o zona.

> **Source:** El reporte deberá permitir aplicar filtros para facilitar la consulta, tales como:Rango de fechas.Estado del recaudo (recaudado, pendiente, por recaudar).Usuario.Ciudad o zona.

### ✓ REQ-287 · data/must p.2
**Statement:** Toda la información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica y disponibilidad para otros módulos o reportes corporativos.

> **Source:** Toda la información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

### ✓ REQ-292 · data/must p.2
**Statement:** La información integrada en el sistema central debe permitir su consulta posterior.

> **Source:** Toda la información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

### ✓ REQ-293 · data/must p.2
**Statement:** La información integrada en el sistema central debe permitir trazabilidad histórica.

> **Source:** Toda la información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

### ✓ REQ-294 · data/must p.2
**Statement:** La información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central y estar disponible para otros módulos o reportes corporativos.

> **Source:** Toda la información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

### ✓ REQ-589 · functional/must p.2
**Statement:** El sistema debe permitir la generación de un reporte que consolide la información financiera relacionada con los servicios ejecutados, incluyendo monto recaudado, monto pendiente y monto por recaudar.

> **Source:** La funcionalidad deberá permitir la generación de un reporte que consolide la información financiera relacionada con los servicios ejecutados, incluyendo:Monto recaudado... Monto pendiente... Monto por recaudar...

### ✓ REQ-594 · data/must p.2
**Statement:** El sistema central debe integrar de forma estructurada toda la información capturada y generada por esta funcionalidad, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

> **Source:** Toda la información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

## Funcionalidad/ Servicios del dia  (26)

### ✓ REQ-068 · functional/must p.2 · NOISE
**Statement:** Identificación de subproducto, en caso de tratarse de un servicio con características especiales como logística inversa u otros tipos definidos por la operación.

> **Source:** Identificación de subproducto, en caso de tratarse de un servicio con características especiales como logística inversa u otros tipos definidos por la operación.

### ✓ REQ-069 · functional/must p.2
**Statement:** Que cada Geo punto de recogida tenga la opción dril Down donde se visualice la información del servicio, además las opciones de cómo llegar o contactar.

> **Source:** Que cada Geo punto de recogida tenga la opción dril Down donde se visualice la información del servicio, además  las opciones de cómo llegar o contactar.

### ✓ REQ-070 · functional/must p.2
**Statement:** Con relación a la información del servicio debe aparecer el Nombre del cliente remitente.

> **Source:** Con relación a la información del servicio debe aparecer las siguientes variables:1)Nombre del cliente remitenteDirección de despachoObservaciones asociadas al servicioNúmero de contacto del remitenteForma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)Identificación de tipo de recogida: En caso de tratarse de un servicio con remesas asociadas, o un servicio sin servicios asociados donde debes crear servicios, o servicio en el cual debes sincronizar para relacionar servicios creados por el cliente.

### ✓ REQ-071 · functional/must p.2
**Statement:** Para los servicios de entrega espero que visualmente se evidencie los estados de entregada, pendiente o con novedad.

> **Source:** Para los servicios de entrega espero que visualmente se evidencie los estados de entregada, pendiente o con novedad, además a medida que se vaya ejecutando cambie y se visualice el estado.

### ✓ REQ-072 · functional/must p.2
**Statement:** Para los servicios de entrega, a medida que se vaya ejecutando cambie y se visualice el estado.

> **Source:** Para los servicios de entrega espero que visualmente se evidencie los estados de entregada, pendiente o con novedad, además a medida que se vaya ejecutando cambie y se visualice el estado.

### ✓ REQ-073 · functional/must p.2
**Statement:** Para los servicios de recogida espero que visualmente se evidencie los estados de realizada, pendiente o con novedad, además a medida que se vaya ejecutando cambie y se visualice el estado.

> **Source:** Para los servicios de recogida espero que visualmente se evidencie los estados de realizada, pendiente o con novedad además a medida que se vaya ejecutando cambie y se visualice el estado.

### ✓ REQ-074 · functional/must p.2
**Statement:** Para los servicios de recogida, visualmente se evidencien los estados de realizada, pendiente o con novedad y a medida que se vaya ejecutando cambie y se visualice el estado.

> **Source:** Para los servicios de recogida espero que visualmente se evidencie los estados de realizada, pendiente o con novedad además a medida que se vaya ejecutando cambie y se visualice el estado.

### ✓ REQ-075 · functional/must p.2
**Statement:** En cada servicio de entrega, tenga la opción que lo lleve a ejecutar el inicio de flujo de entrega.

> **Source:** En cada servicio de entrega, tenga la opción que lo lleve a ejecutar el inicio de flujo de entrega.

### ✓ REQ-076 · functional/must p.2
**Statement:** En cada servicio de recogida, tenga la opción que lo lleve a ejecutar el inicio de flujo de recogida.

> **Source:** En cada servicio de recogida, tenga la opción  que lo lleve a ejecutar el inicio de flujo de recogida.

### ✓ REQ-077 · functional/must p.2
**Statement:** En cada servicio de entrega, tenga la opción que lo lleve a ejecutar el proceso de planteamiento de novedad.

> **Source:** En cada servicio de entrega, tenga la opción que lo lleve a ejecutar el proceso de planteamiento de novedad.

### ✓ REQ-078 · functional/must p.2
**Statement:** En cada servicio de recogida, tenga la opción que lo lleve a ejecutar el proceso de planteamiento de novedad (Mirar anexo).

> **Source:** En cada servicio de recogida, tenga la opción que lo lleve a ejecutar el proceso de planteamiento de novedad (Mirar anexo).

### ✓ REQ-079 · functional/should p.2
**Statement:** Esperamos que tenga la opción de realizar la actualización a demanda refrescando el listado de servicios asociados (Recogidas y entregas asociadas a la orden de trabajo).

> **Source:** Esperamos que tenga la opción de realizar la actualización a demanda refrescando el listado de servicios asociados (Recogidas y entregas asociadas a la orden de trabajo).

### ✓ REQ-080 · functional/must p.2
**Statement:** El sistema debe generar automáticamente el refresh para evidenciar los servicios nuevos asociados a la orden de trabajo (recogidas y entregas) cuando se asocien desde planeación durante la ejecución del proceso de entrega y recogida luego de iniciada el proceso de distribución.

> **Source:** Que se genere automáticamente el refresh, esperamos que se evidencie los servicios nuevos asociados a la orden de trabajo de manera automática (Recogidas y entregas), cuando se asocien desde planeación durante la ejecución del proceso de entrega y recogida luego de iniciada el proceso de distribución.

### ✓ REQ-081 · functional/must p.2
**Statement:** Opción de validación de conexión en línea o no conexión al sistema (Aplicación).

> **Source:** Opción de validación de conexión en línea o no conexión al sistema (Aplicación).

### ✓ REQ-082 · functional/must p.2
**Statement:** Si durante el proceso de ejecución de la ruta, desde planeación se asocia un nuevo servicio de recogida o entrega a la orden de trabajo se requiere que la aplicación genere notificación al usuario de la aplicación notificando la asociación de dicho servicio.

> **Source:** Si durante el proceso de ejecución de la ruta, desde planeación se asocia un nuevo servicio de recogida o entrega a la orden de trabajo  se requiere que la aplicación genere notificación al usuario de la aplicación notificando la asociación de dicho servicio..

### ✓ REQ-083 · functional/must p.2
**Statement:** Durante el proceso de ejecución de la ruta, necesitamos que la aplicación genere notificación al usuario cuando algún servicio este a 30 min de cumplir con franjas horarias de entrega o recolección.

> **Source:** Durante el proceso de ejecución de la ruta, necesitamos que la aplicación genere notificación al usuario cuando algún servicio este a 30 min de cumplir con franjas horarias de entrega o recolección.

### ✓ REQ-084 · functional/must p.2
**Statement:** Durante la ejecución, cuando termine de certificar una entrega o recogida e indique próxima parada a ser atendida espero que la aplicación le genere una notificación al cliente remitente o destinatario relacionado a proximidad a entrega de su servicio.

> **Source:** Durante la ejecución, cuando termine de certificar una entrega o recogida e indique próxima parada a ser atendida espero que la aplicación le genere una notificación al cliente remitente o destinatario relacionado a proximidad a entrega de su servicio.

### ✓ REQ-085 · functional/must p.2
**Statement:** Esperamos que se tenga la opción de parametrizar notificaciones y/o mensajes que se requiera enviar al usuario durante la ejecución de su orden de trayecto, se forma masiva.

> **Source:** Esperamos que se tenga la opción de parametrizar notificaciones y/o mensajes que se requiera enviar al usuario durante la ejecución de su orden de trayecto, se forma masiva.

### ✓ REQ-524 · functional/must p.2
**Statement:** El sistema debe generar automáticamente el refresh para evidenciar los servicios nuevos (recogidas y entregas) asociados a la orden de trabajo de manera automática cuando se asocien desde planeación durante la ejecución.

> **Source:** Que se genere automáticamente el refresh, esperamos que se evidencie los servicios nuevos asociados a la orden de trabajo de manera automática (Recogidas y entregas), cuando se asocien desde planeación durante la ejecución

### ⚠ REQ-527 · functional/must p.2
**Statement:** Al ejecutar la captura de la unidad la aplicación debe capturar usuario, hora, latitud, longitud y vehículo.

> **Source:** Opción visualización de servicios del dia en mapa... Que cada Geo punto de recogida tenga la opción dril Down... al ejecutar la captura de la unidad la aplicación debe capturar usuario, hora, latitud, longitud, vehículo

### ✓ REQ-530 · functional/must p.2
**Statement:** La aplicación debe generar una notificación al usuario de la aplicación notificando la asociación de un servicio asignado.

> **Source:** se requiere que la aplicación genere notificación al usuario de la aplicación notificando la asociación de dicho servicio

### ✓ REQ-531 · functional/must p.2
**Statement:** La aplicación debe generar una notificación al cliente remitente o destinatario relacionada con la proximidad de entrega de su servicio.

> **Source:** espero que la aplicación le genere una notificación al cliente remitente o destinatario relacionado a proximidad a entrega de su servicio

### ✓ REQ-533 · functional/must p.2 · NOISE
**Statement:** La aplicación debe ofrecer opciones de 'cómo llegar' o 'contactar'.

> **Source:** además las opciones de cómo llegar o contactar

### ⚠ REQ-534 · data/must p.2
**Statement:** El sistema debe gestionar un modelo de datos de servicio que incluya como mínimo: identificación de subproducto, tipo de servicio especial (logística inversa), identificación de tipo de recogida, y relación de remesas asociadas o servicios creados por el cliente.

> **Source:** Identificación de subproducto, en caso de tratarse de un servicio con características especiales como logística inversa... Identificación de tipo de recogida: En caso de tratarse de un servicio con remesas asociadas, o un servicio sin servicios asociados donde debes crear servicios, o servicio en el cual debes sincronizar para relacionar servicios creados por el cliente.

### ⚠ REQ-535 · functional/must p.2
**Statement:** La aplicación debe evidenciar visualmente de forma diferenciada los estados para entregas (entregada, pendiente, con novedad) y para recogidas (realizada, pendiente, con novedad).

> **Source:** Para los servicios de entrega espero que visualmente se evidencie los estados de entregada, pendiente o con novedad... Para los servicios de recogida espero que visualmente se evidencie los estados de realizada, pendiente o con novedad

### ✓ REQ-537 · functional/must p.2
**Statement:** El sistema debe permitir parametrizar notificaciones y mensajes masivos para enviar al usuario durante la ejecución de su orden de trayecto.

> **Source:** Esperamos que se tenga la opción de parametrizar notificaciones y/o mensajes que se requiera enviar al usuario durante la ejecución de su orden de trayecto, se forma masiva.

## Funcionalidad/ Servicios del dia - Funcionalidad de filtro (Entrega y Recogidas)  (3)

### ✓ REQ-049 · functional/must p.2
**Statement:** La solución debe permitir aplicar filtros dinámicos sobre el listado de servicios asociados a las órdenes de trabajo, con el fin de facilitar la consulta, trazabilidad y gestión operativa.

> **Source:** La solución debe permitir aplicar filtros dinámicos sobre el listado de servicios asociados a las órdenes de trabajo, con el fin de facilitar la consulta, trazabilidad y gestión operativa.

### ✓ REQ-050 · functional/must p.2
**Statement:** Por tipo de proceso: permitir filtrar por servicios de recogida o servicios de entrega.

> **Source:** Por tipo de proceso: permitir filtrar por servicios de recogida o servicios de entrega.

### ✓ REQ-051 · functional/must p.2
**Statement:** Por variables específicas del servicio, tales como: ID del servicio, Nombre del remitente, Nombre del destinatario, Forma de pago contado en origen, contado en destino y credito en origen o destino.

> **Source:** Por variables específicas del servicio, tales como:ID del servicioNombre del remitenteNombre del destinatarioForma de pago contado en origen, contado en destino y credito en origen o destino.

## Funcionalidad/ Servicios del dia - Opcion de llamada  (1)

### ✓ REQ-058 · functional/must p.2
**Statement:** Opción genere llamada telefónica al cliente destinatario o remitente según sea el servicio, dicha opción debe estar ubicada en cada servicio listado en servicios del día.

> **Source:** Opción genere llamada telefónica al cliente destinatario o remitente según sea el servicio, dicha opción debe estar ubicada en cada servicio listado en servicios del día.

## Funcionalidad/ Servicios del dia - Opción  de navegación en mapa  (1)

### ✓ REQ-059 · functional/must p.2
**Statement:** Opción que permita a partir desde la dirección de la parada, ejecutar la navegación en mapa accionando la funcionalidad de cómo llegar.

> **Source:** opción que permita a partir desde la dirección de la parada, ejecutar la navegación en mapa accionando la funcionalidad de cómo llegar, la opción debe aparecer listada en cada servicio.

## Funcionalidad/ Servicios del dia - Opción de notificación whatsapp  (4)

### ✓ REQ-060 · functional/must p.2
**Statement:** Opción de enviar mensajes parametrizables a demanda por el usuario, al cliente destinatario o remitente por medio del celular relacionado al servicio.

> **Source:** Opción de enviar mensajes parametrizables a demanda por el usuario, al cliente destinatario o remitente por medio del celular relacionado al servicio.

### ✓ REQ-061 · functional/must p.2 · NOISE
**Statement:** La opción de notificación whatsapp debe estar listada en cada uno de los servicios.

> **Source:** La opción debe estar listada en cada uno de los servicios.

### ✓ REQ-062 · functional/must p.2
**Statement:** Las opciones de mensajes parametrizables se relacionan a cercanía de la parada, alguna novedad o duda sobre el servicio.

> **Source:** Las opciones de mensajes parametrizables se relacionan a cercanía de la parada, alguna novedad o duda sobre el servicio

### ✓ REQ-063 · functional/must p.2
**Statement:** Estos mensajes deben quedar parametrizables desde el módulo de parámetros de notificaciones.

> **Source:** estos mensajes deben quedar parametrizables desde el módulo de parámetros de notificaciones.

## Funcionalidad/ Servicios del dia - Opción visualización de servicios del dia en mapa  (4)

### ✓ REQ-064 · functional/must p.2
**Statement:** Opción en la pantalla de servicios del día, que lo lleve a la funcionalidad de navegación en mapa.

> **Source:** Opción en la pantalla de servicios del día, que lo lleve a la funcionalidad de navegación en mapa.

### ✓ REQ-065 · functional/must p.2
**Statement:** Al ingresar a la navegación en mapa es necesaria la visualización de los servicios del día georreferenciados en el mapa, de acuerdo a la dirección de destino para las entregas, y de despacho para las recogidas.

> **Source:** Al ingresar a la navegación en mapa es necesaria la visualización de los servicios del día georreferenciados en el mapa, de acuerdo a la dirección de destino para las entregas, y de despacho para las recogidas.

### ✓ REQ-066 · functional/must p.2
**Statement:** Que cada geo punto de entrega tenga la opción drill down donde se visualice la información del servicio.

> **Source:** Que cada geo punto de entrega tenga la opción dril Down donde se visualice la información del servicio, además  las opciones de cómo llegar o contactar.

### ✓ REQ-067 · functional/must p.2
**Statement:** Con relación a la información del servicio del drill down debe aparecer: Nombre del cliente destinatario, Dirección de entrega, Observaciones asociadas al servicio, Número de contacto del destinatario, Forma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino).

> **Source:** Con relación a la información del servicio debe aparecer las siguientes variables:1) Nombre del cliente destinatarioDirección de entregaObservaciones asociadas al servicioNúmero de contacto del destinatarioForma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)

## Funcionalidad/ Servicios del dia - Visualización de ejecución de orden de trabajo  (1)

### ✓ REQ-057 · functional/must p.2
**Statement:** En la pantalla de servicios del dia en la parte superior cuidando la experiencia de usuario necesitamos una Card donde visualice el progreso de la orden de trabajo del día, que se segmente el nombre de la ruta, cantidad de paradas, cantidad de servicios de entregas, de recogidas, lo anterior separado por servicios faltantes, realizadas y con novedad, dinero recaudado, dinero pendiente por recaudar, dicho comportamiento debe ser interactivo mostrando el porcentaje de progreso a medida que se ejecuten los servicios.

> **Source:** En la pantalla de servicios del dia en la parte superior cuidando la experiencia de usuario necesitamos una Card donde visualice el progreso de la orden de trabajo del día, que se segmente el nombre de la ruta, cantidad de paradas, cantidad de servicios de entregas, de recogidas, lo anterior separado por servicios faltantes, realizadas y con novedad, dinero recaudado, dinero pendiente por recaudar  dicho comportamiento debe ser interactivo mostrando el porcentaje de progreso a medida que se ejecuten los servicios.

## Funcionalidad/ Servicios del dia - Visualización de servicios (Entrega y Recogidas)  (4)

### ✓ REQ-045 · functional/must p.2
**Statement:** La solución debe permitir la visualización dinámica y en tiempo real del listado de órdenes de trabajo, incluyendo el estado actualizado de cada uno de los servicios asociados.

> **Source:** La solución debe permitir la visualización dinámica y en tiempo real del listado de órdenes de trabajo, incluyendo el estado actualizado de cada uno de los servicios asociados.

### ✓ REQ-046 · functional/must p.2
**Statement:** Visualización del estado de los servicios, diferenciando claramente si se encuentran en estado pendiente, con novedad o cumplido.

> **Source:** 1) Visualización del estado de los servicios, diferenciando claramente si se encuentran en estado pendiente, con novedad o cumplido..

### ✓ REQ-047 · functional/must p.2
**Statement:** Actualización dinámica, de modo que a medida que se ejecutan los servicios, se reflejen los cambios en el estado y en la secuencia de ejecución.

> **Source:** 2)Actualización dinámica, de modo que a medida que se ejecutan los servicios, se reflejen los cambios en el estado y en la secuencia de ejecución..

### ✓ REQ-048 · functional/must p.2
**Statement:** Representación de la secuencia de planeación, mostrando el orden previsto de ejecución de los servicios según la lógica integrada desde la planeación.

> **Source:** 3) Representación de la secuencia de planeación, mostrando el orden previsto de ejecución de los servicios según la lógica integrada desde la planeación..

## Funcionalidad/ Servicios del dia - Visualización de servicios (Entregas)  (4)

### ✓ REQ-052 · functional/must p.2
**Statement:** Por cada servicio de entrega en el listado de servicios, se visualice de forma clara y estructurada la siguiente información operativa: Nombre del cliente destinatario, Dirección de entrega, Observaciones asociadas al servicio, Número de contacto del destinatario, Forma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino), Identificación de subproducto en caso de tratarse de un servicio con características especiales como logística inversa u otros tipos definidos por la operación.

> **Source:** Por cada servicio de entrega en el listado de servicios, se visualice de forma clara y estructurada la siguiente información operativa:1) Nombre del cliente destinatarioDirección de entregaObservaciones asociadas al servicioNúmero de contacto del destinatarioForma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)Identificación de subproducto, en caso de tratarse de un servicio con características especiales como logística inversa u otros tipos definidos por la operación.

### ✓ REQ-053 · functional/must p.2
**Statement:** Ícono de mapa: acceso a la funcionalidad de navegación (cómo llegar) hacia la dirección del destinatario, por cada servicio de entrega.

> **Source:** Ícono de mapa: acceso a la funcionalidad de navegación (cómo llegar) hacia la dirección del destinatario.

### ✓ REQ-054 · functional/must p.2
**Statement:** Ícono de teléfono: ejecución directa de llamada al número de contacto del destinatario, por cada servicio de entrega.

> **Source:** Ícono de teléfono: ejecución directa de llamada al número de contacto del destinatario.

### ✓ REQ-055 · functional/must p.2
**Statement:** Ícono de información: visualización de información complementaria del servicio, incluyendo datos del remitente, detalles del destinatario, unidades asociadas al servicio y cualquier otra información relevante para la operación, por cada servicio de entrega.

> **Source:** Ícono de información: visualización de información complementaria del servicio, incluyendo datos del remitente, detalles del destinatario, unidades asociadas al servicio y cualquier otra información relevante para la operación.

## Funcionalidad/ Servicios del dia - Visualización de servicios (Recogidas)  (1)

### ✓ REQ-056 · functional/must p.2
**Statement:** Por cada servicio de recogida en el listado de servicios, se visualice de forma clara y estructurada la siguiente información operativa: Nombre del cliente remitente, Dirección de despacho, Observaciones asociadas al servicio, Número de contacto del remitente, Forma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino), Identificación de tipo de recogida en caso de tratarse de un servicio con remesas asociadas, o un servicio sin servicios asociados donde debes crear servicios, o servicio en el cual debes sincronizar para relacionar servicios creados por el cliente.

> **Source:** Por cada servicio de recogida en el listado de servicios, se visualice de forma clara y estructurada la siguiente información operativa:1)Nombre del cliente remitenteDirección de despachoObservaciones asociadas al servicioNúmero de contacto del remitenteForma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)Identificación de tipo de recogida: En caso de tratarse de un servicio con remesas asociadas, o un servicio sin servicios asociados donde debes crear servicios, o servicio en el cual debes sincronizar para relacionar servicios creados por el cliente.

## Funcionalidad/Cierre de ruta  (13)

### ✓ REQ-246 · functional/must p.2
**Statement:** El sistema debe validar que todos los servicios planeados estén ejecutados, y cero registros pendientes.

> **Source:** Validación de que todos los servicios planeados estén ejecutados, y cero registros pendientes.

### ✓ REQ-247 · functional/must p.2
**Statement:** El sistema debe permitir capturar los datos al sistema de los servicios, fecha y usuario.

> **Source:** que permita capturar los datos al sistema de los servicios, fecha y usuario.

### ✓ REQ-248 · functional/must p.2
**Statement:** Al marcar como entregado el último servicio asignado en la ruta, el sistema deberá ejecutar automáticamente el procedimiento de cierre, sin requerir intervención adicional del usuario.

> **Source:** Al marcar como entregado el último servicio asignado en la ruta, el sistema deberá ejecutar automáticamente el procedimiento de cierre, sin requerir intervención adicional del usuario.

### ✓ REQ-249 · functional/must p.2
**Statement:** Este cierre automático incluirá un resumen de la jornada y confirmación de finalización, optimizando tiempos y reduciendo errores por omisión.

> **Source:** Este cierre automático incluirá un resumen de la jornada y confirmación de finalización, optimizando tiempos y reduciendo errores por omisión.

### ✓ REQ-250 · functional/must p.2
**Statement:** El sistema deberá realizar validaciones automáticas sobre el estado de todos los servicios asignados en la ruta, identificando si están completados, pendientes o presentan novedades.

> **Source:** El sistema deberá realizar validaciones automáticas sobre el estado de todos los servicios asignados en la ruta, identificando si están completados, pendientes o presentan novedades.

### ✓ REQ-251 · functional/must p.2
**Statement:** En caso de detectar inconsistencias (por ejemplo, servicios sin registrar o con datos incompletos), deberá alertar al usuario antes de permitir el cierre.

> **Source:** En caso de detectar inconsistencias (por ejemplo, servicios sin registrar o con datos incompletos), deberá alertar al usuario antes de permitir el cierre.

### ✓ REQ-252 · functional/must p.2
**Statement:** Durante el proceso de cierre, la aplicación deberá permitir la captura de información clave para trazabilidad y análisis posterior, incluyendo, pero no limitándose a: hora de cierre, ubicación GPS, observaciones del conductor, estado del vehículo, y cualquier otra variable definida por la operación.

> **Source:** la aplicación deberá permitir la captura de información clave para trazabilidad y análisis posterior. Esto incluye, pero no se limita a: hora de cierre, ubicación GPS, observaciones del conductor, estado del vehículo, y cualquier otra variable definida por la operación.

### ✓ REQ-253 · functional/must p.2
**Statement:** El sistema deberá identificar automáticamente servicios que no hayan sido gestionados y sugerir al usuario el registro de una novedad asociada (por ejemplo, cliente ausente, dirección incorrecta, etc.).

> **Source:** El sistema deberá identificar automáticamente servicios que no hayan sido gestionados y sugerir al usuario el registro de una novedad asociada (por ejemplo, cliente ausente, dirección incorrecta, etc.).

### ✓ REQ-254 · functional/must p.2
**Statement:** Esta funcionalidad debe facilitar el cumplimiento de protocolos operativos.

> **Source:** Esta funcionalidad debe facilitar el cumplimiento de protocolos operativos y asegurar la integridad de la información registrada.

### ✓ REQ-577 · security/must p.2
**Statement:** Al detectar que el usuario no ha interactuado con la aplicación durante el tiempo definido, el sistema deberá finalizar la sesión de forma segura.

> **Source:** Al detectar que el usuario no ha interactuado con la aplicación durante el tiempo definido, se deberá:Finalizar la sesión de forma segura.Registrar el evento como cierre automático por inactividad.

### ✓ REQ-578 · security/must p.2
**Statement:** El sistema deberá contar con un mecanismo de cierre automático de sesión en caso de inactividad prolongada, cuyo tiempo sea un parámetro configurable.

> **Source:** El sistema deberá contar con un mecanismo de cierre automático de sesión en caso de inactividad prolongada (parámetro configurable).

### ✓ REQ-579 · functional/must p.2
**Statement:** El sistema deberá capturar la ubicación GPS durante el cierre de ruta.

> **Source:** Esto incluye, pero no se limita a: hora de cierre, ubicación GPS, observaciones del conductor, estado del vehículo, y cualquier otra variable definida por la operación.

### ✓ REQ-590 · functional/must p.2
**Statement:** El sistema debe validar si existe una sesión anterior cerrada automáticamente y permitir la reanudación del trabajo desde el punto en que se dejó, siempre que la orden de trabajo siga vigente.

> **Source:** el sistema deberá validar si existe una sesión anterior cerrada automáticamente y permitir la reanudación del trabajo desde el punto en que se dejó, siempre que la orden de trabajo siga vigente.

## Funcionalidad/Cierre de ruta/ Cierre de sesión  (7)

### ✓ REQ-258 · security/must p.2
**Statement:** El sistema deberá permitir al usuario cerrar sesión de forma segura sin que esto implique el cierre automático de la orden de trabajo activa.

> **Source:** El sistema deberá permitir al usuario cerrar sesión de forma segura sin que esto implique el cierre automático de la orden de trabajo activa.

### ✓ REQ-259 · security/must p.2
**Statement:** La aplicación deberá mantener un registro detallado de los eventos de inicio y cierre de sesión, incluyendo: Identificador del usuario, Fecha y hora de inicio y cierre de sesión, Estado de la orden de trabajo al momento del cierre, Indicador de si el cierre fue manual o automático por inactividad.

> **Source:** La aplicación deberá mantener un registro detallado de los eventos de inicio y cierre de sesión, incluyendo:Identificador del usuario.Fecha y hora de inicio y cierre de sesión.Estado de la orden de trabajo al momento del cierre.Indicador de si el cierre fue manual o automático por inactividad.

### ✓ REQ-260 · security/must p.2
**Statement:** El sistema deberá contar con un mecanismo de cierre automático de sesión en caso de inactividad prolongada (parámetro configurable).

> **Source:** El sistema deberá contar con un mecanismo de cierre automático de sesión en caso de inactividad prolongada (parámetro configurable).

### ✓ REQ-261 · security/must p.2
**Statement:** Al detectar inactividad prolongada, el sistema deberá registrar el evento como cierre automático por inactividad.

> **Source:** Registrar el evento como cierre automático por inactividad.

### ✓ REQ-262 · functional/must p.2 · NOISE
**Statement:** Al detectar inactividad prolongada, el sistema deberá mantener el estado de la orden de trabajo sin alteraciones.

> **Source:** Mantener el estado de la orden de trabajo sin alteraciones.

### ✓ REQ-263 · functional/must p.2
**Statement:** Al detectar inactividad prolongada, el sistema deberá notificar al usuario al momento de su próximo inicio de sesión sobre el cierre automático ocurrido.

> **Source:** Notificar al usuario al momento de su próximo inicio de sesión sobre el cierre automático ocurrido.

### ✓ REQ-264 · functional/must p.2
**Statement:** El sistema deberá permitir la reanudación del trabajo desde el punto en que se dejó al iniciar sesión nuevamente, siempre que la orden de trabajo siga vigente.

> **Source:** Al iniciar sesión nuevamente, el sistema deberá validar si existe una sesión anterior cerrada automáticamente y permitir la reanudación del trabajo desde el punto en que se dejó, siempre que la orden de trabajo siga vigente.

## Infraestructura  (1)

### ✓ REQ-427 · reliability/must p.3
**Statement:** Todos los componentes arquitectónicos de la solución como servidor de aplicaciones, bases de datos y Apis de integración deben estar soportados en una arquitectura escalable, de alta disponibilidad (superior 99.95%) y con calidad del servicio (superior 99,6%).

> **Source:** Todos los componentes arquitectónicos de la solución como servidor de  aplicaciones, bases de datos y Apis de integración deben estar soportados en una arquitectura escalable, de alta disponibilidad (superior 99.95%) y con calidad del servicio (superior 99,6%).

## Infraestructura - Conectividad  (2)

### ✓ REQ-431 · data/must p.3
**Statement:** Asegurar interoperabilidad con los sistemas de la organización (ej. ERP, CRM, base datos, etc.).

> **Source:** Asegurar interoperabilidad con los sistemas de la organización (ej. ERP, CRM, base datos, etc.). Adjuntar soportes y documentación.

### ✓ REQ-436 · constraint/must p.3
**Statement:** Asegurar las soluciones para conexión privada y dedicada entre los entornos de la compañía y la nueva solución (ej. FastConnect, VPN, etc.).

> **Source:** Asegurar las soluciones para conexión privada y dedicada entre los entornos de la compañía y la nueva solución (ej. FastConnect, VPN, etc.).

## Infraestructura - Informes  (1)

### ✓ REQ-441 · functional/must p.3
**Statement:** El sistema debe entregar reportes de monitoreo, rendimiento y uso de recursos con una periodicidad mensual.

> **Source:** Entrega de reportes de monitoreo, rendimiento y uso de recursos con una periodicidad mensual y acceso a dashboard en tiempo real.

## Infraestructura - Networking  (1)

### ✓ REQ-440 · data/must p.3
**Statement:** Integrar con APIS, SOAP, Rest web sockets.

> **Source:** Integrar con APIS, SOAP, Rest web sockets.

## Pantalla inicio de ejecución de recolección -Validaciónes de servicio  (1)

### ✓ REQ-559 · functional/must p.2
**Statement:** El sistema debe realizar validaciones antes de ejecutar un servicio, tales como franjas horarias para ejecutar el servicio.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como: Franjas horarias para ejecutar el servicio.

## Reporte BI / Modulo Administrativo  (1)

### ✓ REQ-497 · functional/must p.2
**Statement:** El sistema debe incluir un reporte de Business Intelligence (BI).

> **Source:** Reporte BI/Modulo Administrativo

## Reporte BI/Modulo Administrativo  (13)

### ✓ REQ-295 · functional/must p.2
**Statement:** Integración con power BI, mediante la estructuración de un reporte que permita evidenciar la usabilidad de las funcionalidades de la aplicación, por orden de trabajo, usuario y proceso.

> **Source:** Integración con power BI, mediante la estructuración de un reporte que permita evidenciar la usabilidad de las funcionalidades de la aplicación, por orden de trabajo, usuario y proceso.

### ✓ REQ-301 · functional/must p.2
**Statement:** Modulo administrativo que permita configurar y/o parametrizar notificaciones para los usuarios de la aplicación.

> **Source:** Modulo administrativo que permita configurar y/o parametrizar notificaciones para los usuarios de la aplicación.

### ✓ REQ-305 · functional/must p.2
**Statement:** Modulo administrativo que permita configurar y/o parametrizar notificaciones para los clientes destinatarios del servicio.

> **Source:** Modulo administrativo que permita configurar y/o parametrizar notificaciones para los clientes destinatarios del servicio.

### ✓ REQ-309 · functional/must p.2
**Statement:** Modulo administrativo de configuración relacionado con roles de la aplicación.

> **Source:** Modulo administrativo de configuración relacionado con roles de la aplicación.

### ✓ REQ-314 · functional/must p.2
**Statement:** Integración con power bi, mediante el desarrollo de un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla, que evidencie el desempeño de las tripulaciones logísticas.

> **Source:** Integración con power bi, mediante el desarrollo de un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla, que evidencie el desempeño de las tripulaciones logísticas.

### ✓ REQ-318 · functional/must p.2
**Statement:** El sistema debe integrar con Power BI un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla y evidencie el desempeño de las tripulaciones logísticas.

> **Source:** Integración con power bi, mediante el desarrollo de un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla, que evidencie el desempeño de las tripulaciones logísticas.

### ✓ REQ-323 · functional/must p.2
**Statement:** Integración con power Bi, mediante el desarrollo de un reporte que capture el comportamiento de cierre de ordenes de trabajo, con los servicios pendientes, ejecutados, por orden de trabajo.

> **Source:** Integración con power Bi, mediante el desarrollo de un reporte que capture el comportamiento de cierre de ordenes de trabajo, con los servicios pendientes, ejecutados, por orden de trabajo.

### ✓ REQ-599 · functional/must p.2
**Statement:** La integración con Power BI debe estructurar un reporte que permita evidenciar la usabilidad de las funcionalidades de la aplicación, por orden de trabajo, usuario y proceso.

> **Source:** Integración con power BI, mediante la estructuración de un reporte que permita evidenciar la usabilidad de las funcionalidades de la aplicación, por orden de trabajo, usuario y proceso.

### ✓ REQ-600 · functional/must p.2
**Statement:** El módulo administrativo debe permitir configurar y/o parametrizar notificaciones para los usuarios de la aplicación.

> **Source:** Modulo administrativo que permita configurar y/o parametrizar notificaciones para los usuarios de la aplicación

### ✓ REQ-601 · functional/must p.2
**Statement:** El sistema debe contar con un módulo administrativo de configuración relacionado con los roles de la aplicación.

> **Source:** Modulo administrativo de configuración relacionado con roles de la aplicación

### ✓ REQ-605 · functional/must p.2
**Statement:** El sistema debe proveer control de versiones y trazabilidad de cambios en los flujos.

> **Source:** Control de versiones y trazabilidad de cambios en los flujos

### ✓ REQ-606 · functional/must p.2
**Statement:** El sistema debe integrarse con Power BI mediante el desarrollo de un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla.

> **Source:** Integración con power bi, mediante el desarrollo de un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla

### ✓ REQ-607 · functional/must p.2 · NOISE
**Statement:** El sistema debe gestionar entidades de 'tipo de móviles' a través de un módulo administrativo de roles, usuarios y tipo de móviles.

> **Source:** Modulo adminsitrador de roles, usuarios y tipo de moviles

## Reporte BI/Modulo Administrativo - Módulo de Configuración de Flujos  (7)

### ✓ REQ-333 · functional/must p.2
**Statement:** La aplicación deberá contar con un módulo que permita la configuración dinámica de flujos operativos, con el fin de adaptar y optimizar los procesos internos según las necesidades del negocio.

> **Source:** La aplicación deberá contar con un módulo que permita la configuración dinámica de flujos operativos, con el fin de adaptar y optimizar los procesos internos según las necesidades del negocio.

### ✓ REQ-337 · functional/must p.2
**Statement:** Interfaz gráfica para la creación y edición de flujos.

> **Source:** Interfaz gráfica para la creación y edición de flujos.

### ✓ REQ-338 · functional/must p.2
**Statement:** Posibilidad de definir condiciones, reglas y acciones automatizadas (por ejemplo: asignación de pedidos, validaciones, notificaciones).

> **Source:** Posibilidad de definir condiciones, reglas y acciones automatizadas (por ejemplo: asignación de pedidos, validaciones, notificaciones).

### ✓ REQ-339 · functional/must p.2
**Statement:** Integración con otros módulos del sistema (logística, pagos, usuarios, etc.).

> **Source:** Integración con otros módulos del sistema (logística, pagos, usuarios, etc.).

### ✓ REQ-343 · functional/must p.2
**Statement:** El sistema debe proporcionar control de versiones de los flujos configurados.

> **Source:** Control de versiones y trazabilidad de cambios en los flujos.

### ✓ REQ-344 · functional/must p.2
**Statement:** Capacidad para activar o desactivar flujos según contexto operativo o necesidad específicas.

> **Source:** Capacidad para activar o desactivar flujos según contexto operativo o necesidad específicas.

### ✓ REQ-345 · functional/must p.2
**Statement:** Capacidad para activar o desactivar campos de las diferentes pantallas, según contexto operativo o necesidad específicas.

> **Source:** Capacidad para activar o desactivar campos de las diferentes pantallas,  según contexto operativo o necesidad específicas.

## Soporte - Disponibilidad  (1)

### ✓ REQ-425 · reliability/must p.3
**Statement:** Disponibilidad mínima del 99,95%.

> **Source:** Disponibilidad mínima del 99,95%.

## Soporte - Monitoreo  (1)

### ✓ REQ-424 · reliability/must p.3
**Statement:** Permitir la integración con herramientas de monitoreo TCC para la identificación temprana de fallos en los componentes de la solución (servicios, Kubernetes, plataformas, etc.).

> **Source:** Permitir la integración con herramientas de monitoreo TCC para la identificación temprana de fallos en los componentes de la solución (servicios, Kubernetes, plataformas, etc.).

## Soporte / Infraestructura  (1)

### ✓ REQ-623 · reliability/must p.3
**Statement:** Todos los componentes arquitectónicos de la solución —servidor de aplicaciones, bases de datos y APIs de integración— deben estar soportados en una arquitectura escalable de alta disponibilidad con disponibilidad mínima superior al 99,95%.

> **Source:** Disponibilidad mínima del 99,95%. [...] Todos los componentes arquitectónicos de la solución como servidor de aplicaciones, bases de datos y Apis de integración deben estar soportados en una arquitectura escalable, de alta disponibilidad (superior 99.95%)
