# Extracción de requerimientos — 1. Requerimientos tecnicos- funcionales.xlsx

- Extraídos: **482**
- Span verificados: **448/482** (93%)
- GT (col Descripción): **296**  ·  recall@0.7: ver consola

Leyenda: ✓ span verificado · ⚠ span NO verificado (posible alucinación) · Noise = sin match contra GT (ruido de plantilla, no alucinación).

## Aplicación ruta / Req funcionales  (6)

### ✓ 1  · conf 0.80 p.2 · NOISE
**Statement:** Marcar con una 'X' la solución que se ofrece para cada necesidad detallada por TCC, indicando el tipo de soporte (A, B, C o NS).

> **Source:** Marque con una "X", si la(s) solución(es) que Ud. ofrece, cubre cada necesidad detallada por TCC, de acuerdo a las siguientes opciones:

### ⚠ 2  · conf 0.90 p.2 · NOISE
**Statement:** Tipo de soporte A significa que la funcionalidad es soportada al entregarse 'listo para utilizarse'.

> **Source:** A, Tipo de soporte por funcionalidad = Soportado al entregarse "listo para utilizarse"

### ⚠ 3  · conf 0.90 p.2 · NOISE
**Statement:** Tipo de soporte B significa que la funcionalidad es soportada mediante desarrollo sin costo.

> **Source:** B, Tipo de soporte por funcionalidad = Soportado mediante desarrollo sin costo

### ⚠ 4  · conf 0.90 p.2 · NOISE
**Statement:** Tipo de soporte C significa que la funcionalidad es soportada mediante desarrollo con costo adicional.

> **Source:** C, Tipo de soporte por funcionalidad = Soportado mediante desarrollo con costo adicional ( este costo deberá ser tenido en cuenta en la propuesta economica)

### ✓ 5  · conf 0.90 p.2 · NOISE
**Statement:** El costo adicional del tipo de soporte C deberá ser tenido en cuenta en la propuesta económica.

> **Source:** este costo deberá ser tenido en cuenta en la propuesta economica

### ⚠ 6  · conf 0.90 p.2 · NOISE
**Statement:** Tipo de soporte NS significa que la funcionalidad no es soportada.

> **Source:** NS, Tipo de soporte por funcionalidad = No soportado

## Arquitectura - Back End  (18)

### ✓ 1  · conf 0.97 p.3
**Statement:** La aplicación back end debe tener una arquitectura basada en eventos.

> **Source:** Aplicación con una arquitectura basada en eventos.

### ✓ 2  · conf 0.97 p.3
**Statement:** Si existen logs, en estos no se debe colocar información sensible en claro.

> **Source:** Si existen logs, en estos no se debe colocar información sensible en claro.

### ✓ 3  · conf 0.98 p.3
**Statement:** La lectura de los códigos de barra no debe ser mayor a 300 milisegundos.

> **Source:** La lectura de los códigos de barra no debe ser mayor a 300 milisegundos.

### ✓ 4  · conf 0.96 p.3
**Statement:** La aplicación debe permitir la integración por medio de APIs (Envío de eventos, pagos, notificaciones, entre otros) en ambas direcciones: enviar info desde la aplicación a sistemas internos y al revés.

> **Source:** La aplicación debe permitir la integración por medio de APIs (Envío de eventos, pagos, notificaciones, entre otros.) Esta integración debe ser en ambas direcciones: enviar info. desde la aplicación a sistemas internos y al revés..

### ✓ 5  · conf 0.97 p.3
**Statement:** La arquitectura debe permitir el procesamiento simultáneo de múltiples eventos generados por la aplicación y actualizados en tiempo real con el backend.

> **Source:** La arquitectura debe permitir el procesamiento simultáneo de múltiples eventos generados por la aplicación y actualizados en tiempo real con el backend..

### ✓ 6  · conf 0.97 p.3
**Statement:** La aplicación debe tener una arquitectura flexible y escalable.

> **Source:** La aplicación debe tener una arquitectura flexible y escalable..

### ✓ 7  · conf 0.97 p.3
**Statement:** La aplicación debe tener una arquitectura resiliente a fallos.

> **Source:** La aplicación debe tener una arquitectura resiliente a fallos.

### ✓ 8  · conf 0.97 p.3
**Statement:** La aplicación debe tener una arquitectura de alta disponibilidad y que garantice su comunicación en tiempo real haciendo uso de Microservicios, Kubernetes y WebSockets.

> **Source:** La aplicación debe tener una arquitectura de alta disponibilidad y que garantice su comunicación en tiempo real (uso de Microservicios, Kubernetes y WebSockets)..

### ✓ 9  · conf 0.95 p.3
**Statement:** Se debe permitir la visualización de ejecución de servicios en línea (doble vía), evidenciando el cambio de estado de forma automática para los diferentes usuarios conectados al vehículo.

> **Source:** Visualización de ejecución de servicios en línea (doble vía), evidenciando el cambio de estado de forma automática para los diferentes usuarios conectados al vehículo..

### ✓ 10  · conf 0.96 p.3
**Statement:** Se debe implementar protocolos seguros para la comunicación con conexiones seguras HTTPS.

> **Source:** Implementación de protocolos seguros para la comunicación (conexiones seguras HTTPS, cifrado para datos sensibles como datos bancarios, validación de certificados de servidor SSL, inclusión de Headers de Seguridad(CSP))..

### ✓ 11  · conf 0.96 p.3 · NOISE
**Statement:** Se debe implementar cifrado para datos sensibles como datos bancarios.

> **Source:** Implementación de protocolos seguros para la comunicación (conexiones seguras HTTPS, cifrado para datos sensibles como datos bancarios, validación de certificados de servidor SSL, inclusión de Headers de Seguridad(CSP))..

### ✓ 12  · conf 0.96 p.3 · NOISE
**Statement:** Se debe implementar validación de certificados de servidor SSL.

> **Source:** Implementación de protocolos seguros para la comunicación (conexiones seguras HTTPS, cifrado para datos sensibles como datos bancarios, validación de certificados de servidor SSL, inclusión de Headers de Seguridad(CSP))..

### ✓ 13  · conf 0.96 p.3 · NOISE
**Statement:** Se debe implementar inclusión de Headers de Seguridad (CSP).

> **Source:** Implementación de protocolos seguros para la comunicación (conexiones seguras HTTPS, cifrado para datos sensibles como datos bancarios, validación de certificados de servidor SSL, inclusión de Headers de Seguridad(CSP))..

### ✓ 14  · conf 0.97 p.3
**Statement:** Se debe hacer uso de firmas digitales en las transacciones usando claves asimétricas.

> **Source:** Uso de firmas digitales en las transacciones usando claves asimétricas..

### ✓ 15  · conf 0.97 p.3
**Statement:** Se debe implementar el concepto de Rate Limit que prevenga ataques de fuerza bruta.

> **Source:** Implementar el concepto de Rate Limit que prevengan ataques de fuerza bruta..

### ✓ 16  · conf 0.97 p.3
**Statement:** Si hay almacenamiento de contraseñas estas deben estar encriptadas.

> **Source:** Si hay almacenamiento de contraseñas estas deben estar encriptadas,

### ✓ 17  · conf 0.97 p.3
**Statement:** Se debe implementar balanceo de cargas.

> **Source:** Implementación de balanceo de Cargas. Indicar como se garantiza.

### ✓ 18  · conf 0.96 p.3
**Statement:** En el caso del software a la medida, se deben entregar los requerimientos de HW que garanticen el cumplimiento de los requerimientos de alta disponibilidad, DRP, flexibilidad y escalabilidad.

> **Source:** En el caso del software a la medida, se deben entregar los requerimientos de HW que garanticen el cumplimiento de los requerimientos de alta disponibilidad, DRP, flexibilidad y escalabilidad.

## Arquitectura - Back End - BCP  (1)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se debe adjuntar diagrama de arquitectura física indicando los componentes que garantizan alta disponibilidad y DRP.

> **Source:** Adjuntar diagrama de arquitectura física indicando los componentes que garantizan alta disponibilidad y DRP.

## Arquitectura - Back End - Despliegues  (1)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se debe realizar despliegue automático y continuo de la solución (CI/CD) mitigando el impacto en el despliegue de nuevas versiones.

> **Source:** Despliegue automático y continuo de la solución (CI/CD) mitigando el impacto en el despliegue de nuevas versiones..

## Arquitectura - Back End - Diagrama  (1)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se debe adjuntar diagrama lógico de la arquitectura de la solución (Aplicación, componentes arquitectónicos, APIs, entre otros).

> **Source:** Adjuntar diagrama lógico de la arquitectura de la solución ( Aplicación, componentes arquitectónicos, APIs, entre otros).

## Arquitectura- Front End  (17)

### ✓ 1  · conf 0.98 p.3
**Statement:** La aplicación front end debe ser nativa en Android ejecutándose en versiones Android 13 y superiores.

> **Source:** Aplicación nativa en Android ejecutándose en versiones Android 13 y superiores.

### ✓ 2  · conf 0.98 p.3
**Statement:** Se debe garantizar la homologación con nuevas versiones del sistema operativo Android evitando la obsolescencia tecnológica.

> **Source:** Se debe garantizar la homologación con nuevas versiones del sistema operativo (Android) evitando la obsolescencia tecnológica.

### ✓ 3  · conf 0.97 p.3
**Statement:** La actualización periódica para evitar obsolescencia debe estar incluida en la propuesta en el costo del SaaS o en el desarrollo a la medida.

> **Source:** La actualización periódica para evitar obsolescencia debe estar incluida en la propuesta en el costo del SaaS  o en el desarrollo a la medida.

### ✓ 4  · conf 0.97 p.3
**Statement:** La aplicación debe estar optimizada para un funcionamiento online / offline.

> **Source:** Aplicación optimizada para un funcionamiento "online / offline".

### ✓ 5  · conf 0.97 p.3
**Statement:** Si hay necesidad de almacenar información en el dispositivo, todos los datos almacenados en el dispositivo deben estar cifrados.

> **Source:** Si hay necesidad de almacenar información en el dispositivo, todos los datos almacenados en el dispositivo deben estar cifrados.

### ✓ 6  · conf 0.97 p.3
**Statement:** La aplicación debe garantizar solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC).

> **Source:** La aplicación debe garantizar solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC)..

### ✓ 7  · conf 0.95 p.3
**Statement:** La aplicación debe permitir la integración con servicios para la autenticación de usuarios con el DA.

> **Source:** La aplicación debe permitir la integración con servicios para la autenticación de usuarios. Con el DA.

### ✓ 8  · conf 0.97 p.3
**Statement:** La aplicación deberá garantizar la autenticación al momento de usar los diferentes servicios WEB haciendo uso de tokens de Acceso Seguro.

> **Source:** La aplicación deberá garantizar la autenticación al momento de usar los diferentes servicios WEB haciendo uso de tokens de Acceso Seguro y garantizar su renovación..

### ✓ 9  · conf 0.96 p.3
**Statement:** La aplicación deberá garantizar la renovación de los tokens de Acceso Seguro.

> **Source:** La aplicación deberá garantizar la autenticación al momento de usar los diferentes servicios WEB haciendo uso de tokens de Acceso Seguro y garantizar su renovación..

### ✓ 10  · conf 0.97 p.3
**Statement:** La aplicación debe permitir integración con servicios OTP.

> **Source:** La aplicación debe permitir integración con servicios OTP.

### ✓ 11  · conf 0.95 p.3
**Statement:** Se debe implementar validación de entrada de datos por el usuario, previniendo inyecciones de SQL, XSS, actualizaciones de librerías.

> **Source:** Validación de entrada de datos por el usuario, previniendo inyecciones de  SQL, XSS, actualizaciones de de librerías,.

### ✓ 12  · conf 0.97 p.3
**Statement:** Se debe implementar reglas contra ingeniería inversa.

> **Source:** Implementación de reglas contra ingeniería inversa..

### ✓ 13  · conf 0.97 p.3
**Statement:** La aplicación front end no debe ejecutar reglas de negocio, se debe garantizar que estas estén del lado del servidor.

> **Source:** La aplicación no debe ejecutar reglas de negocio, se debe garantizar que estas estén del lado del servidor..

### ✓ 14  · conf 0.97 p.3
**Statement:** La aplicación debe estar disponible en la Play Store.

> **Source:** La aplicación debe estar disponible en la Play Store y se debe permitir actualizarse automáticamente (ejemplo en el Splash Screen).

### ✓ 15  · conf 0.96 p.3
**Statement:** La aplicación debe permitir actualizarse automáticamente (ejemplo en el Splash Screen).

> **Source:** La aplicación debe estar disponible en la Play Store y se debe permitir actualizarse automáticamente (ejemplo en el Splash Screen).

### ✓ 16  · conf 0.97 p.3
**Statement:** La aplicación debe tener drivers para leer códigos de barra lineal garantizando mínimamente Code-128 y Code-39.

> **Source:** La aplicación debe tener drivers para leer códigos de barra lineal (garantizando mínimamente Code-128, Code-39) y 2D ( garantizando mínimamente Data Matrix  y QR).

### ✓ 17  · conf 0.97 p.3
**Statement:** La aplicación debe tener drivers para leer códigos de barra 2D garantizando mínimamente Data Matrix y QR.

> **Source:** La aplicación debe tener drivers para leer códigos de barra lineal (garantizando mínimamente Code-128, Code-39) y 2D ( garantizando mínimamente Data Matrix  y QR).

## Ciberseguridad - Gestión de vulnerabilidades  (2)

### ✓ 1  · conf 0.93 p.3
**Statement:** Se debe compartir procesos y lineamientos para la gestión y remediación de vulnerabilidades.

> **Source:** Compartir procesos y lineamiento para la gestión y remediación de vulnerabilidades. En caso de nos ser un SaaS, la propuesta económica debe incluir la intervención de las vulnerabilidad.

### ✓ 2  · conf 0.94 p.3
**Statement:** En caso de no ser un SaaS, la propuesta económica debe incluir la intervención de las vulnerabilidades.

> **Source:** Compartir procesos y lineamiento para la gestión y remediación de vulnerabilidades. En caso de nos ser un SaaS, la propuesta económica debe incluir la intervención de las vulnerabilidad.

## Ciberseguridad - Requerimientos generales de Ciberseguridad  (27)

### ✓ 1  · conf 0.94 p.3
**Statement:** El aliado o proveedor cuenta con marcos de referencia para apoyar la Gestión de la Ciberseguridad al interior de su compañía, como: ISO 27001, ISO 27032 o NIST CS, entre otros.

> **Source:** El aliado o proveedor cuenta con marcos de referencia para apoyar la Gestión de la Ciberseguridad al interior de su compañía, como: ISO 27001, ISO 27032 o NIST CS, entre otros.

### ✓ 2  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar y evidenciar que incorpora en sus sistemas de información mecanismos de múltiple factor de autenticación (MFA).

> **Source:** El aliado o proveedor debe garantizar y evidenciar que incorpora en sus sistemas de información mecanismos de múltiple factor de autenticación (MFA)..

### ✓ 3  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar que toda la información almacenada del Grupo Logístico TCC cumple con los principios de disponibilidad, integridad y confidencialidad.

> **Source:** El aliado o proveedor debe garantizar que toda la información almacenada del Grupo Logístico TCC cumple con los principios de disponibilidad, integridad y confidencialidad. Se debe adjuntar evidencia..

### ✓ 4  · conf 0.95 p.3
**Statement:** Se debe adjuntar evidencia del cumplimiento de los principios de disponibilidad, integridad y confidencialidad de la información almacenada del Grupo Logístico TCC.

> **Source:** El aliado o proveedor debe garantizar que toda la información almacenada del Grupo Logístico TCC cumple con los principios de disponibilidad, integridad y confidencialidad. Se debe adjuntar evidencia..

### ✓ 5  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar que los procesos sostenidos del Intercambio de Información o archivos con la empresa se realice mediante mecanismos seguros como SFTP, FTPS.

> **Source:** El aliado o proveedor debe garantizar que los procesos sostenidos del Intercambio de Información o archivos con la empresa se realice mediante mecanismos seguros como SFTP, FTPS..

### ✓ 6  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe permitir pruebas de ciberseguridad en el servicio ofrecido.

> **Source:** El aliado o proveedor debe permitir  pruebas de ciberseguridad en el servicio ofrecido..

### ✓ 7  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar que cuenta con una estructura de gobierno para gestionar los temas de Ciberseguridad.

> **Source:** El aliado o proveedor debe garantizar que cuenta con una estructura de gobierno para gestionar los temas de Ciberseguridad. Se debe adjuntar evidencia..

### ✓ 8  · conf 0.90 p.3
**Statement:** Se debe adjuntar evidencia de la estructura de gobierno para gestionar los temas de Ciberseguridad.

> **Source:** El aliado o proveedor debe garantizar que cuenta con una estructura de gobierno para gestionar los temas de Ciberseguridad. Se debe adjuntar evidencia..

### ✓ 9  · conf 0.95 p.3
**Statement:** El aliado o proveedor garantiza y evidencia que el servicio a prestar incluye procedimientos y tecnologías que aseguran la alta disponibilidad y continuidad de la operación de los servicios del Grupo Logístico TCC.

> **Source:** El aliado o proveedor garantiza y evidencia que el servicio a prestar incluye procedimientos y tecnologías que aseguran la alta disponibilidad y continuidad de la operación de los servicios del Grupo Logístico TCC. La evidencia debe ser presentada anualmente..

### ✓ 10  · conf 0.95 p.3 · NOISE
**Statement:** La evidencia de alta disponibilidad y continuidad debe ser presentada anualmente.

> **Source:** La evidencia debe ser presentada anualmente..

### ✓ 11  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe disponer a la finalización del contrato que cuenta con los procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios con el Grupo Logístico TCC.

> **Source:** El aliado o proveedor debe disponer a la finalización del contrato que cuenta con los procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios con el Grupo Logístico TCC..

### ✓ 12  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar Backup o copias de respaldo (indicando el tiempo de resguardo y periodicidad de las copias).

> **Source:** El aliado o proveedor debe garantizar Backup o copias de respaldo (indicando el tiempo de resguardo y periodicidad de las copias). Aplica para los aliados o proveedore que cuenten con almacenamiento de información del Grupo Logístico TCC..

### ✓ 13  · conf 0.90 p.3
**Statement:** El requisito de Backup o copias de respaldo aplica para los aliados o proveedores que cuenten con almacenamiento de información del Grupo Logístico TCC.

> **Source:** Aplica para los aliados o proveedore que cuenten con almacenamiento de información del Grupo Logístico TCC..

### ✓ 14  · conf 0.90 p.3
**Statement:** El aliado o proveedor debe estar en la capacidad de cumplir con una cláusula sobre la gestión y notificación de información ante Incidentes de Ciberseguridad que puedan afectar el servicio y la información del Grupo Logístico TCC.

> **Source:** 1. Cláusula sobre la gestión, notificación de información ante Incidentes de Ciberseguridad que puedan afectar el servicio y la información del Grupo Logístico TCC.

### ✓ 15  · conf 0.90 p.3
**Statement:** El aliado o proveedor debe estar en la capacidad de cumplir con una cláusula que posibilite la auditabilidad hacia el servicio ofrecido por el proveedor.

> **Source:** 2. Cláusula que posibilite la auditabilidad hacia el servicio ofrecido por el proveedor. Anexar clausulas.

### ✓ 16  · conf 0.85 p.3 · NOISE
**Statement:** Se deben anexar las cláusulas contractuales sobre gestión de incidentes y auditabilidad.

> **Source:** Anexar clausulas.

### ✓ 17  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe cumplir con los lineamientos de Ciberseguridad del Grupo Logístico TCC.

> **Source:** El aliado o proveedor debe cumplir con los lineamientos de Ciberseguridad del Grupo Logístico TCC. Anexar lineamientos de TCC.

### ✓ 18  · conf 0.85 p.3
**Statement:** Se deben anexar los lineamientos de Ciberseguridad de TCC.

> **Source:** Anexar lineamientos de TCC.

### ✓ 19  · conf 0.90 p.3
**Statement:** El aliado o proveedor debe suministrar evidencia que incorpora los siguientes sistemas de protección básicos en su organización.

> **Source:** El aliado o proveedor debe suministrar evidencia que incorpora los siguientes sistemas de protección básicos en su organización.1. Protección endpoint2. Protección de seguridad perimetral3. Seguridad correo electrónico4. Gestión de actualización y parches en su infraestructura TI5. Seguridad red corporativaNota: Toda evidencia debe ser presentada anualmente..

### ✓ 20  · conf 0.85 p.3
**Statement:** El aliado o proveedor debe suministrar evidencia que incorpora Protección endpoint.

> **Source:** El aliado o proveedor debe suministrar evidencia que incorpora los siguientes sistemas de protección básicos en su organización.1. Protección endpoint2. Protección de seguridad perimetral3. Seguridad correo electrónico4. Gestión de actualización y parches en su infraestructura TI5. Seguridad red corporativaNota: Toda evidencia debe ser presentada anualmente..

### ✓ 21  · conf 0.85 p.3
**Statement:** El aliado o proveedor debe suministrar evidencia que incorpora Protección de seguridad perimetral.

> **Source:** El aliado o proveedor debe suministrar evidencia que incorpora los siguientes sistemas de protección básicos en su organización.1. Protección endpoint2. Protección de seguridad perimetral3. Seguridad correo electrónico4. Gestión de actualización y parches en su infraestructura TI5. Seguridad red corporativaNota: Toda evidencia debe ser presentada anualmente..

### ✓ 22  · conf 0.85 p.3
**Statement:** El aliado o proveedor debe suministrar evidencia que incorpora Seguridad de correo electrónico.

> **Source:** El aliado o proveedor debe suministrar evidencia que incorpora los siguientes sistemas de protección básicos en su organización.1. Protección endpoint2. Protección de seguridad perimetral3. Seguridad correo electrónico4. Gestión de actualización y parches en su infraestructura TI5. Seguridad red corporativaNota: Toda evidencia debe ser presentada anualmente..

### ✓ 23  · conf 0.85 p.3
**Statement:** El aliado o proveedor debe suministrar evidencia que incorpora Gestión de actualización y parches en su infraestructura TI.

> **Source:** El aliado o proveedor debe suministrar evidencia que incorpora los siguientes sistemas de protección básicos en su organización.1. Protección endpoint2. Protección de seguridad perimetral3. Seguridad correo electrónico4. Gestión de actualización y parches en su infraestructura TI5. Seguridad red corporativaNota: Toda evidencia debe ser presentada anualmente..

### ✓ 24  · conf 0.85 p.3
**Statement:** El aliado o proveedor debe suministrar evidencia que incorpora Seguridad de red corporativa.

> **Source:** El aliado o proveedor debe suministrar evidencia que incorpora los siguientes sistemas de protección básicos en su organización.1. Protección endpoint2. Protección de seguridad perimetral3. Seguridad correo electrónico4. Gestión de actualización y parches en su infraestructura TI5. Seguridad red corporativaNota: Toda evidencia debe ser presentada anualmente..

### ✓ 25  · conf 0.95 p.3 · NOISE
**Statement:** Toda la evidencia de los sistemas de protección básicos debe ser presentada anualmente.

> **Source:** Nota: Toda evidencia debe ser presentada anualmente..

### ✓ 26  · conf 0.90 p.3
**Statement:** El aliado o proveedor se compromete a la gestión de vulnerabilidades presentadas en su servicio.

> **Source:** El aliado o proveedor se compromete a la gestión de vulnerabilidades presentadas en su servicio, cumplimiento con nuestros ANS para gestión de remediación según la severidad de las vulnerabilidades, encontradas en los servicios tecnológicos ofrecidos:.

### ✓ 27  · conf 0.90 p.3
**Statement:** El aliado o proveedor se compromete al cumplimiento con los ANS para gestión de remediación según la severidad de las vulnerabilidades encontradas en los servicios tecnológicos ofrecidos.

> **Source:** El aliado o proveedor se compromete a la gestión de vulnerabilidades presentadas en su servicio, cumplimiento con nuestros ANS para gestión de remediación según la severidad de las vulnerabilidades, encontradas en los servicios tecnológicos ofrecidos:.

## Ciberseguridad - Requerimientos: Desarrollo Externo de software y sitios WEB  (11)

### ✓ 1  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe contar con lineamientos para el Desarrollo Seguro de Software y sitios WEB.

> **Source:** El aliado o proveedor debe contar con lineamientos para el Desarrollo Seguro de Software y sitios WEB. Anexar evidencia.

### ✓ 2  · conf 0.85 p.3
**Statement:** Se debe anexar evidencia de los lineamientos para el Desarrollo Seguro de Software y sitios WEB.

> **Source:** El aliado o proveedor debe contar con lineamientos para el Desarrollo Seguro de Software y sitios WEB. Anexar evidencia.

### ✓ 3  · conf 0.95 p.3
**Statement:** El aliado o proveedor garantiza incorporar en el desarrollo mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0.

> **Source:** El aliado o proveedor garantiza incorporar en el desarrollo mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0. Anexar evidencia.

### ✓ 4  · conf 0.85 p.3
**Statement:** Se debe anexar evidencia de los mecanismos de múltiple factor de autenticación (MFA) incorporados en el desarrollo.

> **Source:** El aliado o proveedor garantiza incorporar en el desarrollo mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0. Anexar evidencia.

### ✓ 5  · conf 0.90 p.3
**Statement:** El aliado o proveedor debe suministrar previo a la implementación y antes de cualquier liberación de nuevas versiones de software, los resultados generales y la gestión de la remediación realizada de pruebas de tipo SAST, IAST y DAST.

> **Source:** 1. Pruebas de tipo (SAST, IAST y DAST )

### ✓ 6  · conf 0.90 p.3
**Statement:** El aliado o proveedor debe suministrar previo a la implementación y antes de cualquier liberación de nuevas versiones de software, los resultados generales y la gestión de la remediación realizada de Pruebas de Penetración o Ética Hacking (caja negra o caja gris según corresponda).

> **Source:** 2. Pruebas de Penetración o Ética Hacking (caja negra o caja gris según corresponda)

### ✓ 7  · conf 0.90 p.3
**Statement:** El aliado o proveedor debe suministrar previo a la implementación y antes de cualquier liberación de nuevas versiones de software, los resultados generales y la gestión de la remediación realizada de Pruebas de Vulnerabilidades Tecnológicas.

> **Source:** 3. Pruebas de Vulnerabilidades Tecnológicas.

### ✓ 8  · conf 0.95 p.3
**Statement:** El aliado o proveedor garantiza que se basa en los principios y buenas prácticas del desarrollo seguro establecidas por OWASP.

> **Source:** El aliado o proveedor garantiza que se basa en los principios y buenas prácticas del desarrollo seguro establecidas por OWASP. Anexar evidencia.

### ✓ 9  · conf 0.85 p.3
**Statement:** Se debe anexar evidencia del cumplimiento de los principios y buenas prácticas del desarrollo seguro establecidas por OWASP.

> **Source:** El aliado o proveedor garantiza que se basa en los principios y buenas prácticas del desarrollo seguro establecidas por OWASP. Anexar evidencia.

### ✓ 10  · conf 0.95 p.3
**Statement:** El aliado o proveedor garantiza el cumplimiento del ciclo de vida del desarrollo de software (SDLC), que contemple las fases: Requerimientos, diseño, desarrollo, verificación, implementación, mantenimiento y evolución.

> **Source:** El aliado o proveedor garantiza el cumplimiento del ciclo de vida del desarrollo de software (SDLC), que contemple las fases: Requerimientos, diseño, desarrollo, verificación, implementación, mantenimiento y evolución. Anexar evidencia.

### ✓ 11  · conf 0.85 p.3
**Statement:** Se debe anexar evidencia del cumplimiento del ciclo de vida del desarrollo de software (SDLC).

> **Source:** El aliado o proveedor garantiza el cumplimiento del ciclo de vida del desarrollo de software (SDLC), que contemple las fases: Requerimientos, diseño, desarrollo, verificación, implementación, mantenimiento y evolución. Anexar evidencia.

## Ciberseguridad - Requerimientos: Servicio pruebas de Ciberseguridad  (5)

### ✓ 1  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar que el personal a cargo de las pruebas (Interno o externo) cuenta con certificaciones de industria actualizadas que avalen su conocimiento y experiencia en la ejecución de pruebas de seguridad (CEH, CISP, CPTE), entre otras que apliquen.

> **Source:** El aliado o proveedor debe garantizar que el personal a cargo de las pruebas (Interno o externo) cuenta con certificaciones de industria actualizadas que avalen su conocimiento y experiencia en la ejecución de pruebas de seguridad (CEH, CISP, CPTE), entre otras que apliquen. Anexar certificación.

### ✓ 2  · conf 0.85 p.3
**Statement:** Se debe anexar la certificación del personal a cargo de las pruebas de seguridad.

> **Source:** Anexar certificación.

### ✓ 3  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar la ejecución de retest para verificación del cierre de los hallazgos encontrados en las pruebas y análisis ejecutados.

> **Source:** El aliado o proveedor debe garantizar la ejecución de retest para verificación del cierre de los hallazgos encontrados en las pruebas y análisis ejecutados..

### ✓ 4  · conf 0.90 p.3
**Statement:** El aliado o proveedor debe disponer a la finalización del contrato de procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios de pruebas de Ciberseguridad con el Grupo Logístico TCC.

> **Source:** El aliado o proveedor debe disponer a la finalización del contrato que cuenta con los procedimientos para eliminación y borrado seguro de datos e información recolectada durante la prestación de servicios con el Grupo Logístico TCC. Anexar procedimiento.

### ✓ 5  · conf 0.85 p.3 · NOISE
**Statement:** Se debe anexar el procedimiento de eliminación y borrado seguro de datos e información.

> **Source:** Anexar procedimiento.

## Ciberseguridad - Requerimientos: Servicios en nube  (26)

### ✓ 1  · conf 0.95 p.3
**Statement:** El aliado o proveedor garantiza que dentro del alcance del servicio a prestar se cuenta con conocimiento experto en estándares de Seguridad para incorporación de Tecnologías en Nube, como CSA o similares.

> **Source:** El aliado o proveedor garantiza que dentro del alcance del servicio a prestar se cuenta con conocimiento experto en estándares de Seguridad para incorporación de Tecnologías en Nube, como CSA o similares. Anexar certificaciones correspondiente.

### ✓ 2  · conf 0.85 p.3
**Statement:** Se deben anexar las certificaciones correspondientes de conocimiento experto en estándares de Seguridad para Tecnologías en Nube.

> **Source:** Anexar certificaciones correspondiente.

### ✓ 3  · conf 0.95 p.3
**Statement:** El aliado o proveedor dispone de un lineamiento o procedimiento para reportar la ocurrencia de Incidentes de ciberseguridad que se puedan presentar sobre la información del Grupo Logístico TCC almacenada en la nube administrada por el proveedor.

> **Source:** El aliado o proveedor dispone de un lineamiento o procedimiento para reportar la ocurrencia de Incidentes de ciberseguridad que se puedan presentar sobre la información del Grupo Logístico TCC almacenada en la nube administrada por el proveedor. Anexar lineamientos.

### ✓ 4  · conf 0.85 p.3
**Statement:** Se deben anexar los lineamientos o procedimientos para reportar Incidentes de ciberseguridad sobre la información del Grupo Logístico TCC en la nube.

> **Source:** El aliado o proveedor dispone de un lineamiento o procedimiento para reportar la ocurrencia de Incidentes de ciberseguridad que se puedan presentar sobre la información del Grupo Logístico TCC almacenada en la nube administrada por el proveedor. Anexar lineamientos.

### ✓ 5  · conf 0.85 p.3
**Statement:** El aliado o proveedor garantiza que cuenta con la protección para Seguridad en la Ejecución y Protección Contra Manipulación de Datos.

> **Source:** 1.Manipulación de Datos

### ✓ 6  · conf 0.85 p.3
**Statement:** El aliado o proveedor garantiza que cuenta con Prevención de Exfiltración de Información.

> **Source:** 2Prevención de Exfiltración de Información

### ✓ 7  · conf 0.85 p.3
**Statement:** El aliado o proveedor garantiza que cuenta con Protección Contra Escalamiento de Privilegios.

> **Source:** 3.Protección Contra Escalamiento de Privilegios.

### ✓ 8  · conf 0.85 p.3
**Statement:** El aliado o proveedor garantiza que cuenta con Administración y definición de roles y perfiles.

> **Source:** 4.Administración definición de roles y perfiles..

### ✓ 9  · conf 0.80 p.3 · NOISE
**Statement:** Se debe anexar evidencia de la protección para Seguridad en la Ejecución.

> **Source:** anexar evidencia

### ✓ 10  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar la restricción de conexiones por Geolocalización.

> **Source:** El aliado o proveedor debe garantizar la restricción de conexiones por Geolocalización. Anexar evidencia o explicar como se garantiza.

### ✓ 11  · conf 0.85 p.3
**Statement:** Se debe anexar evidencia o explicar cómo se garantiza la restricción de conexiones por Geolocalización.

> **Source:** El aliado o proveedor debe garantizar la restricción de conexiones por Geolocalización. Anexar evidencia o explicar como se garantiza.

### ✓ 12  · conf 0.95 p.3
**Statement:** El aliado o proveedor debe garantizar escalabilidad: Actualizaciones y versionamiento.

> **Source:** El aliado o proveedor debe garantizar escalabilidad: Actualizaciones y versionamiento. Anexar evidencia o explicar como se garantiza.

### ✓ 13  · conf 0.85 p.3
**Statement:** Se debe anexar evidencia o explicar cómo se garantiza la escalabilidad (Actualizaciones y versionamiento).

> **Source:** El aliado o proveedor debe garantizar escalabilidad: Actualizaciones y versionamiento. Anexar evidencia o explicar como se garantiza.

### ✓ 14  · conf 0.95 p.3
**Statement:** El aliado o proveedor garantiza que el servicio ofrecido incorpora mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0.

> **Source:** El aliado o proveedor garantiza que el servicio ofrecido incorpora mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0. Anexar evidencia o explicar como se garantiza.

### ✓ 15  · conf 0.85 p.3
**Statement:** Se debe anexar evidencia o explicar cómo se garantiza la incorporación de mecanismos de MFA en el servicio ofrecido.

> **Source:** El aliado o proveedor garantiza que el servicio ofrecido incorpora mecanismos de múltiple factor de autenticación (MFA), como: TOKEN, CLAVE API o OAUTH 2.0. Anexar evidencia o explicar como se garantiza.

### ✓ 16  · conf 0.85 p.3
**Statement:** El aliado o proveedor dispone de personal certificado en Profesional de Seguridad en la Nube Certificado (CCSP) de (ISC)2.

> **Source:** 1. Profesional de Seguridad en la Nube Certificado (CCSP) de (ISC)2

### ✓ 17  · conf 0.85 p.3
**Statement:** El aliado o proveedor dispone de personal certificado en Certificado de Conocimientos de Seguridad en la Nube (CCSK) de Cloud Security Alliance.

> **Source:** 2. Certificado de Conocimientos de Seguridad en la Nube (CCSK) de Cloud Security Alliance

### ✓ 18  · conf 0.85 p.3
**Statement:** El aliado o proveedor dispone de personal certificado en Especialista Certificado en Seguridad en la Nube (CCSS) del Foro Global de Ciencia y Tecnología.

> **Source:** 3. Especialista Certificado en Seguridad en la Nube (CCSS) del Foro Global de Ciencia y Tecnología

### ✓ 19  · conf 0.85 p.3 · NOISE
**Statement:** El aliado o proveedor dispone de personal certificado en Integrador Certificado de Servicios Seguros en la Nube, de EXIN.

> **Source:** 4. Integrador Certificado de Servicios Seguros en la Nube, de EXIN

### ✓ 20  · conf 0.85 p.3
**Statement:** El aliado o proveedor dispone de personal certificado en Fundamentos de Seguridad en la Nube del Instituto SANS (SEC524).

> **Source:** 5. Fundamentos de Seguridad en la Nube del Instituto SANS (SEC524)

### ✓ 21  · conf 0.85 p.3
**Statement:** El aliado o proveedor dispone de personal certificado en Certificación Profesional de Administrador de Seguridad en la Nube (PCS) del Cloud Credential Council.

> **Source:** 6. Certificación Profesional de Administrador de Seguridad en la Nube (PCS) del Cloud Credential Council

### ✓ 22  · conf 0.80 p.3
**Statement:** Se deben anexar las certificaciones del personal en seguridad en la nube.

> **Source:** Anexar certificaciones

### ✓ 23  · conf 0.90 p.3
**Statement:** El aliado o proveedor suministra previo a la implementación en ambiente productivo, los resultados y la gestión de la remediación realizada de Pruebas de servicios cloud.

> **Source:** 1. Pruebas de servicios cloud

### ✓ 24  · conf 0.90 p.3
**Statement:** El aliado o proveedor suministra previo a la implementación en ambiente productivo, los resultados y la gestión de la remediación realizada de Pruebas de servicios API.

> **Source:** 2. Pruebas de servicios API

### ✓ 25  · conf 0.90 p.3
**Statement:** El aliado o proveedor suministra previo a la implementación en ambiente productivo, los resultados y la gestión de la remediación realizada de Pruebas de Penetración o Ethical Hacking (caja negra o caja gris según corresponda).

> **Source:** 3. Pruebas de Penetración o Ethical Hacking (caja negra o caja gris según corresponda)

### ✓ 26  · conf 0.90 p.3
**Statement:** El aliado o proveedor suministra previo a la implementación en ambiente productivo, los resultados y la gestión de la remediación realizada de Pruebas de Vulnerabilidades Tecnológicas.

> **Source:** 4. Pruebas de Vulnerabilidades Tecnológicas.

## Ciberseguridad - Seguridad de la información  (1)

### ✓ 1  · conf 0.94 p.3
**Statement:** Se debe realizar notificación a los usuarios de la aplicación sobre recolección de datos.

> **Source:** Notificación a los usuarios de la aplicación sobre recolección de datos.

## Demo o mockups de aplicación  (1)

### ⚠ 1  · conf 0.95 p.2
**Statement:** El proveedor debe contar con una demo de prueba de la aplicación o en caso de no tener evidenciar mockups de referencia de los distintos flujos.

> **Source:** Demo o mockups de aplicación, Proyecto diseño de aplicación ruta -Req funcionales = Contar con una demo de prueba de la aplicación o en caso de no tener evidenciar mockups de referencia de los distintos flujos.

## Experiencia de usuario  (7)

### ⚠ 1  · conf 0.95 p.2
**Statement:** La aplicación deberá estar diseñada bajo principios de experiencia de usuario (UX) que prioricen la simplicidad, claridad y eficiencia, permitiendo a los usuarios completar tareas clave con el mínimo número de interacciones posibles (pocos clics).

> **Source:** Experiencia de usuario, Proyecto diseño de aplicación ruta -Req funcionales = La aplicación deberá estar diseñada bajo principios de experiencia de usuario (UX) que prioricen la simplicidad, claridad y eficiencia, permitiendo a los usuarios completar tareas clave con el mínimo número de interacciones posibles (pocos clics)..

### ⚠ 2  · conf 0.95 p.2
**Statement:** La aplicación debe tener un diseño centrado en el usuario, con interfaces limpias, jerarquía visual clara y elementos interactivos accesibles.

> **Source:** Experiencia de usuario, Proyecto diseño de aplicación ruta -Req funcionales = Diseño centrado en el usuario, con interfaces limpias, jerarquía visual clara y elementos interactivos accesibles..

### ⚠ 3  · conf 0.95 p.2
**Statement:** La aplicación debe contar con flujos de navegación optimizados, que permitan realizar tareas frecuentes (como cumplir una entrega, una recogida) en máximo 4 pantallas con pocos clics.

> **Source:** Experiencia de usuario, Proyecto diseño de aplicación ruta -Req funcionales = Flujos de navegación optimizados, que permitan realizar tareas frecuentes (como cumplir una entrega, una recogida) en máximo 4 pantallas con pocos clics..

### ⚠ 4  · conf 0.95 p.2
**Statement:** La aplicación debe contar con accesos directos contextuales y botones de acción rápida para tareas recurrentes.

> **Source:** Experiencia de usuario, Proyecto diseño de aplicación ruta -Req funcionales = Accesos directos contextuales y botones de acción rápida para tareas recurrentes..

### ⚠ 5  · conf 0.95 p.2
**Statement:** La aplicación debe proporcionar retroalimentación inmediata al usuario ante cada acción (confirmaciones, errores, estados de carga).

> **Source:** Experiencia de usuario, Proyecto diseño de aplicación ruta -Req funcionales = Retroalimentación inmediata al usuario ante cada acción (confirmaciones, errores, estados de carga)..

### ⚠ 6  · conf 0.95 p.2
**Statement:** La aplicación debe tener compatibilidad móvil con diseño responsivo y gestos táctiles intuitivos.

> **Source:** Experiencia de usuario, Proyecto diseño de aplicación ruta -Req funcionales = Compatibilidad móvil con diseño responsivo y gestos táctiles intuitivos..

### ⚠ 7  · conf 0.95 p.2
**Statement:** La aplicación debe permitir la personalización de vistas según el rol del usuario para mostrar solo la información relevante.

> **Source:** Experiencia de usuario, Proyecto diseño de aplicación ruta -Req funcionales = Personalización de vistas según el rol del usuario para mostrar solo la información relevante..

## Flujo de aplicación / Servicio de recogidas - Ejecucción de servicio de recolección - Creación de servicios  (6)

### ✓ 1  · conf 0.90 p.2 · NOISE
**Statement:** El flujo debe ser intuitivo, con navegación simplificada y validación de campos obligatorios como correo electrónico y número de contacto.

> **Source:** El flujo debe ser intuitivo, con navegación simplificada y validación de campos obligatorios como correo electrónico y número de contacto.

### ✓ 2  · conf 0.95 p.2
**Statement:** El sistema debe consultar automáticamente la base de datos interna para identificar si el remitente o destinatario ya existen.

> **Source:** El sistema debe consultar automáticamente la base de datos interna para identificar si el remitente o destinatario ya existen..

### ✓ 3  · conf 0.90 p.2
**Statement:** En caso afirmativo, debe precargar los siguientes datos: Nombre o razón social, Número de identificación (NIT/CC), Dirección y ciudad, Forma de pago habitual, Cuenta asociada (si aplica), tanto para remitente como para destinatario.

> **Source:** En caso afirmativo, debe precargar los siguientes datos:Nombre o razón social.Número de identificación (NIT/CC).Dirección y ciudad.Forma de pago habitual.Cuenta asociada (si aplica).Esta funcionalidad debe estar disponible tanto para remitente como para destinatario, permitiendo que el usuario solo deba ingresar los datos de la unidad logística, reduciendo tiempos de digitación y errores operativos.

### ✓ 4  · conf 0.90 p.2
**Statement:** Al ingresar un nuevo servicio, la aplicación debe consumir un servicio de integración que devuelva el valor estimado del flete.

> **Source:** Al ingresar un nuevo servicio, la aplicación debe consumir un servicio de integración que devuelva el valor estimado del flete.

### ✓ 5  · conf 0.90 p.2 · NOISE
**Statement:** El cotizador debe considerar variables como origen, destino, tipo de unidad, peso y volumen.

> **Source:** El cotizador debe considerar variables como origen, destino, tipo de unidad, peso y volumen..

### ✓ 6  · conf 0.95 p.2
**Statement:** La interfaz debe estar diseñada para facilitar la creación del servicio en menos de 2 minutos, con campos auto completables, búsqueda predictiva y botones de acción visibles.

> **Source:** La interfaz debe estar diseñada para facilitar la creación del servicio en menos de 2 minutos, con campos auto completables, búsqueda predictiva y botones de acción visibles..

## Flujo de aplicación / Servicio de recogidas - Ejecucción de servicio de recolección - Sincronización de servicios  (3)

### ✓ 1  · conf 0.85 p.2
**Statement:** El flujo de sincronización debe contemplar consulta al servidor central.

> **Source:** El flujo debe contemplar:1)Consulta al servidor central.2)Sincronización de datos al dispositivo.3)Confirmación de sincronización exitosa..

### ✓ 2  · conf 0.85 p.2
**Statement:** El flujo de sincronización debe contemplar sincronización de datos al dispositivo.

> **Source:** El flujo debe contemplar:1)Consulta al servidor central.2)Sincronización de datos al dispositivo.3)Confirmación de sincronización exitosa..

### ✓ 3  · conf 0.85 p.2
**Statement:** El flujo de sincronización debe contemplar confirmación de sincronización exitosa.

> **Source:** El flujo debe contemplar:1)Consulta al servidor central.2)Sincronización de datos al dispositivo.3)Confirmación de sincronización exitosa..

## Flujo de aplicación / Servicio de recogidas - Ejecucción de servicios de recolección - Registro de motivo de no recolección  (3)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir al usuario seleccionar un motivo de no recolección desde un catálogo predefinido, desplegado mediante búsqueda predictiva por palabra clave.

> **Source:** La aplicación debe permitir al usuario seleccionar un motivo de no recolección desde un catálogo predefinido, desplegado mediante búsqueda predictiva por palabra clave.

### ✓ 2  · conf 0.90 p.2
**Statement:** El motivo seleccionado debe quedar asociado al servicio.

> **Source:** El motivo seleccionado debe quedar asociado al servicio y visible en la trazabilidad del mismo.

### ✓ 3  · conf 0.90 p.2
**Statement:** El motivo seleccionado debe quedar visible en la trazabilidad del servicio.

> **Source:** El motivo seleccionado debe quedar asociado al servicio y visible en la trazabilidad del mismo.

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicio de recolección - Conceptos de recaudo  (9)

### ✓ 1  · conf 0.90 p.2
**Statement:** La aplicación debe tener la opción de realizar validaciones del servicio para verificar si tiene conceptos de recaudo de dinero.

> **Source:** La aplicación tenga la opción de realizar validaciones, para este caso realice la validación del servicio si tiene conceptos de recaudo de dinero.

### ✓ 2  · conf 0.90 p.2
**Statement:** La opción de recaudo de dinero debe evidenciar los detalles del cobro, es decir los conceptos de liquidación del servicio.

> **Source:** Opción de recaudo de dinero, que evidencie los detalles del cobro es decir los conceptos de liquidación del servicio.

### ✓ 3  · conf 0.90 p.2
**Statement:** La opción debe permitir el recaudo de dinero con pagos en efectivo o integración con pasarelas de pago para procesar pagos con tarjetas.

> **Source:** Que la opción permita el recaudo de dinero pagos en  Efectivo o  Integración con pasarelas de pago para procesar pagos con tarjetas..

### ✓ 4  · conf 0.90 p.2
**Statement:** La aplicación debe registrar cada transacción relacionando información de pago.

> **Source:** La aplicación registre cada transacción relacionando información de pago e integre con el collection management.

### ✓ 5  · conf 0.90 p.2
**Statement:** La aplicación debe integrar con el collection management.

> **Source:** La aplicación registre cada transacción relacionando información de pago e integre con el collection management.

### ✓ 6  · conf 0.85 p.2
**Statement:** Implementar un sistema de generación de alertas en tiempo real que permita confirmar pagos recibidos.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:1) Confirmar pagos recibidos y detectar pagos pendientes..

### ✓ 7  · conf 0.85 p.2
**Statement:** Implementar un sistema de generación de alertas en tiempo real que permita detectar pagos pendientes.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:1) Confirmar pagos recibidos y detectar pagos pendientes..

### ✓ 8  · conf 0.90 p.2
**Statement:** Implementar un sistema de generación de alertas en tiempo real que permita recibir automáticamente la información del recaudo.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:2) Recibir automáticamente la información del recaudo..

### ✓ 9  · conf 0.90 p.2
**Statement:** Implementar un sistema de generación de alertas en tiempo real que permita notificar de forma inmediata a la aplicación correspondiente sobre el estado del pago.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:3) Notificar de forma inmediata a la aplicación correspondiente sobre el estado del pago..

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicio de recolección - Creación de servicios  (1)

### ✓ 1  · conf 0.90 p.2
**Statement:** El sistema debe permitir la creación de nuevos servicios desde el dispositivo móvil, incluyendo datos del remitente y destinatario, características de las unidades (peso, volumen, tipo de mercancía), captura de evidencia fotográfica y observaciones.

> **Source:** El sistema debe permitir la creación de nuevos servicios desde el dispositivo móvil, incluyendo:Datos del remitente y destinatario.Características de las unidades (peso, volumen, tipo de mercancía).Captura de evidencia fotográfica y observaciones..

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicio de recolección - Inteligencia contextual para escenarios de recolección  (2)

### ✓ 1  · conf 0.90 p.2
**Statement:** La aplicación debe ser capaz de identificar automáticamente el tipo de recolección al escanear una unidad (Recolección programada, Recolección no programada, Recolección de devolución).

> **Source:** La aplicación debe ser capaz de identificar automáticamente el tipo de recolección al escanear una unidad:Recolección programada.Recolección no programada.Recolección de devolución.Según el tipo identificado, debe activar el flujo correspondiente de forma automática..

### ✓ 2  · conf 0.90 p.2 · NOISE
**Statement:** Según el tipo identificado, debe activar el flujo correspondiente de forma automática.

> **Source:** Según el tipo identificado, debe activar el flujo correspondiente de forma automática..

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicio de recolección - Lectura de unidades  (3)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR.

> **Source:** La aplicación debe permitir la lectura automática de unidades asociadas a un servicio de recolección mediante escaneo de códigos de barras o QR..

### ✓ 2  · conf 0.90 p.2
**Statement:** Debe validar que las unidades escaneadas correspondan al servicio asignado.

> **Source:** Debe validar que las unidades escaneadas correspondan al servicio asignado y registrar la hora y ubicación del escaneo..

### ✓ 3  · conf 0.90 p.2
**Statement:** Debe registrar la hora y ubicación del escaneo.

> **Source:** Debe validar que las unidades escaneadas correspondan al servicio asignado y registrar la hora y ubicación del escaneo..

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicio de recolección - Lectura de unidades anónimas  (3)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir escanear unidades que no estén previamente asociadas a un servicio (unidades anónimas).

> **Source:** La aplicación debe permitir escanear unidades que no estén previamente asociadas a un servicio (unidades anónimas)..

### ✓ 2  · conf 0.90 p.2
**Statement:** Al escanear, debe capturar código de la unidad, fecha, hora y ubicación, observaciones del operador.

> **Source:** Al escanear, debe capturar:Código de la unidad.Fecha, hora y ubicación.Observaciones del operador.Estas unidades deben quedar registradas para trazabilidad y posterior asociación..

### ✓ 3  · conf 0.90 p.2
**Statement:** Estas unidades anónimas deben quedar registradas para trazabilidad y posterior asociación.

> **Source:** Al escanear, debe capturar:Código de la unidad.Fecha, hora y ubicación.Observaciones del operador.Estas unidades deben quedar registradas para trazabilidad y posterior asociación..

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicio de recolección - Sincronización de servicios  (2)

### ✓ 1  · conf 0.90 p.2
**Statement:** La aplicación debe permitir sincronizar los servicios asignados por diferentes criterios: por ruta, por zona geográfica, por fecha, por tipo de cliente, por ID de servicio.

> **Source:** La aplicación debe permitir sincronizar los servicios asignados por diferentes criterios:Por ruta, por zona geográfica, por fecha, por tipo de cliente, por ID de servicio.

### ✓ 2  · conf 0.90 p.2
**Statement:** La sincronización debe incluir datos del servicio, información del remitente y estado actual del servicio.

> **Source:** La sincronización debe incluir:Datos del servicio.Información del remitente.Estado actual del servicio..

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicios de recolección - Funcionalidad de seguridad  (3)

### ✓ 1  · conf 0.95 p.2
**Statement:** El sistema debe generar un código de seguridad único por servicio.

> **Source:** El sistema debe generar un código de seguridad único por servicio.

### ✓ 2  · conf 0.90 p.2
**Statement:** El cliente debe validar la recolección ingresando el código de seguridad en el dispositivo del operador, para contrastarlo con el que se le envía desde la planeación del servicio.

> **Source:** El cliente debe validar la recolección ingresando este código en el dispositivo del operador, para contrastarlo con el que se le envía desde la planeación del servicio.

### ✓ 3  · conf 0.95 p.2
**Statement:** Esta validación debe quedar registrada como evidencia de cumplimiento, relacionando usuario, fecha, hora y ID de servicio.

> **Source:** Esta validación debe quedar registrada como evidencia de cumplimiento, relacionando usuario, fecha, hora y ID de servicio.

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicios de recolección - Registro de servicios no programados  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir registrar servicios de recolección no programados durante la ejecución de una ruta u orden de trabajo.

> **Source:** La aplicación debe permitir registrar servicios de recolección no programados durante la ejecución de una ruta u orden de trabajo..

### ✓ 2  · conf 0.90 p.2
**Statement:** Estos servicios no programados deben quedar identificados como 'no programados' y seguir el mismo flujo de validación y sincronización.

> **Source:** Estos servicios deben quedar identificados como “no programados” y seguir el mismo flujo de validación y sincronización..

## Flujo de aplicación / Servicio de recogidas - Ejecución de servicios de recolección - Resumen servicio  (1)

### ✓ 1  · conf 0.90 p.2
**Statement:** La aplicación debe mostrar un resumen en tiempo real del estado de la recolección: total de unidades recolectadas, variables por tipo de unidad/cliente/estado, alertas por inconsistencias o unidades faltantes.

> **Source:** La aplicación debe mostrar un resumen en tiempo real del estado de la recolección:Total de unidades recolectadas.Variables por tipo de unidad, cliente, estado.Alertas por inconsistencias o unidades faltantes..

## Flujo de aplicación / Servicio de recogidas - Pantalla inicio de ejecución de recolección - Validaciones de servicio  (2)

### ✓ 1  · conf 0.90 p.2
**Statement:** Es necesario que realice validaciones antes de ejecutar un servicio tales como: si el servicio tiene una novedad (Motivo de no recolección) activa.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  Si el servicio tiene una novedad (Motivo de no recolección) activa.

### ✓ 2  · conf 0.90 p.2
**Statement:** Es necesario que realice validaciones antes de ejecutar un servicio tales como: franjas horarias para ejecutar el servicio.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  Franjas horarias para ejecutar el servicio.

## Flujo de aplicación/ Servicio de recogidas  (8)

### ✓ 1  · conf 0.95 p.2
**Statement:** Si el servicio ya fue recolectado, debe mostrarse un mensaje informativo.

> **Source:** Si el servicio ya fue recolectado, debe mostrarse un mensaje informativo y bloquear la edición.

### ✓ 2  · conf 0.95 p.2
**Statement:** Si el servicio ya fue recolectado, debe bloquear la edición.

> **Source:** Si el servicio ya fue recolectado, debe mostrarse un mensaje informativo y bloquear la edición.

### ✓ 3  · conf 0.95 p.2
**Statement:** Toda modificación realizada debe quedar registrada en el sistema con: Usuario que realizó la edición, Fecha y hora del cambio, Valores anteriores y nuevos de cada campo modificado.

> **Source:** Toda modificación realizada debe quedar registrada en el sistema con:Usuario que realizó la edición.Fecha y hora del cambio.Valores anteriores y nuevos de cada campo modificado.

### ✓ 4  · conf 0.95 p.2
**Statement:** Esta trazabilidad debe estar disponible para consulta desde el módulo de auditoría o trazabilidad del servicio.

> **Source:** Esta trazabilidad debe estar disponible para consulta desde el módulo de auditoría o trazabilidad del servicio.

### ✓ 5  · conf 0.95 p.2
**Statement:** El sistema debe recalcular automáticamente las condiciones del servicio, incluyendo la liquidación si aplica.

> **Source:** Recalcular automáticamente las condiciones del servicio, incluyendo la liquidación si aplica.

### ✓ 6  · conf 0.95 p.2
**Statement:** El sistema debe actualizar la información en todos los módulos relacionados (por ejemplo, trazabilidad, módulo de cliente, módulo de liquidación).

> **Source:** Actualizar la información en todos los módulos relacionados (por ejemplo, trazabilidad, módulo de cliente, módulo de liquidación).

### ✓ 7  · conf 0.95 p.2
**Statement:** El sistema debe sincronizar los cambios con el servidor central en tiempo real o en cuanto haya conectividad.

> **Source:** Sincronizar los cambios con el servidor central en tiempo real o en cuanto haya conectividad.

### ✓ 8  · conf 0.90 p.2
**Statement:** Debe existir una pantalla donde se evidencie la culminación exitosa, y evidencie opciones de navegar a la próxima parada, ver la próxima parada, e ir a servicios del día (Ir a ruta).

> **Source:** Pantalla donde se evidencie la culminación exitosa, y evidencie opciones de navegar a la próxima parada, ver la próxima parada, e ir a servicios del día (Ir a ruta).

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección- Edición de Características del Servicio de Recolección- Funcionalidad de Edición de Servicio  (5)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir la edición de las características del servicio de recolección únicamente si el servicio aún no ha sido recolectado.

> **Source:** La aplicación debe permitir la edición de las características del servicio de recolección únicamente si el servicio aún no ha sido recolectado.

### ✓ 2  · conf 0.95 p.2
**Statement:** Las características editables incluyen la cantidad de unidades.

> **Source:** Las características editables incluyen:Cantidad de unidades.Dimensiones de las unidades: alto, largo, ancho.Peso estimado (si aplica).

### ✓ 3  · conf 0.95 p.2
**Statement:** Las características editables incluyen las dimensiones de las unidades: alto, largo, ancho.

> **Source:** Las características editables incluyen:Cantidad de unidades.Dimensiones de las unidades: alto, largo, ancho.Peso estimado (si aplica).

### ✓ 4  · conf 0.95 p.2 · NOISE
**Statement:** Las características editables incluyen el peso estimado (si aplica).

> **Source:** Las características editables incluyen:Cantidad de unidades.Dimensiones de las unidades: alto, largo, ancho.Peso estimado (si aplica).

### ✓ 5  · conf 0.95 p.2
**Statement:** La edición debe estar disponible desde la vista detallada del servicio, con validación previa del estado operativo del mismo.

> **Source:** La edición debe estar disponible desde la vista detallada del servicio, con validación previa del estado operativo del mismo.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección- Edición de Características del Servicio de Recolección- Validación de Estado del Servicio  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio no haya sido marcado como recolectado.

> **Source:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio:No haya sido marcado como recolectado.No esté en estado de cierre de ruta o liquidación.

### ✓ 2  · conf 0.95 p.2
**Statement:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio no esté en estado de cierre de ruta o liquidación.

> **Source:** Antes de permitir cualquier modificación, el sistema debe validar que el servicio:No haya sido marcado como recolectado.No esté en estado de cierre de ruta o liquidación.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Certificación de recolección  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** En caso de no contar con conexión a red, la aplicación debe almacenar esta información localmente.

> **Source:** En caso de no contar con conexión a red, la aplicación debe almacenar esta información localmente y sincronizarla con el servidor una vez se restablezca la conectividad.

### ✓ 2  · conf 0.95 p.2
**Statement:** La aplicación debe sincronizar la información con el servidor una vez se restablezca la conectividad.

> **Source:** En caso de no contar con conexión a red, la aplicación debe almacenar esta información localmente y sincronizarla con el servidor una vez se restablezca la conectividad.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Certificación de recolección- Captura de Datos de Geolocalización y Temporalidad  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** El sistema debe registrar automáticamente la latitud y longitud del dispositivo al momento de la certificación.

> **Source:** El sistema debe registrar automáticamente los siguientes datos al momento de la certificación:Latitud y longitud del dispositivo.Fecha y hora exacta del evento.

### ✓ 2  · conf 0.95 p.2
**Statement:** El sistema debe registrar automáticamente la fecha y hora exacta del evento al momento de la certificación.

> **Source:** El sistema debe registrar automáticamente los siguientes datos al momento de la certificación:Latitud y longitud del dispositivo.Fecha y hora exacta del evento.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Certificación de recolección- Cumplimiento y Certificación de Recogida  (6)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de evidencia fotográfica del paquete o entorno.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:1)Evidencia fotográfica del paquete o entorno.

### ✓ 2  · conf 0.95 p.2
**Statement:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de firma digital del remitente o responsable de entrega.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:2)Firma digital del remitente o responsable de entrega.

### ✓ 3  · conf 0.95 p.2
**Statement:** La aplicación debe permitir certificar la ejecución de una recogida mediante el registro del nombre completo, número de documento de identidad y número telefónico de la persona que entrega.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:3)Registro del nombre completo, número de documento de identidad y número telefónico de la persona que entrega.

### ✓ 4  · conf 0.95 p.2
**Statement:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de un campo de texto libre para observaciones adicionales.

> **Source:** La aplicación debe permitir certificar la ejecución de una recogida mediante la captura de los siguientes elementos:4)Campo de texto libre para observaciones adicionales.

### ✓ 5  · conf 0.90 p.2
**Statement:** Esta información debe quedar asociada al número de servicio de recogida.

> **Source:** Esta información debe quedar asociada al número de servicio de recogida y almacenarse de forma segura en el sistema.

### ✓ 6  · conf 0.90 p.2
**Statement:** Esta información debe almacenarse de forma segura en el sistema.

> **Source:** Esta información debe quedar asociada al número de servicio de recogida y almacenarse de forma segura en el sistema.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Certificación de recolección- Identificación del Punto de Parada  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe indicar si la certificación se realizó en línea (punto de parada programado) o fuera de línea (punto no programado).

> **Source:** La aplicación debe indicar si la certificación se realizó en línea (punto de parada programado) o fuera de línea (punto no programado).

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Certificación de recolección- Trazabilidad y Consulta  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** Toda la información capturada debe estar disponible para consulta en el módulo de trazabilidad de servicios desde los sistemas fuente.

> **Source:** Toda la información capturada debe estar disponible para consulta en el módulo de trazabilidad de servicios desde los sistemas fuente.

### ✓ 2  · conf 0.95 p.2
**Statement:** La información debe poder exportarse como parte del comprobante digital de recogida, incluyendo firma, fotos, geolocalización y observaciones.

> **Source:** Debe poder exportarse como parte del comprobante digital de recogida, incluyendo firma, fotos, geolocalización y observaciones.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Generación de Comprobante Digital (POC)- Generación del Servicio con Información del Comprobante Digital  (5)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir la creación de un servicio de recogida que incluya automáticamente la generación del comprobante digital (POC).

> **Source:** La aplicación debe permitir la creación de un servicio de recogida que incluya automáticamente la generación del comprobante digital (POC).

### ✓ 2  · conf 0.95 p.2
**Statement:** El POC debe contener como dato mínimo el número de servicio o guía.

> **Source:** El POC debe contener los siguientes datos mínimos:Número de servicio o guía.Fecha y hora de la recogida.Datos del remitente y del punto de origen.Firma digital, fotografías, observaciones y validaciones de seguridad (como código de verificación).

### ✓ 3  · conf 0.95 p.2
**Statement:** El POC debe contener como dato mínimo la fecha y hora de la recogida.

> **Source:** El POC debe contener los siguientes datos mínimos:Número de servicio o guía.Fecha y hora de la recogida.Datos del remitente y del punto de origen.Firma digital, fotografías, observaciones y validaciones de seguridad (como código de verificación).

### ✓ 4  · conf 0.95 p.2
**Statement:** El POC debe contener como dato mínimo los datos del remitente y del punto de origen.

> **Source:** El POC debe contener los siguientes datos mínimos:Número de servicio o guía.Fecha y hora de la recogida.Datos del remitente y del punto de origen.Firma digital, fotografías, observaciones y validaciones de seguridad (como código de verificación).

### ✓ 5  · conf 0.95 p.2
**Statement:** El POC debe contener como dato mínimo la firma digital, fotografías, observaciones y validaciones de seguridad (como código de verificación).

> **Source:** El POC debe contener los siguientes datos mínimos:Número de servicio o guía.Fecha y hora de la recogida.Datos del remitente y del punto de origen.Firma digital, fotografías, observaciones y validaciones de seguridad (como código de verificación).

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Generación de Comprobante Digital (POC)- Integración del POC con Sistemas de Información  (4)

### ✓ 1  · conf 0.95 p.2
**Statement:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo la consulta desde el módulo de trazabilidad de servicios.

> **Source:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo:Consulta desde el módulo de trazabilidad de servicios.

### ✓ 2  · conf 0.95 p.2
**Statement:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo la visualización desde el portal web para clientes (remitente y destinatario).

> **Source:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo:Visualización desde el portal web para clientes (remitente y destinatario).

### ✓ 3  · conf 0.95 p.2
**Statement:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo la asociación con el expediente digital del servicio.

> **Source:** El comprobante digital debe integrarse con los sistemas centrales de información de la organización, permitiendo:Asociación con el expediente digital del servicio.

### ✓ 4  · conf 0.95 p.2
**Statement:** La integración debe contemplar interoperabilidad con sistemas de terceros si aplica (por ejemplo, plataformas regulatorias o de cumplimiento).

> **Source:** La integración debe contemplar interoperabilidad con sistemas de terceros si aplica (por ejemplo, plataformas regulatorias o de cumplimiento).

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Generación de Comprobante Digital (POC)- Notificación del POC al Cliente Remitente  (5)

### ✓ 1  · conf 0.95 p.2
**Statement:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de correo electrónico con enlace al comprobante digital.

> **Source:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de:Correo electrónico con enlace al comprobante digital.Mensaje de texto (SMS) con resumen y enlace de consulta.

### ✓ 2  · conf 0.95 p.2
**Statement:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de mensaje de texto (SMS) con resumen y enlace de consulta.

> **Source:** Una vez generado el POC, el sistema debe enviar automáticamente una notificación al cliente remitente a través de:Correo electrónico con enlace al comprobante digital.Mensaje de texto (SMS) con resumen y enlace de consulta.

### ✓ 3  · conf 0.90 p.2 · NOISE
**Statement:** El contenido del mensaje debe ser configurable.

> **Source:** El contenido del mensaje debe ser configurable y debe incluir el logo institucional actualizado, según lineamientos regulatorios (por ejemplo, Supertransporte).

### ✓ 4  · conf 0.90 p.2
**Statement:** El contenido del mensaje debe incluir el logo institucional actualizado, según lineamientos regulatorios (por ejemplo, Supertransporte).

> **Source:** El contenido del mensaje debe ser configurable y debe incluir el logo institucional actualizado, según lineamientos regulatorios (por ejemplo, Supertransporte).

### ✓ 5  · conf 0.95 p.2
**Statement:** El enlace debe dirigir al portal web o repositorio donde el cliente pueda visualizar y descargar el comprobante, con validación de acceso mediante número de documento o código de seguridad.

> **Source:** El enlace debe dirigir al portal web o repositorio donde el cliente pueda visualizar y descargar el comprobante, con validación de acceso mediante número de documento o código de seguridad.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Registro de motivo de no recolección- Captura de evidencia fotograficas  (4)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir tomar una o más fotografías como evidencia del intento de recolección fallido.

> **Source:** La aplicación debe permitir tomar una o más fotografías como evidencia del intento de recolección fallido.

### ✓ 2  · conf 0.90 p.2
**Statement:** Las imágenes deben almacenarse junto con el motivo y las coordenadas.

> **Source:** Las imágenes deben almacenarse junto con el motivo y las coordenadas, y estar disponibles para consulta posterior.

### ✓ 3  · conf 0.90 p.2
**Statement:** Las imágenes deben estar disponibles para consulta posterior.

> **Source:** Las imágenes deben almacenarse junto con el motivo y las coordenadas, y estar disponibles para consulta posterior.

### ✓ 4  · conf 0.95 p.2
**Statement:** La aplicación debe permitir la validación de la fotografía tomada, si es coherente con el motivo de no recolección planteado.

> **Source:** La aplicación debe permitir la validación de la fotografía tomada, si es coherente con el motivo de no recolección planteado.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Registro de motivo de no recolección- Descripción de validaciones y reglas de campos obligatorios  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir parametrizar campos de planteamiento de obligación tal como observacion, fotografia y cantidad de caracteres.

> **Source:** La aplicación debe permitir parametrizar campos de planteamiento de obligación tal como observacion, fotografia y cantidad de caracteres.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Registro de motivo de no recolección- Ingreso de observaciones  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** El usuario debe poder registrar observaciones adicionales en texto libre, complementando el motivo seleccionado.

> **Source:** El usuario debe poder registrar observaciones adicionales en texto libre, complementando el motivo seleccionado.

## Flujo de aplicación/ Servicio de recogidas / Ejecución de servicios de recolección-Registro de motivo de no recolección- Validación de reglas operativas  (4)

### ✓ 1  · conf 0.95 p.2
**Statement:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar que el intento de recolección se haya realizado dentro de la franja horaria asignada al servicio.

> **Source:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar:Que el intento de recolección se haya realizado dentro de la franja horaria asignada al servicio.

### ✓ 2  · conf 0.95 p.2
**Statement:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar que la ubicación del dispositivo móvil coincida con las coordenadas geográficas del punto de recolección.

> **Source:** Antes de permitir el registro de un motivo de no recolección, la aplicación debe validar:Que la ubicación del dispositivo móvil coincida con las coordenadas geográficas del punto de recolección.

### ✓ 3  · conf 0.90 p.2
**Statement:** Si alguna de las condiciones de validación no se cumple, debe mostrarse un mensaje de advertencia.

> **Source:** Si alguna de estas condiciones no se cumple, debe mostrarse un mensaje de advertencia y bloquear el registro hasta que se corrija la condición.

### ✓ 4  · conf 0.90 p.2
**Statement:** Si alguna de las condiciones de validación no se cumple, debe bloquear el registro hasta que se corrija la condición.

> **Source:** Si alguna de estas condiciones no se cumple, debe mostrarse un mensaje de advertencia y bloquear el registro hasta que se corrija la condición.

## Flujo de aplicación/ Servicio de recogidas / Registro de Motivos de No Entrega  (1)

### ✓ 1  · conf 0.90 p.2
**Statement:** La aplicación debe permitir al usuario registrar por pick to voice la descripción del evento, registrarlo e integrarlo con servicio de planteamiento.

> **Source:** La aplicación debe permitir al usuario registrar por pick to voice la descripción del evento, registrarlo e integrarlo con servicio de planteamiento.

## Flujo de aplicación/Servicio entregas  (61)

### ✓ req-035  · conf 0.95 p.2 · NOISE
**Statement:** La aplicación debe ofrecer la opción de no realizar entrega en la pantalla inicio de ejecución de entrega.

> **Source:** Pantalla inicio de ejecución de entrega -Opción de no realizar entrega

### ✓ req-036  · conf 0.95 p.2
**Statement:** Al ejecutar la opción de no realizar entrega, la aplicación debe llevar al planteamiento de novedad (Motivo de no entrega).

> **Source:** Es necesario que al ejecutar la opción lo lleve al  planteamiento de novedad (Motivo de no entrega)

### ✓ req-037  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de información detalle del servicio en la pantalla inicio de ejecución de entrega.

> **Source:** Pantalla inicio de ejecución de entrega -Opción información detalle

### ✓ req-038  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar el Nombre del remitente.

> **Source:** 1.Nombe del remitente

### ✓ req-039  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar el ID remitente.

> **Source:** 2.ID remitente

### ✓ req-040  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Dirección remitente.

> **Source:** 3.Dirección remitente

### ✓ req-041  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar el Nombre destinatario.

> **Source:** 4-Nombre destinatario

### ✓ req-042  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar el Id de destinatario.

> **Source:** 5.Id de destinatario

### ✓ req-043  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Ciudad destino.

> **Source:** 6.Ciudad destino

### ✓ req-044  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Ciudad origen.

> **Source:** 7.Ciudad origen

### ✓ req-045  · conf 0.90 p.2
**Statement:** La opción información detalle debe relacionar la Cantidad de unidades.

> **Source:** 8.Cantidad de unidades

### ✓ req-046  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Forma de pago.

> **Source:** 9.Forma de pago

### ✓ req-047  · conf 0.90 p.2
**Statement:** La opción información detalle debe relacionar el Subproducto servicio.

> **Source:** 10, Subproducto servicio

### ✓ req-048  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar el Teléfono remitente.

> **Source:** 11.Telefono remitente

### ✓ req-049  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar el Teléfono destinatario.

> **Source:** 12, Telefono destinatario

### ✓ req-050  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Última novedad.

> **Source:** 13, Ultima novedad

### ✓ req-051  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Solución novedad.

> **Source:** 14, Solución novedad

### ✓ req-052  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Observación novedad.

> **Source:** 15.Observación novedad

### ✓ req-053  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Unidad de negocio.

> **Source:** 16, Unidad de negocio

### ✓ req-054  · conf 0.90 p.2 · NOISE
**Statement:** La opción información detalle debe relacionar la Licencia.

> **Source:** 17, Licencia

### ✓ req-055  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de ver las unidades del servicio en la pantalla inicio de ejecución de entrega.

> **Source:** Pantalla inicio de ejecució de entrega -Opción unidades servicio

### ✓ req-056  · conf 0.90 p.2
**Statement:** Al ejecutar la opción de ver las unidades, se debe evidenciar el Código IUP de la unidad.

> **Source:** 1.Codigo IUP de la unidad

### ✓ req-057  · conf 0.90 p.2
**Statement:** Al ejecutar la opción de ver las unidades, se debe evidenciar el Tipo de unidad.

> **Source:** 2.Tipo de unidad

### ✓ req-058  · conf 0.90 p.2 · NOISE
**Statement:** Al ejecutar la opción de ver las unidades, se debe evidenciar la Clase de empaque.

> **Source:** 3.Clase de empaque

### ✓ req-059  · conf 0.90 p.2 · NOISE
**Statement:** Al ejecutar la opción de ver las unidades, se debe evidenciar el Peso.

> **Source:** 4.Peso

### ✓ req-060  · conf 0.90 p.2 · NOISE
**Statement:** Al ejecutar la opción de ver las unidades, se debe evidenciar el Volumen.

> **Source:** 5.Volumen

### ✓ req-061  · conf 0.90 p.2 · NOISE
**Statement:** Al ejecutar la opción de ver las unidades, se deben evidenciar las Notas.

> **Source:** 6.Notas

### ✓ req-062  · conf 0.95 p.2 · NOISE
**Statement:** La aplicación debe ofrecer la opción de lectura de unidades a entregar en la pantalla inicio de ejecución de entrega.

> **Source:** Pantalla inicio de ejecució de entrega - Opción lectura de unidades

### ✓ req-063  · conf 0.95 p.2
**Statement:** Al ejecutar la opción de realizar entrega, la aplicación debe llevar a la opción de lectura de unidades a entregar.

> **Source:** Al ejecutar la opción de realizar entrega espero que lo lleve a la opción de lectura de unidades a entregar

### ✓ req-064  · conf 0.90 p.2
**Statement:** En la lectura de unidades se debe visualizar el Código IUP de la unidad.

> **Source:** 1, Código IUP de la unidad

### ✓ req-065  · conf 0.90 p.2 · NOISE
**Statement:** En la lectura de unidades se debe visualizar el UEN.

> **Source:** 2.UEN

### ✓ req-066  · conf 0.90 p.2 · NOISE
**Statement:** En la lectura de unidades se debe visualizar el Peso y volumen.

> **Source:** 3.Peso y volumen

### ✓ req-067  · conf 0.95 p.2
**Statement:** La aplicación debe contar con una pantalla de inicio proceso de entrega donde tenga las opciones de realizar entrega, no realizar, información detalle del servicio, unidades, opción ubicación mapa.

> **Source:** Se cuente con una pantalla de inicio proceso de entrega donde tenga las opciones de realizar entrega, no realizar, información detalle del servicio, unidades, opción ubicación mapa

### ✓ req-068  · conf 0.95 p.2
**Statement:** La aplicación debe realizar validaciones antes de ejecutar un servicio: si tiene conceptos de recaudo de dinero asociados al servicio.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  si tiene conceptos de recaudo de dinero asociados al servicio

### ✓ req-069  · conf 0.95 p.2
**Statement:** La aplicación debe realizar validaciones antes de ejecutar un servicio: si el servicio tiene una novedad activa.

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  Si el servicio tiene una novedad activa

### ✓ req-070  · conf 0.95 p.2
**Statement:** La opción de recaudo de dinero debe evidenciar los detalles del cobro, es decir los conceptos de liquidación del servicio.

> **Source:** Opción de recaudo de dinero, que evidencie los detalles del cobro es decir los conceptos de liquidación del servicio

### ✓ req-071  · conf 0.95 p.2
**Statement:** La opción debe permitir el recaudo de dinero con pagos en Efectivo.

> **Source:** Que la opción permita el recaudo de dinero pagos en  Efectivo

### ✓ req-072  · conf 0.95 p.2
**Statement:** La opción debe permitir Integración con pasarelas de pago para procesar pagos con tarjetas.

> **Source:** Integración con pasarelas de pago para procesar pagos con tarjetas

### ✓ req-073  · conf 0.95 p.2
**Statement:** La aplicación debe registrar cada transacción relacionando información de pago.

> **Source:** La aplicación registre cada transacción relacionando información de pago

### ✓ req-074  · conf 0.90 p.2
**Statement:** La aplicación debe integrar cada transacción con el collection management.

> **Source:** integre con el collection management

### ✓ req-075  · conf 0.95 p.2
**Statement:** La aplicación debe implementar un sistema de generación de alertas en tiempo real que permita confirmar pagos recibidos y detectar pagos pendientes.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:1) Confirmar pagos recibidos y detectar pagos pendientes

### ✓ req-076  · conf 0.95 p.2
**Statement:** La aplicación debe implementar un sistema de generación de alertas en tiempo real que permita recibir automáticamente la información del recaudo.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:2) Recibir automáticamente la información del recaudo

### ✓ req-077  · conf 0.95 p.2
**Statement:** La aplicación debe implementar un sistema de generación de alertas en tiempo real que permita notificar de forma inmediata a la aplicación correspondiente sobre el estado del pago.

> **Source:** Implementar un sistema de generación de alertas en tiempo real que permita:3) Notificar de forma inmediata a la aplicación correspondiente sobre el estado del pago

### ✓ req-078  · conf 0.95 p.2
**Statement:** La aplicación debe tener la opción de realizar validación del estado del servicio si tiene novedad (Motivo de no entrega) activa antes de ejecutar la lectura de unidades.

> **Source:** La aplicación tenga la opción de realizar validaciones de reglas, en este caso que realice validación del estado del servicio si tiene novedad (Motivo de no entrega) activa antes de ejecutar la lectura de unidades

### ✓ req-079  · conf 0.90 p.2 · NOISE
**Statement:** La aplicación debe asumir acciones en caso de que la novedad esté o no activa.

> **Source:** y que asuma acciones en caso de estar o no activa

### ✓ req-080  · conf 0.95 p.2
**Statement:** La aplicación debe realizar la validación del servicio si tiene conceptos de recaudo de dinero.

> **Source:** La aplicación tenga la opción de realizar validaciones, para este caso realice la validación del servicio si tiene conceptos de recaudo de dinero

### ✓ req-081  · conf 0.95 p.2
**Statement:** Al ejecutar la lectura de la unidad se debe actualizar el contador de unidades a leer.

> **Source:** Al ejecutar la lectura de la unidad se actualice el contador de unidades a leer

### ✓ req-082  · conf 0.95 p.2
**Statement:** Al ejecutar la captura de la unidad, la aplicación debe capturar usuario.

> **Source:** Al ejecutar la captura de la unidad la aplicación debe capturar usuario

### ✓ req-083  · conf 0.90 p.2
**Statement:** Al ejecutar la captura de la unidad, la aplicación debe capturar hora.

> **Source:** hora

### ✓ req-084  · conf 0.90 p.2
**Statement:** Al ejecutar la captura de la unidad, la aplicación debe capturar latitud.

> **Source:** latitud

### ✓ req-085  · conf 0.90 p.2
**Statement:** Al ejecutar la captura de la unidad, la aplicación debe capturar longitud.

> **Source:** longitud

### ✓ req-086  · conf 0.90 p.2
**Statement:** Al ejecutar la captura de la unidad, la aplicación debe capturar vehículo.

> **Source:** vehículo

### ✓ req-087  · conf 0.95 p.2
**Statement:** Al ejecutar la captura de la unidad, la aplicación debe capturar si leyó o no unidades.

> **Source:** capturar si leyó o no unidades

### ✓ req-088  · conf 0.95 p.2
**Statement:** Al ejecutar la captura de la unidad, la aplicación debe capturar si leyó la totalidad de las mismas.

> **Source:** si leyó la totalidad de las mismas

### ✓ req-089  · conf 0.95 p.2
**Statement:** La lectura de unidades debe poder realizarse por Código de lectura.

> **Source:** Es necesario que la lectura de unidades se pueda realizar por Código de lectura

### ✓ req-090  · conf 0.95 p.2
**Statement:** La lectura de unidades debe poder realizarse utilizando la cámara del dispositivo.

> **Source:** o utilizando la cámara del dispositivo

### ✓ req-091  · conf 0.95 p.2 · NOISE
**Statement:** Los códigos de lectura pueden ser de barras o QR.

> **Source:** Los códigos de lectura pueden ser de barras o QR

### ✓ req-092  · conf 0.90 p.2 · NOISE
**Statement:** La certificación de la entrega a destinatario debe capturar el Nombre de quien recibe.

> **Source:** 1) Nombre de quien recibe

### ✓ req-093  · conf 0.90 p.2
**Statement:** La certificación de la entrega a destinatario debe capturar el Documento de identidad.

> **Source:** 2)Documento de identidad

### ✓ req-094  · conf 0.90 p.2 · NOISE
**Statement:** La certificación de la entrega a destinatario debe capturar el Numero de contacto (Celular).

> **Source:** 3)Numero de contacto (Celular)

### ✓ req-095  · conf 0.90 p.2
**Statement:** La certificación de la entrega a destinatario debe capturar el Correo electrónico.

> **Source:** 4)Correo electrónico

## Funcionalidad / Login  (21)

### ✓ 1  · conf 0.98 p.2 · NOISE
**Statement:** La aplicación debe habilitar la posibilidad de que múltiples usuarios puedan iniciar sesión y operar simultáneamente sobre un mismo medio logístico (Vehículo de ultima milla), permitiendo la ejecución paralela de varios servicios (entregas y recogidas) desde dicho medio.

> **Source:** Se requiere habilitar la posibilidad de que múltiples usuarios puedan iniciar sesión y operar simultáneamente sobre un mismo medio logístico (Vehiculo de ultima milla), permitiendo la ejecución paralela de varios servicios (entregas y recogidas) desde dicho medio.

### ✓ 2  · conf 0.97 p.2 · NOISE
**Statement:** La planeación de las órdenes de trabajo debe estar asociada al identificador del medio logístico (ID del vehículo) y no a un usuario específico, de modo que cualquier miembro de la tripulación autorizado pueda iniciar sesión y ejecutar uno o varios servicios asignados a ese ID de móvil.

> **Source:** Esta necesidad se fundamenta en que la planeación de las órdenes de trabajo está asociada al identificador del móvil (ID del medio logístico) y no a un usuario específico. En consecuencia, cualquier miembro de la tripulación autorizado podrá iniciar sesión y ejecutar uno o varios servicios asignados a ese ID de móvil, sin restricciones por usuario.

### ✓ 3  · conf 0.98 p.2
**Statement:** La aplicación debe implementar validaciones de inicio de sesión basadas en los parámetros ingresados por el usuario, con el fin de garantizar la integridad operativa y la correcta asociación entre usuarios, ciudad de operación y medios logísticos (vehículo).

> **Source:** Se requiere que la aplicación implemente validaciones de inicio de sesión basadas en los parámetros ingresados por el usuario, con el fin de garantizar la integridad operativa y la correcta asociación entre usuarios, ciudad de operación y medios logísticos (vehículo).

### ✓ 4  · conf 0.99 p.2 · NOISE
**Statement:** La aplicación debe verificar que el usuario exista y se encuentre activo en el directorio corporativo de TCC.

> **Source:** Verificar que el usuario exista y se encuentre activo en el directorio corporativo de TCC..

### ✓ 5  · conf 0.99 p.2 · NOISE
**Statement:** La aplicación debe confirmar que el identificador del vehículo ingresado exista en el maestro de vehículos y esté en estado activo.

> **Source:** Confirmar que el identificador del vehículo ingresado exista en el maestro de vehículos y esté en estado activo..

### ✓ 6  · conf 0.99 p.2 · NOISE
**Statement:** La aplicación debe validar que el centro de operación asignado al usuario coincida con el centro de operación del medio logístico al que intenta acceder.

> **Source:** Validar que el centro de operación asignado al usuario coincida con el centro de operación del medio logístico al que intenta acceder..

### ✓ 7  · conf 0.98 p.2 · NOISE
**Statement:** La aplicación debe validar, en el momento de relacionar el ID del medio logístico (vehículo), que el usuario logeado actualmente no esté logeado en otro medio logístico (vehículo) para evitar simultaneidades en 2 órdenes de trabajo.

> **Source:** Que el usuario logeado, en el momento de relacionar el ID del medio logístico (vehículo), la aplicación realice la validación, de que actualmente no este logeado en otro medio logístico (vehículo). Para evitar simultaneidades en 2 órdenes de trabajo.

### ✓ 8  · conf 0.98 p.2 · NOISE
**Statement:** La aplicación debe evidenciar el inicio exitoso de la orden de trabajo mediante un mensaje de confirmación.

> **Source:** que evidencie el inicio exitoso de la orden de trabajo, mediante una mensaje de confirmación.

### ✓ 9  · conf 0.98 p.2 · NOISE
**Statement:** En caso de que el login no sea exitoso, la aplicación debe evidenciar un mensaje explicando por qué no fue posible el ingreso, relacionando cuál fue la validación que falló.

> **Source:** en caso de no ser exitosa que se evidencie un mensaje explicando porque no fue posible el ingreso. Relacionando cual fue la validación que fallo.

### ✓ 10  · conf 0.98 p.2
**Statement:** La aplicación debe capturar el registro de los usuarios que se logean y se relacionan a un medio logístico (Vehículo) para permitir trazabilidad de qué tripulación desarrolló la orden de trabajo.

> **Source:** capturar el registro de los usuarios que se logean y se relacionan a un medio logístico (Vehículo). Para que este en capacidad de mirar por trazabilidad que tripulación desarrollo la orden de trabajo.

### ✓ 11  · conf 0.97 p.2
**Statement:** La aplicación debe permitir la implementación de un mecanismo de doble factor de autenticación (2FA) como medida de seguridad adicional en el proceso de recuperación de contraseña, activándose cuando un usuario indique que ha olvidado su contraseña.

> **Source:** La aplicación debe permitir la implementación de un mecanismo de doble factor de autenticación (2FA) como medida de seguridad adicional en el proceso de recuperación de contraseña. Esta funcionalidad debe activarse cuando un usuario indique que ha olvidado su contraseña, y debe contemplar al menos los siguientes elementos:

### ✓ 12  · conf 0.98 p.2 · NOISE
**Statement:** El mecanismo 2FA debe incluir la validación del usuario mediante un canal alternativo previamente registrado (correo electrónico corporativo o número de teléfono móvil).

> **Source:** Validación del usuario mediante un canal alternativo previamente registrado (correo electrónico corporativo o número de teléfono móvil).

### ✓ 13  · conf 0.98 p.2 · NOISE
**Statement:** El mecanismo 2FA debe incluir el envío de un código de verificación de un solo uso (OTP) al canal seleccionado.

> **Source:** Envío de un código de verificación de un solo uso (OTP) al canal seleccionado.

### ✓ 14  · conf 0.98 p.2
**Statement:** El mecanismo 2FA debe incluir la solicitud y validación del código OTP antes de permitir el restablecimiento de la contraseña.

> **Source:** Solicitud y validación del código OTP antes de permitir el restablecimiento de la contraseña..

### ✓ 15  · conf 0.98 p.2
**Statement:** La aplicación deberá incluir un módulo de onboarding digital que permita registrar, validar y capacitar a nuevos usuarios logísticos (conductores, auxiliares, coordinadores) antes de habilitar su acceso operativo, siendo este proceso guiado, auditable y adaptable según el rol asignado.

> **Source:** La aplicación deberá incluir un módulo de onboarding digital que permita registrar, validar y capacitar a nuevos usuarios logísticos (conductores, auxiliares, coordinadores) antes de habilitar su acceso operativo. Este proceso debe ser guiado, auditable y adaptable según el rol asignado..

### ✓ 16  · conf 0.97 p.2
**Statement:** La aplicación debe permitir la visualización de una E-Card o tarjeta digital informativa que consolide los principales datos operativos de la ruta asignada, presentándose de forma clara, accesible y en tiempo real para los usuarios autorizados.

> **Source:** Permitir la visualización de una E-Card o tarjeta digital informativa que consolide los principales datos operativos de la ruta asignada. Esta E-Card debe presentarse de forma clara, accesible y en tiempo real para los usuarios autorizados, e incluir en su diseño como mínimo los siguientes elementos:

### ✓ 17  · conf 0.95 p.2
**Statement:** La E-Card debe incluir como mínimo el nombre de la ruta asignada.

> **Source:** Nombre de la ruta asignada.

### ✓ 18  · conf 0.95 p.2 · NOISE
**Statement:** La E-Card debe incluir como mínimo la cantidad total de paradas programadas.

> **Source:** Cantidad total de paradas programadas.

### ✓ 19  · conf 0.95 p.2 · NOISE
**Statement:** La E-Card debe incluir como mínimo el número de servicios de entrega asociados a la ruta.

> **Source:** Número de servicios de entrega asociados a la ruta.

### ✓ 20  · conf 0.95 p.2 · NOISE
**Statement:** La E-Card debe incluir como mínimo el número de servicios de recogida asignados.

> **Source:** Número de servicios de recogida asignados.

### ✓ 21  · conf 0.95 p.2
**Statement:** La E-Card debe incluir como mínimo el valor total de dinero a recaudar durante la ejecución de la ruta, por producto y forma de pago.

> **Source:** Valor total de dinero a recaudar durante la ejecución de la ruta, por producto y forma de pago.

## Funcionalidad/ Consulta de unidad  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** La funcionalidad deberá permitir al usuario acceder a una vista consolidada de los datos clave de una unidad, incluyendo datos del remitente, datos del destinatario, información de liquidación, datos operativos e identificadores asociados (número de guía, número de orden de trabajo, placa del vehículo si aplica, entre otros).

> **Source:** La funcionalidad deberá permitir al usuario acceder a una vista consolidada de los datos clave de una unidad, incluyendo:Datos del remitente: nombre, dirección, contacto.Datos del destinatario: nombre, dirección, contacto.Información de liquidación: valor del servicio, estado de cobro, forma de pago.Datos operativos: ciudad de origen, ciudad de destino, fecha y hora de recolección y entrega, estado actual del servicio.Identificadores asociados: número de guía, número de orden de trabajo, placa del vehículo (si aplica), entre otros.

### ✓ 2  · conf 0.95 p.2
**Statement:** La información de consulta de unidad deberá presentarse de forma ordenada, agrupada por secciones (ej. remitente, destinatario, trazabilidad, liquidación), permitiendo al usuario identificar rápidamente los datos relevantes sin necesidad de desplazamientos extensos o múltiples clics.

> **Source:** La información deberá presentarse de forma ordenada, agrupada por secciones (ej. remitente, destinatario, trazabilidad, liquidación), permitiendo al usuario identificar rápidamente los datos relevantes sin necesidad de desplazamientos extensos o múltiples clics.

## Funcionalidad/ Cotización de servicio  (8)

### ✓ 1  · conf 0.95 p.2
**Statement:** La funcionalidad deberá permitir al usuario ingresar datos mínimos para cotización: datos del remitente (nombre, dirección, ciudad de origen, tipo de cliente si aplica), datos del destinatario (nombre, dirección, ciudad de destino), y características de la unidad o envío (número de unidades, peso, volumen, tipo de mercancía, condiciones especiales si aplica).

> **Source:** La funcionalidad deberá permitir al usuario ingresar los siguientes datos mínimos para generar una cotización:Datos del remitente: nombre, dirección, ciudad de origen, tipo de cliente (si aplica).Datos del destinatario: nombre, dirección, ciudad de destino.Características de la unidad o envío: número de unidades, peso, volumen, tipo de mercancía, condiciones especiales (si aplica).

### ✓ 2  · conf 0.95 p.2
**Statement:** El sistema deberá calcular automáticamente el valor estimado del servicio considerando las reglas tarifarias vigentes, zonas de cobertura, condiciones comerciales y restricciones operativas.

> **Source:** Con base en los datos ingresados, el sistema deberá calcular automáticamente el valor estimado del servicio, considerando las reglas tarifarias vigentes, zonas de cobertura, condiciones comerciales y restricciones operativas.

### ✓ 3  · conf 0.95 p.2
**Statement:** El resultado de la cotización deberá presentarse de forma clara, incluyendo valor total estimado y detalle de componentes (flete base, recargos, impuestos, etc.).

> **Source:** El resultado deberá presentarse de forma clara, incluyendo:Valor total estimado.Detalle de componentes (flete base, recargos, impuestos, etc.).

### ✓ 4  · conf 0.95 p.2
**Statement:** El sistema deberá validar que los datos ingresados sean coherentes y completos antes de generar la cotización (Integración).

> **Source:** El sistema deberá validar que los datos ingresados sean coherentes y completos antes de generar la cotización (Integración).

### ✓ 5  · conf 0.95 p.2
**Statement:** En caso de inconsistencias (por ejemplo, ciudad de destino fuera de cobertura), el sistema deberá notificar al usuario y sugerir alternativas viables.

> **Source:** En caso de inconsistencias (por ejemplo, ciudad de destino fuera de cobertura), deberá notificar al usuario y sugerir alternativas viables.

### ✓ 6  · conf 0.95 p.2
**Statement:** Si el usuario acepta la cotización, el sistema deberá permitir la conversión directa a una orden de servicio, heredando automáticamente toda la información ingresada (remitente, destinatario, unidades, origen, destino, etc.) para evitar reprocesos.

> **Source:** Si el usuario acepta la cotización, el sistema deberá permitir la conversión directa a una orden de servicio, heredando automáticamente toda la información ingresada (remitente, destinatario, unidades, origen, destino, etc.) para evitar reprocesos.

### ✓ 7  · conf 0.95 p.2
**Statement:** La acción de conversión a orden de servicio deberá redirigir al módulo de grabación de servicios, permitiendo al usuario completar los datos adicionales requeridos para formalizar el servicio.

> **Source:** Esta acción deberá redirigir al módulo de grabación de servicios, permitiendo al usuario completar los datos adicionales requeridos para formalizar el servicio.

### ✓ 8  · conf 0.95 p.2
**Statement:** Toda cotización generada deberá quedar registrada en el sistema con un identificador único, incluyendo usuario que la generó, fecha y hora, datos de entrada, resultado de la cotización, y estado (cotizada, convertida en servicio, descartada).

> **Source:** Toda cotización generada deberá quedar registrada en el sistema con un identificador único, incluyendo:Usuario que la generó.Fecha y hora.Datos de entrada.Resultado de la cotización.Estado (cotizada, convertida en servicio, descartada).

## Funcionalidad/ Reporte de recaudo  (5)

### ✓ 1  · conf 0.95 p.2
**Statement:** La funcionalidad deberá permitir la generación de un reporte que consolide la información financiera relacionada con los servicios ejecutados, incluyendo monto recaudado, monto pendiente y monto por recaudar.

> **Source:** La funcionalidad deberá permitir la generación de un reporte que consolide la información financiera relacionada con los servicios ejecutados, incluyendo:Monto recaudado: total de dinero efectivamente cobrado por el usuario.Monto pendiente: dinero correspondiente a servicios entregados, pero aún no recaudado.Monto por recaudar: servicios programados o en tránsito con expectativa de recaudo.

### ✓ 2  · conf 0.95 p.2
**Statement:** Cada registro del reporte de recaudo deberá incluir información detallada del servicio asociado: número de guía u orden, fecha y hora del servicio, ciudad de origen y destino, estado del servicio, valor del servicio, valor recaudado y medio de recaudo.

> **Source:** Cada registro del reporte deberá incluir información detallada del servicio asociado, como:Número de guía u orden.Fecha y hora del servicio.Ciudad de origen y destino.Estado del servicio (entregado, en tránsito, pendiente).Valor del servicio, valor recaudado y medio de recaudo.

### ✓ 3  · conf 0.95 p.2
**Statement:** El reporte deberá identificar el método de recaudo utilizado en cada caso, tales como efectivo, medio electrónico, segmentación por tipo de medio electrónico, recaudo flete contra entrega, recaudo contado en origen, y recaudo de producto.

> **Source:** El reporte deberá identificar el método de recaudo utilizado en cada caso, tales como:Efectivo.Medio electrónico.Segmentación por tipo de medio electrónicoRecaudo Flete contra entrega.Recaudo contado en origen Recaudo de producto.

### ✓ 4  · conf 0.95 p.2
**Statement:** El sistema deberá registrar y mostrar el usuario que realizó el recaudo, incluyendo nombre del usuario, identificador de sesión, y fecha y hora del registro de recaudo.

> **Source:** El sistema deberá registrar y mostrar el usuario que realizó el recaudo, incluyendo:Nombre del usuario.Identificador de sesión.Fecha y hora del registro de recaudo.

### ✓ 5  · conf 0.95 p.2
**Statement:** El reporte deberá permitir aplicar filtros para facilitar la consulta, tales como rango de fechas, estado del recaudo (recaudado, pendiente, por recaudar), usuario, y ciudad o zona.

> **Source:** El reporte deberá permitir aplicar filtros para facilitar la consulta, tales como:Rango de fechas.Estado del recaudo (recaudado, pendiente, por recaudar).Usuario.Ciudad o zona.

## Funcionalidad/ Servicios del dia  (25)

### ✓ 1  · conf 0.98 p.2
**Statement:** La solución debe permitir la visualización dinámica y en tiempo real del listado de órdenes de trabajo, incluyendo el estado actualizado de cada uno de los servicios asociados.

> **Source:** La solución debe permitir la visualización dinámica y en tiempo real del listado de órdenes de trabajo, incluyendo el estado actualizado de cada uno de los servicios asociados.

### ✓ 2  · conf 0.97 p.2
**Statement:** La solución debe permitir la visualización del estado de los servicios, diferenciando claramente si se encuentran en estado pendiente, con novedad o cumplido.

> **Source:** 1) Visualización del estado de los servicios, diferenciando claramente si se encuentran en estado pendiente, con novedad o cumplido..

### ✓ 3  · conf 0.97 p.2
**Statement:** La solución debe permitir actualización dinámica, de modo que a medida que se ejecutan los servicios, se reflejen los cambios en el estado y en la secuencia de ejecución.

> **Source:** 2)Actualización dinámica, de modo que a medida que se ejecutan los servicios, se reflejen los cambios en el estado y en la secuencia de ejecución..

### ✓ 4  · conf 0.97 p.2
**Statement:** La solución debe permitir la representación de la secuencia de planeación, mostrando el orden previsto de ejecución de los servicios según la lógica integrada desde la planeación.

> **Source:** 3) Representación de la secuencia de planeación, mostrando el orden previsto de ejecución de los servicios según la lógica integrada desde la planeación..

### ✓ 5  · conf 0.97 p.2
**Statement:** La solución debe permitir aplicar filtros dinámicos sobre el listado de servicios asociados a las órdenes de trabajo, con el fin de facilitar la consulta, trazabilidad y gestión operativa.

> **Source:** La solución debe permitir aplicar filtros dinámicos sobre el listado de servicios asociados a las órdenes de trabajo, con el fin de facilitar la consulta, trazabilidad y gestión operativa.

### ✓ 6  · conf 0.97 p.2
**Statement:** La solución debe permitir filtro por estado del servicio: visualización según si el servicio está pendiente, ejecutado o presenta novedad.

> **Source:** Por estado del servicio: permitir la visualización según si el servicio está pendiente, ejecutado o presenta novedad.

### ✓ 7  · conf 0.97 p.2
**Statement:** La solución debe permitir filtro por tipo de proceso: filtrar por servicios de recogida o servicios de entrega.

> **Source:** Por tipo de proceso: permitir filtrar por servicios de recogida o servicios de entrega.

### ✓ 8  · conf 0.96 p.2
**Statement:** La solución debe permitir filtro por variables específicas del servicio, tales como: ID del servicio, nombre del remitente, nombre del destinatario, forma de pago contado en origen, contado en destino y crédito en origen o destino.

> **Source:** Por variables específicas del servicio, tales como:ID del servicioNombre del remitenteNombre del destinatarioForma de pago contado en origen, contado en destino y credito en origen o destino.

### ✓ 9  · conf 0.95 p.2
**Statement:** Por cada servicio de entrega en el listado de servicios, se debe visualizar de forma clara y estructurada la siguiente información operativa: nombre del cliente destinatario, dirección de entrega, observaciones asociadas al servicio, número de contacto del destinatario, forma de pago del servicio (crédito, recaudo en origen o en destino) e identificación de subproducto en caso de logística inversa u otros tipos definidos por la operación.

> **Source:** Por cada servicio de entrega en el listado de servicios, se visualice de forma clara y estructurada la siguiente información operativa:1) Nombre del cliente destinatarioDirección de entregaObservaciones asociadas al servicioNúmero de contacto del destinatarioForma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)Identificación de subproducto, en caso de tratarse de un servicio con características especiales como logística inversa u otros tipos definidos por la operación.

### ✓ 10  · conf 0.95 p.2
**Statement:** Por cada servicio de entrega debe visualizarse un ícono de mapa que dé acceso a la funcionalidad de navegación (cómo llegar) hacia la dirección del destinatario.

> **Source:** Ícono de mapa: acceso a la funcionalidad de navegación (cómo llegar) hacia la dirección del destinatario.

### ✓ 11  · conf 0.95 p.2
**Statement:** Por cada servicio de entrega debe visualizarse un ícono de teléfono que ejecute directamente una llamada al número de contacto del destinatario.

> **Source:** Ícono de teléfono: ejecución directa de llamada al número de contacto del destinatario.

### ✓ 12  · conf 0.95 p.2
**Statement:** Por cada servicio de entrega debe visualizarse un ícono de información que muestre información complementaria del servicio, incluyendo datos del remitente, detalles del destinatario, unidades asociadas al servicio y cualquier otra información relevante para la operación.

> **Source:** Ícono de información: visualización de información complementaria del servicio, incluyendo datos del remitente, detalles del destinatario, unidades asociadas al servicio y cualquier otra información relevante para la operación..

### ✓ 13  · conf 0.94 p.2
**Statement:** Por cada servicio de recogida en el listado de servicios, se debe visualizar de forma clara y estructurada la siguiente información operativa: nombre del cliente remitente, dirección de despacho, observaciones asociadas al servicio, número de contacto del remitente, forma de pago del servicio (crédito, recaudo en origen o en destino) e identificación de tipo de recogida (servicio con remesas asociadas, servicio sin servicios asociados donde se deben crear servicios, o servicio donde se debe sincronizar para relacionar servicios creados por el cliente).

> **Source:** Por cada servicio de recogida en el listado de servicios, se visualice de forma clara y estructurada la siguiente información operativa:1)Nombre del cliente remitenteDirección de despachoObservaciones asociadas al servicioNúmero de contacto del remitenteForma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)Identificación de tipo de recogida: En caso de tratarse de un servicio con remesas asociadas, o un servicio sin servicios asociados donde debes crear servicios, o servicio en el cual debes sincronizar para relacionar servicios creados por el cliente.

### ✓ 14  · conf 0.95 p.2
**Statement:** Por cada servicio de recogida debe visualizarse un ícono de mapa que dé acceso a la funcionalidad de navegación (cómo llegar) hacia la dirección del destinatario.

> **Source:** Ícono de mapa: acceso a la funcionalidad de navegación (cómo llegar) hacia la dirección del destinatario.

### ✓ 15  · conf 0.95 p.2
**Statement:** Por cada servicio de recogida debe visualizarse un ícono de teléfono que ejecute directamente una llamada al número de contacto del destinatario.

> **Source:** Ícono de teléfono: ejecución directa de llamada al número de contacto del destinatario.

### ✓ 16  · conf 0.95 p.2
**Statement:** Por cada servicio de recogida debe visualizarse un ícono de información que muestre información complementaria del servicio, incluyendo datos del remitente, detalles del destinatario, unidades asociadas al servicio y cualquier otra información relevante para la operación.

> **Source:** Ícono de información: visualización de información complementaria del servicio, incluyendo datos del remitente, detalles del destinatario, unidades asociadas al servicio y cualquier otra información relevante para la operación..

### ✓ 17  · conf 0.96 p.2
**Statement:** En la pantalla de servicios del día, en la parte superior, se necesita una Card interactiva que visualice el progreso de la orden de trabajo del día, segmentando nombre de la ruta, cantidad de paradas, cantidad de servicios de entregas, de recogidas, separados por servicios faltantes, realizadas y con novedad, dinero recaudado y dinero pendiente por recaudar, mostrando el porcentaje de progreso a medida que se ejecuten los servicios.

> **Source:** En la pantalla de servicios del dia en la parte superior cuidando la experiencia de usuario necesitamos una Card donde visualice el progreso de la orden de trabajo del día, que se segmente el nombre de la ruta, cantidad de paradas, cantidad de servicios de entregas, de recogidas, lo anterior separado por servicios faltantes, realizadas y con novedad, dinero recaudado, dinero pendiente por recaudar  dicho comportamiento debe ser interactivo mostrando el porcentaje de progreso a medida que se ejecuten los servicios..

### ✓ 18  · conf 0.98 p.2
**Statement:** La aplicación debe incluir una opción que genere llamada telefónica al cliente destinatario o remitente según sea el servicio, ubicada en cada servicio listado en servicios del día.

> **Source:** Opción genere llamada telefónica al cliente destinatario o remitente según sea el servicio, dicha opción debe estar ubicada en cada servicio listado en servicios del día.

### ✓ 19  · conf 0.98 p.2
**Statement:** La aplicación debe incluir una opción que permita, a partir desde la dirección de la parada, ejecutar la navegación en mapa accionando la funcionalidad de cómo llegar, listada en cada servicio.

> **Source:** opción que permita a partir desde la dirección de la parada, ejecutar la navegación en mapa accionando la funcionalidad de cómo llegar, la opción debe aparecer listada en cada servicio.

### ✓ 20  · conf 0.97 p.2
**Statement:** La aplicación debe incluir una opción de enviar mensajes parametrizables a demanda por el usuario, al cliente destinatario o remitente por medio del celular relacionado al servicio, estando listada en cada uno de los servicios.

> **Source:** Opción de enviar mensajes parametrizables a demanda por el usuario, al cliente destinatario o remitente por medio del celular relacionado al servicio. La opción debe estar listada en cada uno de los servicios.

### ✓ 21  · conf 0.97 p.2
**Statement:** Las opciones de mensajes parametrizables (cercanía de la parada, novedad o duda sobre el servicio) deben quedar parametrizables desde el módulo de parámetros de notificaciones.

> **Source:** Las opciones de mensajes parametrizables se relacionan a cercanía de la parada, alguna novedad o duda sobre el servicio, estos mensajes deben quedar parametrizables desde el módulo de parámetros de notificaciones.

### ✓ 22  · conf 0.98 p.2
**Statement:** La aplicación debe incluir una opción en la pantalla de servicios del día que lleve a la funcionalidad de navegación en mapa.

> **Source:** Opción en la pantalla de servicios del día, que lo lleve a la funcionalidad de navegación en mapa.

### ✓ 23  · conf 0.98 p.2
**Statement:** Al ingresar a la navegación en mapa es necesaria la visualización de los servicios del día georreferenciados en el mapa, de acuerdo a la dirección de destino para las entregas, y de despacho para las recogidas.

> **Source:** Al ingresar a la navegación en mapa es necesaria la visualización de los servicios del día georreferenciados en el mapa, de acuerdo a la dirección de destino para las entregas, y de despacho para las recogidas..

### ✓ 24  · conf 0.97 p.2
**Statement:** Cada geo punto de entrega debe tener la opción drill down donde se visualice la información del servicio, además de las opciones de cómo llegar o contactar.

> **Source:** Que cada geo punto de entrega tenga la opción dril Down donde se visualice la información del servicio, además  las opciones de cómo llegar o contactar.

### ✓ 25  · conf 0.95 p.2
**Statement:** La información del servicio en el drill down del geo punto debe mostrar las siguientes variables: nombre del cliente destinatario, dirección de entrega, observaciones asociadas al servicio, número de contacto del destinatario, y forma de pago del servicio (crédito, recaudo en origen o en destino).

> **Source:** Con relación a la información del servicio debe aparecer las siguientes variables:1) Nombre del cliente destinatarioDirección de entregaObservaciones asociadas al servicioNúmero de contacto del destinatarioForma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)

## Funcionalidad/Cierre de ruta  (21)

### ✓ 1  · conf 0.90 p.2
**Statement:** El sistema debe realizar validación de que todos los servicios planeados estén ejecutados, y cero registros pendientes.

> **Source:** Validación de que todos los servicios planeados estén ejecutados, y cero registros pendientes. Además, que permita capturar los datos al sistema de los servicios, fecha y usuario.

### ✓ 2  · conf 0.90 p.2
**Statement:** El sistema debe permitir capturar los datos al sistema de los servicios, fecha y usuario.

> **Source:** Además, que permita capturar los datos al sistema de los servicios, fecha y usuario.

### ✓ 3  · conf 0.85 p.2 · NOISE
**Statement:** Debe explicar muy bien desde el frente en que no fue efectiva la entrega.

> **Source:** Notas: explicar muy bien desde el frente en que no fue efectiva la entrega.

### ✓ 4  · conf 0.95 p.2
**Statement:** Al marcar como entregado el último servicio asignado en la ruta, el sistema deberá ejecutar automáticamente el procedimiento de cierre, sin requerir intervención adicional del usuario.

> **Source:** Al marcar como entregado el último servicio asignado en la ruta, el sistema deberá ejecutar automáticamente el procedimiento de cierre, sin requerir intervención adicional del usuario.

### ✓ 5  · conf 0.95 p.2
**Statement:** El cierre automático deberá incluir un resumen de la jornada y confirmación de finalización.

> **Source:** Este cierre automático incluirá un resumen de la jornada y confirmación de finalización, optimizando tiempos y reduciendo errores por omisión.

### ✓ 6  · conf 0.95 p.2
**Statement:** El sistema deberá realizar validaciones automáticas sobre el estado de todos los servicios asignados en la ruta, identificando si están completados, pendientes o presentan novedades.

> **Source:** El sistema deberá realizar validaciones automáticas sobre el estado de todos los servicios asignados en la ruta, identificando si están completados, pendientes o presentan novedades.

### ✓ 7  · conf 0.95 p.2
**Statement:** En caso de detectar inconsistencias (por ejemplo, servicios sin registrar o con datos incompletos), el sistema deberá alertar al usuario antes de permitir el cierre.

> **Source:** En caso de detectar inconsistencias (por ejemplo, servicios sin registrar o con datos incompletos), deberá alertar al usuario antes de permitir el cierre.

### ✓ 8  · conf 0.95 p.2
**Statement:** Durante el proceso de cierre, la aplicación deberá permitir la captura de información clave para trazabilidad y análisis posterior, incluyendo hora de cierre, ubicación GPS, observaciones del conductor, estado del vehículo, y cualquier otra variable definida por la operación.

> **Source:** Durante el proceso de cierre, la aplicación deberá permitir la captura de información clave para trazabilidad y análisis posterior. Esto incluye, pero no se limita a: hora de cierre, ubicación GPS, observaciones del conductor, estado del vehículo, y cualquier otra variable definida por la operación.

### ✓ 9  · conf 0.95 p.2
**Statement:** El sistema deberá identificar automáticamente servicios que no hayan sido gestionados y sugerir al usuario el registro de una novedad asociada (por ejemplo, cliente ausente, dirección incorrecta, etc.).

> **Source:** El sistema deberá identificar automáticamente servicios que no hayan sido gestionados y sugerir al usuario el registro de una novedad asociada (por ejemplo, cliente ausente, dirección incorrecta, etc.).

### ✓ 10  · conf 0.90 p.2
**Statement:** Esta funcionalidad debe facilitar el cumplimiento de protocolos operativos y asegurar la integridad de la información registrada.

> **Source:** Esta funcionalidad debe facilitar el cumplimiento de protocolos operativos y asegurar la integridad de la información registrada.

### ✓ 11  · conf 0.95 p.2
**Statement:** El sistema deberá permitir al usuario cerrar sesión de forma segura sin que esto implique el cierre automático de la orden de trabajo activa.

> **Source:** El sistema deberá permitir al usuario cerrar sesión de forma segura sin que esto implique el cierre automático de la orden de trabajo activa.

### ✓ 12  · conf 0.85 p.2 · NOISE
**Statement:** Esta separación funcional garantiza que el usuario pueda salir temporalmente de la aplicación sin afectar el estado operativo de la ruta o servicios en curso.

> **Source:** Esta separación funcional garantiza que el usuario pueda salir temporalmente de la aplicación sin afectar el estado operativo de la ruta o servicios en curso.

### ✓ 13  · conf 0.95 p.2
**Statement:** La aplicación deberá mantener un registro detallado de los eventos de inicio y cierre de sesión, incluyendo Identificador del usuario, Fecha y hora de inicio y cierre de sesión, Estado de la orden de trabajo al momento del cierre, e Indicador de si el cierre fue manual o automático por inactividad.

> **Source:** La aplicación deberá mantener un registro detallado de los eventos de inicio y cierre de sesión, incluyendo:Identificador del usuario.Fecha y hora de inicio y cierre de sesión.Estado de la orden de trabajo al momento del cierre.Indicador de si el cierre fue manual o automático por inactividad.

### ✓ 14  · conf 0.85 p.2
**Statement:** Esta información de registro de sesión será utilizada para auditoría, análisis de uso y control de cumplimiento operativo.

> **Source:** Esta información será utilizada para auditoría, análisis de uso y control de cumplimiento operativo.

### ✓ 15  · conf 0.95 p.2
**Statement:** El sistema deberá contar con un mecanismo de cierre automático de sesión en caso de inactividad prolongada (parámetro configurable).

> **Source:** El sistema deberá contar con un mecanismo de cierre automático de sesión en caso de inactividad prolongada (parámetro configurable).

### ✓ 16  · conf 0.90 p.2
**Statement:** Al detectar inactividad durante el tiempo definido, el sistema deberá finalizar la sesión de forma segura.

> **Source:** Al detectar que el usuario no ha interactuado con la aplicación durante el tiempo definido, se deberá:Finalizar la sesión de forma segura.Registrar el evento como cierre automático por inactividad.Mantener el estado de la orden de trabajo sin alteraciones.Notificar al usuario al momento de su próximo inicio de sesión sobre el cierre automático ocurrido.

### ✓ 17  · conf 0.90 p.2
**Statement:** Al detectar inactividad durante el tiempo definido, el sistema deberá registrar el evento como cierre automático por inactividad.

> **Source:** Al detectar que el usuario no ha interactuado con la aplicación durante el tiempo definido, se deberá:Finalizar la sesión de forma segura.Registrar el evento como cierre automático por inactividad.Mantener el estado de la orden de trabajo sin alteraciones.Notificar al usuario al momento de su próximo inicio de sesión sobre el cierre automático ocurrido.

### ✓ 18  · conf 0.90 p.2
**Statement:** Al detectar inactividad durante el tiempo definido, el sistema deberá mantener el estado de la orden de trabajo sin alteraciones.

> **Source:** Al detectar que el usuario no ha interactuado con la aplicación durante el tiempo definido, se deberá:Finalizar la sesión de forma segura.Registrar el evento como cierre automático por inactividad.Mantener el estado de la orden de trabajo sin alteraciones.Notificar al usuario al momento de su próximo inicio de sesión sobre el cierre automático ocurrido.

### ✓ 19  · conf 0.90 p.2
**Statement:** Al detectar inactividad durante el tiempo definido, el sistema deberá notificar al usuario al momento de su próximo inicio de sesión sobre el cierre automático ocurrido.

> **Source:** Al detectar que el usuario no ha interactuado con la aplicación durante el tiempo definido, se deberá:Finalizar la sesión de forma segura.Registrar el evento como cierre automático por inactividad.Mantener el estado de la orden de trabajo sin alteraciones.Notificar al usuario al momento de su próximo inicio de sesión sobre el cierre automático ocurrido.

### ✓ 20  · conf 0.95 p.2
**Statement:** Al iniciar sesión nuevamente, el sistema deberá validar si existe una sesión anterior cerrada automáticamente y permitir la reanudación del trabajo desde el punto en que se dejó, siempre que la orden de trabajo siga vigente.

> **Source:** Al iniciar sesión nuevamente, el sistema deberá validar si existe una sesión anterior cerrada automáticamente y permitir la reanudación del trabajo desde el punto en que se dejó, siempre que la orden de trabajo siga vigente.

### ✓ 21  · conf 0.80 p.2 · NOISE
**Statement:** Esto asegura continuidad operativa sin pérdida de información.

> **Source:** Esto asegura continuidad operativa sin pérdida de información.

## Funcionalidad/Funcionalidad Copilot  (1)

### ⚠ 1  · conf 0.95 p.2
**Statement:** La aplicación debe contar con una funcionalidad Copilot que guía e indica al colaborador para ejecutar procesos de la aplicación.

> **Source:** Funcionalidad/ Funcionalidad Copilot, Proyecto diseño de aplicación ruta -Req funcionales = Funcionalidad que guía e indica al colaborador para ejecutar procesos de la aplicación.

## Funcionalidad/Funcionalidad Pick to voice  (1)

### ⚠ 1  · conf 0.95 p.2
**Statement:** La aplicación debe contar con una funcionalidad Pick to voice que permite ejecutar procesos mediante las indicaciones de la voz, relacionado a eventos importantes durante el desarrollo de flujos de entregas y recogidas, permitiendo programarle al flujo de la aplicación tips de a ejecutar del proceso.

> **Source:** Funcionalidad/ Funcionalidad Pick to voice, Proyecto diseño de aplicación ruta -Req funcionales = Funcionalidad que permite ejecutar procesos mediante las indicaciones de la voz, relacionado a eventos importantes durante el desarrollo de flujos de entregas y recogidas. Es decir que permita programarle al flujo de la aplicación tips de a ejecutar del proceso.

## Funcionalidad/Lectura de codigos  (1)

### ⚠ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir la lectura de códigos de barras lineales y QR por cámara.

> **Source:** Funcionalidad/ Lectura de codigos, Proyecto diseño de aplicación ruta -Req funcionales = Lectura de códigos de barras lineales y QR por cámara.

## Funcionalidad/Notificaciones  (2)

### ⚠ 1  · conf 0.95 p.2
**Statement:** La aplicación debe solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC).

> **Source:** Funcionalidad/ Notificaciónes, Proyecto diseño de aplicación ruta -Req funcionales = Solicitar los permisos mínimos necesarios para operar (ejemplo, Cámara, GPS, Llamadas, ETC)..

### ⚠ 2  · conf 0.95 p.2
**Statement:** La aplicación debe permitir la visualización de ejecución de servicios en línea (doble vía), evidenciando el cambio de estado de forma automática para los diferentes usuarios conectados al medio logístico.

> **Source:** Funcionalidad/ Notificaciónes, Proyecto diseño de aplicación ruta -Req funcionales = Visualización de ejecución de servicios en línea (doble vía), evidenciando el cambio de estado de forma automática para los diferentes usuarios conectados al medio logístico..

## Funcionalidad/Reporte de recaudo  (2)

### ⚠ 1  · conf 0.95 p.2 · NOISE
**Statement:** El reporte de recaudo debe seguir la funcionalidad del Mockup 11 Recaudo de dinero.

> **Source:** Funcionalidad/ Reporte de recaudo, Proyecto diseño de aplicación ruta -Req funcionales = Remitirse a 2) Mockup de referencia- Mockup 11 Recaudo de dinero.

### ⚠ 2  · conf 0.95 p.2
**Statement:** Toda la información capturada y generada por la funcionalidad de recaudo deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

> **Source:** Funcionalidad/ Reporte de recaudo, Proyecto diseño de aplicación ruta -Req funcionales = Toda la información capturada y generada por esta funcionalidad deberá quedar integrada de forma estructurada en el sistema central, permitiendo su consulta posterior, trazabilidad histórica, y disponibilidad para otros módulos o reportes corporativos.

## Funcionalidad/Servicios del dia  (34)

### ✓ req-001  · conf 0.90 p.2 · NOISE
**Statement:** La aplicación debe identificar el subproducto en caso de tratarse de un servicio con características especiales como logística inversa u otros tipos definidos por la operación.

> **Source:** Identificación de subproducto, en caso de tratarse de un servicio con características especiales como logística inversa u otros tipos definidos por la operación.

### ✓ req-002  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de visualización de servicios del día en mapa.

> **Source:** Opción visualización de servicios del dia en mapa

### ✓ req-003  · conf 0.95 p.2
**Statement:** Cada Geo punto de recogida debe tener la opción drill Down donde se visualice la información del servicio.

> **Source:** Que cada Geo punto de recogida tenga la opción dril Down donde se visualice la información del servicio

### ✓ req-004  · conf 0.90 p.2
**Statement:** Cada Geo punto de recogida debe mostrar además las opciones de cómo llegar o contactar.

> **Source:** además  las opciones de cómo llegar o contactar

### ✓ req-005  · conf 0.90 p.2
**Statement:** La información del servicio debe mostrar el Nombre del cliente remitente.

> **Source:** Nombre del cliente remitente

### ✓ req-006  · conf 0.90 p.2
**Statement:** La información del servicio debe mostrar la Dirección de despacho.

> **Source:** Dirección de despacho

### ✓ req-007  · conf 0.90 p.2
**Statement:** La información del servicio debe mostrar las Observaciones asociadas al servicio.

> **Source:** Observaciones asociadas al servicio

### ✓ req-008  · conf 0.90 p.2
**Statement:** La información del servicio debe mostrar el Número de contacto del remitente.

> **Source:** Número de contacto del remitente

### ✓ req-009  · conf 0.95 p.2
**Statement:** La información del servicio debe mostrar la Forma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino).

> **Source:** Forma de pago del servicio (por ejemplo: crédito, recaudo en origen o en destino)

### ✓ req-010  · conf 0.90 p.2
**Statement:** La información del servicio debe mostrar la Identificación de tipo de recogida.

> **Source:** Identificación de tipo de recogida

### ✓ req-011  · conf 0.85 p.2
**Statement:** La información del servicio debe identificar si se trata de un servicio con remesas asociadas.

> **Source:** En caso de tratarse de un servicio con remesas asociadas

### ✓ req-012  · conf 0.85 p.2
**Statement:** La información del servicio debe identificar si se trata de un servicio sin servicios asociados donde se deben crear servicios.

> **Source:** o un servicio sin servicios asociados donde debes crear servicios

### ✓ req-013  · conf 0.85 p.2
**Statement:** La información del servicio debe identificar si se trata de un servicio en el cual se debe sincronizar para relacionar servicios creados por el cliente.

> **Source:** o servicio en el cual debes sincronizar para relacionar servicios creados por el cliente

### ✓ req-014  · conf 0.95 p.2
**Statement:** Para los servicios de entrega la aplicación debe evidenciar visualmente los estados de entregada, pendiente o con novedad.

> **Source:** Para los servicios de entrega espero que visualmente se evidencie los estados de entregada, pendiente o con novedad

### ✓ req-015  · conf 0.90 p.2
**Statement:** Para los servicios de entrega, a medida que se vaya ejecutando debe cambiar y visualizarse el estado.

> **Source:** además a medida que se vaya ejecutando cambie y se visualice el estado

### ✓ req-016  · conf 0.95 p.2
**Statement:** Para los servicios de recogida la aplicación debe evidenciar visualmente los estados de realizada, pendiente o con novedad.

> **Source:** Para los servicios de recogida espero que visualmente se evidencie los estados de realizada, pendiente o con novedad

### ✓ req-017  · conf 0.90 p.2
**Statement:** Para los servicios de recogida, a medida que se vaya ejecutando debe cambiar y visualizarse el estado.

> **Source:** además a medida que se vaya ejecutando cambie y se visualice el estado

### ✓ req-018  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de cumplir entrega.

> **Source:** Opción de cumplir entrega

### ✓ req-019  · conf 0.95 p.2
**Statement:** En cada servicio de entrega, la aplicación debe tener la opción que lo lleve a ejecutar el inicio de flujo de entrega.

> **Source:** En cada servicio de entrega, tenga la opción que lo lleve a ejecutar el inicio de flujo de entrega

### ✓ req-020  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de cumplir recogida.

> **Source:** Opción de cumplir recogida

### ✓ req-021  · conf 0.95 p.2
**Statement:** En cada servicio de recogida, la aplicación debe tener la opción que lo lleve a ejecutar el inicio de flujo de recogida.

> **Source:** En cada servicio de recogida, tenga la opción  que lo lleve a ejecutar el inicio de flujo de recogida

### ✓ req-022  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de plantear novedad de no entrega.

> **Source:** Opción de plantear novedad de no entrega

### ✓ req-023  · conf 0.95 p.2
**Statement:** En cada servicio de entrega, la aplicación debe tener la opción que lo lleve a ejecutar el proceso de planteamiento de novedad.

> **Source:** En cada servicio de entrega, tenga la opción que lo lleve a ejecutar el proceso de planteamiento de novedad

### ✓ req-024  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de plantear novedad de no recolección.

> **Source:** Opción de plantear novedad de no recolección

### ✓ req-025  · conf 0.95 p.2
**Statement:** En cada servicio de recogida, la aplicación debe tener la opción que lo lleve a ejecutar el proceso de planteamiento de novedad (Mirar anexo).

> **Source:** En cada servicio de recogida, tenga la opción que lo lleve a ejecutar el proceso de planteamiento de novedad (Mirar anexo)

### ✓ req-026  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de actualizar listado de servicios.

> **Source:** Opción de actualizar listado de servicios

### ✓ req-027  · conf 0.95 p.2
**Statement:** La aplicación debe permitir refrescar o actualizar el listado de servicios asociados al móvil por parte del colaborador.

> **Source:** Opción que permita refrescar o actualizar el listado de servicios asociados al móvil por parte del colaborador

### ✓ req-028  · conf 0.95 p.2
**Statement:** La aplicación debe tener la opción de realizar la actualización a demanda refrescando el listado de servicios asociados (Recogidas y entregas asociadas a la orden de trabajo).

> **Source:** Esperamos que tenga la opción de realizar la actualización a demanda refrescando el listado de servicios asociados (Recogidas y entregas asociadas a la orden de trabajo)

### ✓ req-029  · conf 0.95 p.2
**Statement:** La aplicación debe generar automáticamente el refresh evidenciando los servicios nuevos asociados a la orden de trabajo de manera automática (Recogidas y entregas) cuando se asocien desde planeación durante la ejecución del proceso de entrega y recogida luego de iniciada el proceso de distribución.

> **Source:** Que se genere automáticamente el refresh, esperamos que se evidencie los servicios nuevos asociados a la orden de trabajo de manera automática (Recogidas y entregas), cuando se asocien desde planeación durante la ejecución del proceso de entrega y recogida luego de iniciada el proceso de distribución

### ✓ req-030  · conf 0.95 p.2
**Statement:** La aplicación debe ofrecer la opción de validación de conexión en línea o no conexión al sistema (Aplicación).

> **Source:** Opción de validación de conexión en línea o no conexión al sistema (Aplicación)

### ✓ req-031  · conf 0.95 p.2
**Statement:** Si durante el proceso de ejecución de la ruta, desde planeación se asocia un nuevo servicio de recogida o entrega a la orden de trabajo, la aplicación debe generar notificación al usuario notificando la asociación de dicho servicio.

> **Source:** Si durante el proceso de ejecución de la ruta, desde planeación se asocia un nuevo servicio de recogida o entrega a la orden de trabajo  se requiere que la aplicación genere notificación al usuario de la aplicación notificando la asociación de dicho servicio

### ✓ req-032  · conf 0.95 p.2
**Statement:** Durante el proceso de ejecución de la ruta, la aplicación debe generar notificación al usuario cuando algún servicio esté a 30 min de cumplir con franjas horarias de entrega o recolección.

> **Source:** Durante el proceso de ejecución de la ruta, necesitamos que la aplicación genere notificación al usuario cuando algún servicio este a 30 min de cumplir con franjas horarias de entrega o recolección

### ✓ req-033  · conf 0.95 p.2
**Statement:** Durante la ejecución, cuando termine de certificar una entrega o recogida e indique próxima parada a ser atendida, la aplicación debe generar una notificación al cliente remitente o destinatario relacionado a proximidad a entrega de su servicio.

> **Source:** Durante la ejecución, cuando termine de certificar una entrega o recogida e indique próxima parada a ser atendida espero que la aplicación le genere una notificación al cliente remitente o destinatario relacionado a proximidad a entrega de su servicio

### ✓ req-034  · conf 0.95 p.2
**Statement:** La aplicación debe tener la opción de parametrizar notificaciones y/o mensajes que se requiera enviar al usuario durante la ejecución de su orden de trayecto, de forma masiva.

> **Source:** Esperamos que se tenga la opción de parametrizar notificaciones y/o mensajes que se requiera enviar al usuario durante la ejecución de su orden de trayecto, se forma masiva

## General - Gestión de cambios  (1)

### ✓ 1  · conf 0.93 p.3
**Statement:** En caso de no ser un SaaS, se deben adjuntar los lineamientos del control de cambios alineado a la organización TCC.

> **Source:** En caso de no ser un SaaS, adjuntar los lineamientos del control de cambios, debe estar alineado a la organización TCC (se adjunta lineamientos de TCC).

## Infraestructura  (3)

### ✓ 1  · conf 0.96 p.3
**Statement:** Todos los componentes arquitectónicos de la solución como servidor de aplicaciones, bases de datos y Apis de integración deben estar soportados en una arquitectura escalable, de alta disponibilidad (superior 99.95%) y con calidad del servicio (superior 99,6%).

> **Source:** Todos los componentes arquitectónicos de la solución como servidor de  aplicaciones, bases de datos y Apis de integración deben estar soportados en una arquitectura escalable, de alta disponibilidad (superior 99.95%) y con calidad del servicio (superior 99,6%).

### ✓ 2  · conf 0.93 p.3
**Statement:** Se debe detallar el esquema de evolución de la aplicación indicando cómo se atenderán nuevos requerimientos y actualizaciones tecnológicas.

> **Source:** Detallar esquema de evolución de la aplicación (como se atenderán nuevos requerimientos y actualizaciones tecnológicas. La propuesta económica debe incluir costos de esta evolución tecnológica).

### ✓ 3  · conf 0.94 p.3 · NOISE
**Statement:** La propuesta económica debe incluir costos de la evolución tecnológica.

> **Source:** Detallar esquema de evolución de la aplicación (como se atenderán nuevos requerimientos y actualizaciones tecnológicas. La propuesta económica debe incluir costos de esta evolución tecnológica).

## Infraestructura - BCP  (1)

### ✓ 1  · conf 0.97 p.3
**Statement:** Se debe adjuntar plan de continuidad del negocio del sistema ante fallos.

> **Source:** Adjuntar plan de continuidad del negocio del sistema ante fallos.

## Infraestructura - Backups  (1)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se debe adjuntar política de backup, incluyendo plan de retención que especifique los tiempos de recuperación, frecuencia de ejecución de backups y tipo de backup que se ejecuta (full e incremental).

> **Source:** Adjuntar política de backup, incluyendo plan de retención que especifique los tiempos de recuperación, frecuencia de ejecución de backups y tipo de backup que se ejecuta (full e incremental).

## Infraestructura - Conectividad  (2)

### ✓ 1  · conf 0.94 p.3
**Statement:** Se debe asegurar interoperabilidad con los sistemas de la organización (ej. ERP, CRM, base datos, etc.) adjuntando soportes y documentación.

> **Source:** Asegurar interoperabilidad con los sistemas de la organización (ej. ERP, CRM, base datos, etc.). Adjuntar soportes y documentación.

### ✓ 2  · conf 0.95 p.3
**Statement:** Se deben asegurar las soluciones para conexión privada y dedicada entre los entornos de la compañía y la nueva solución (ej. FastConnect, VPN, etc.).

> **Source:** Asegurar las soluciones para conexión privada y dedicada entre los entornos de la compañía y la nueva solución (ej. FastConnect, VPN, etc.).

## Infraestructura - DRP  (1)

### ✓ 1  · conf 0.95 p.3
**Statement:** Se debe adjuntar diagrama del modelo de recuperación del sistema ante fallos indicando cuál es el RTO y RPO y dónde estaría el servicio alojado.

> **Source:** Adjuntar diagrama del modelo de recuperación del sistema ante fallos, ¿cual es el RTO y RPO? ¿Dónde estaría el servicio alojado?.

## Infraestructura - Gestión de la capacidad  (1)

### ✓ 1  · conf 0.94 p.3
**Statement:** Se debe adjuntar procesos que soporten la gestión de la capacidad, donde se evidencie cómo se planifica el crecimiento y uso de recursos con una estrategia de optimización del rendimiento.

> **Source:** Adjuntar procesos que soporten la gestión de la capacidad, donde se evidencie como se planifica el crecimiento y uso de recursos con una estrategia de optimización del rendimiento..

## Infraestructura - Gestión de la configuración  (1)

### ✓ 1  · conf 0.93 p.3
**Statement:** Se debe adjuntar procesos que soporten la gestión de la configuración, donde se evidencie cómo se ejecutan control de activos, actualización de los sistemas software y hardware, entre otros.

> **Source:** Adjuntar procesos que soporte la gestión de la configuración, donde se evidencia como se ejecutan control de activos, actualización de los sistemas software y hardware, entre otros)..

## Infraestructura - Gestión de riesgos  (1)

### ✓ 1  · conf 0.95 p.3
**Statement:** Se debe adjuntar matriz de riesgos a nivel de infraestructura de la evaluación más reciente que se realizó y el plan de acción ejecutado.

> **Source:** Adjuntar matriz de riesgos a nivel de infraestructura de la evaluación mas reciente que se realizó y el plan de acción ejecutado.

## Infraestructura - Informes  (2)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se debe entregar reportes de monitoreo, rendimiento y uso de recursos con una periodicidad mensual.

> **Source:** Entrega de reportes de monitoreo, rendimiento y uso de recursos con una periodicidad mensual y acceso a dashboard en tiempo real.

### ✓ 2  · conf 0.96 p.3
**Statement:** Se debe proveer acceso a dashboard en tiempo real de monitoreo, rendimiento y uso de recursos.

> **Source:** Entrega de reportes de monitoreo, rendimiento y uso de recursos con una periodicidad mensual y acceso a dashboard en tiempo real.

## Infraestructura - Migración  (1)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se deben adjuntar soportes de documentación donde se especifique la facilidad de migración desde y hasta su plataforma.

> **Source:** Adjuntar soportes de documentación donde se especifique la facilidad de migración desde y hasta su plataforma.

## Infraestructura - Networking  (1)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se debe integrar con APIS, SOAP, Rest web sockets.

> **Source:** Integrar con APIS, SOAP, Rest web sockets.

## Infraestructura - Relaciones entre CI  (1)

### ✓ 1  · conf 0.94 p.3
**Statement:** En caso de no ser un SaaS, se debe adjuntar diagramas de relaciones entre CI (items de configuración) en los sistemas y componentes.

> **Source:** En caso de no ser un SaaS, adjuntar  diagramas de relaciones entre CI (items de configuración) en los sistemas y componentes.

## Infraestructura - Seguridad de redes  (1)

### ✓ 1  · conf 0.95 p.3
**Statement:** Se deben compartir políticas de seguridad de la información de la compañía que detalle los componentes de redes (Ej. Firewall, WAF, etc.).

> **Source:** Compartir políticas de seguridad de la información de la compañía que detalle los componentes de redes (Ej. Firewall, WAF, etc.)…

## Instrucciones de Respuesta  (1)

### ⚠ 1  · conf 0.90 p.2 · NOISE
**Statement:** El proveedor debe marcar con una "X" si la(s) solución(es) que ofrece cubre cada necesidad detallada por TCC, de acuerdo a las opciones A (listo para utilizarse), B (desarrollo sin costo), C (desarrollo con costo adicional) o NS (No soportado).

> **Source:** Marque con una "X", si la(s) solución(es) que Ud. ofrece, cubre cada necesidad detallada por TCC, de acuerdo a las siguientes opciones:
A, Tipo de soporte por funcionalidad = Soportado al entregarse "listo para utilizarse". B, Tipo de soporte por funcionalidad = Soportado mediante desarrollo sin costo. C, Tipo de soporte por funcionalidad = Soportado mediante desarrollo con costo adicional ( este costo deberá ser tenido en cuenta en la propuesta económica). NS, Tipo de soporte por funcionalidad = No soportado

## Reporte BI/Modulo Administrativo  (7)

### ⚠ 1  · conf 0.95 p.2
**Statement:** La solución debe integrarse con power BI, mediante la estructuración de un reporte que permita evidenciar la usabilidad de las funcionalidades de la aplicación, por orden de trabajo, usuario y proceso.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Integración con power BI, mediante la estructuración de un reporte que permita evidenciar la usabilidad de las funcionalidades de la aplicación, por orden de trabajo, usuario y proceso.

### ⚠ 2  · conf 0.95 p.2
**Statement:** La aplicación debe contar con un módulo administrativo que permita configurar y/o parametrizar notificaciones para los usuarios de la aplicación.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Modulo administrativo que permita configurar y/o parametrizar notificaciones para los usuarios de la aplicación..

### ⚠ 3  · conf 0.95 p.2
**Statement:** La aplicación debe contar con un módulo administrativo que permita configurar y/o parametrizar notificaciones para los clientes destinatarios del servicio.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Modulo administrativo que permita configurar y/o parametrizar notificaciones para los clientes destinatarios del servicio.

### ⚠ 4  · conf 0.95 p.2
**Statement:** La aplicación debe contar con un módulo administrativo que permita configurar y/o parametrizar notificaciones para los clientes remitente del servicio.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Modulo administrativo que permita configurar y/o parametrizar notificaciones para los clientes remitente del servicio.

### ⚠ 5  · conf 0.95 p.2
**Statement:** La aplicación debe contar con un módulo administrativo de configuración relacionado con roles de la aplicación.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Modulo administrativo de configuración relacionado con roles de la aplicación.

### ⚠ 6  · conf 0.95 p.2
**Statement:** La solución debe integrarse con power bi, mediante el desarrollo de un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla, que evidencie el desempeño de las tripulaciones logísticas.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Integración con power bi, mediante el desarrollo de un reporte de productividad que permita ver la evolución de las órdenes de trabajo de primera y última milla, que evidencie el desempeño de las tripulaciones logísticas.

### ⚠ 7  · conf 0.95 p.2
**Statement:** La solución debe integrarse con power Bi, mediante el desarrollo de un reporte que capture el comportamiento de cierre de ordenes de trabajo, con los servicios pendientes, ejecutados, por orden de trabajo.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Integración con power Bi, mediante el desarrollo de un reporte que capture el comportamiento de cierre de ordenes de trabajo, con los servicios pendientes, ejecutados, por orden de trabajo.

## Reporte BI/Modulo Administrativo - Módulo de Configuración de Flujos  (7)

### ⚠ 1  · conf 0.95 p.2
**Statement:** La aplicación deberá contar con un módulo que permita la configuración dinámica de flujos operativos, con el fin de adaptar y optimizar los procesos internos según las necesidades del negocio.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = La aplicación deberá contar con un módulo que permita la configuración dinámica de flujos operativos, con el fin de adaptar y optimizar los procesos internos según las necesidades del negocio.

### ⚠ 2  · conf 0.95 p.2
**Statement:** El módulo de configuración de flujos debe contar con una interfaz gráfica para la creación y edición de flujos.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Interfaz gráfica para la creación y edición de flujos..

### ⚠ 3  · conf 0.95 p.2
**Statement:** El módulo de configuración de flujos debe permitir la definición de condiciones, reglas y acciones automatizadas (por ejemplo: asignación de pedidos, validaciones, notificaciones).

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Posibilidad de definir condiciones, reglas y acciones automatizadas (por ejemplo: asignación de pedidos, validaciones, notificaciones)..

### ⚠ 4  · conf 0.95 p.2
**Statement:** El módulo de configuración de flujos debe permitir la integración con otros módulos del sistema (logística, pagos, usuarios, etc.).

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Integración con otros módulos del sistema (logística, pagos, usuarios, etc.)..

### ⚠ 5  · conf 0.95 p.2
**Statement:** El módulo de configuración de flujos debe permitir el control de versiones y trazabilidad de cambios en los flujos.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Control de versiones y trazabilidad de cambios en los flujos..

### ⚠ 6  · conf 0.95 p.2
**Statement:** El módulo de configuración de flujos debe tener capacidad para activar o desactivar flujos según contexto operativo o necesidad específicas.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Capacidad para activar o desactivar flujos según contexto operativo o necesidad específicas..

### ⚠ 7  · conf 0.95 p.2
**Statement:** El módulo de configuración de flujos debe tener capacidad para activar o desactivar campos de las diferentes pantallas, según contexto operativo o necesidad específicas.

> **Source:** Reporte BI/Modulo Administrativo, Proyecto diseño de aplicación ruta -Req funcionales = Capacidad para activar o desactivar campos de las diferentes pantallas,  según contexto operativo o necesidad específicas..

## Servicio de recogidas > Pantalla inicio de ejecución de recolección  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** Se debe contar con una pantalla de inicio de proceso de recolección con las opciones de realizar recogida, no realizar, información detalle del servicio, unidades y opción ubicación mapa

> **Source:** Se cuente con una pantalla de inicio proceso de recolección donde tenga las opciones de realizar recogida, no realizar, información detalle del servicio, unidades, opción ubicación mapa.

## Servicio de recogidas > Pantalla inicio de ejecución de recolección > Opción de no realizar entrega  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** Al ejecutar la opción de no realizar recolección, es necesario que lleve al planteamiento de novedad (Motivo de no recolección)

> **Source:** Es necesario que al ejecutar la opción lo lleve al  planteamiento de novedad (Motivo de no recolección).

## Servicio de recogidas > Pantalla inicio de ejecución de recolección > Opción información detalle  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe mostrar información detalle del servicio con: nombre del remitente, ID remitente, dirección remitente, nombre solicitante, ID solicitante, ciudad solicitante, ventana horaria, cantidad de unidades, forma de pago, subproducto servicio, teléfono remitente, teléfono solicitante, unidad de negocio, licencia, cuenta, valor de la mercancía y servicios asociados a la recolección

> **Source:** Opción que al ejecutar lo lleve a observar información detalle del servicio relacionando la siguiente información:1.Nombe del remitente2.ID remitente 3.Dirección remitente4-Nombre solicitante5.Id de solicitante6.Ciudad solicitante7.Ventana horaria8.Cantidad de unidades 9.Forma de pago 10, Subproducto servicio 11.Telefono remitente12, Telefono solicitante13, Unidad de negocio14, Licencia15, Cuenta16, Valor de la mercancía 17.Servicios asociados a la recolección.

## Servicio de recogidas > Pantalla inicio de ejecución de recolección > Opción información detalle unidades relacionadas a la recolección  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** Al ver las unidades asociadas a la recolección, se debe evidenciar por unidad: código IUP de la unidad, tipo de unidad, clase de empaque, peso, volumen y notas

> **Source:** Necesitamos que, al ejecutar la opción de ver las unidades asociadas a la recolección, se evidencie la siguiente información por unidad:1.Codigo IUP de la unidad 2.Tipo de unidad3.Clase de empaque4.Peso5.Volumen6.Notas

## Servicio de recogidas > Pantalla inicio de ejecución de recolección > Validaciones de servicio  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** Es necesario que la aplicación realice validaciones antes de ejecutar un servicio tales como si tiene conceptos de recaudo de dinero asociados al servicio

> **Source:** Es necesario que realice validaciones antes de ejecutar un servicio tales como:  si tiene conceptos de recaudo de dinero asociados al servicio.

## Servicio entregas > Certificación de Servicios Agrupados por Destinatario en Parada Única > Agrupación automática de servicios  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** Al sincronizar los servicios del día, el sistema debe identificar aquellos que comparten el mismo destinatario y punto de entrega, agrupándolos en una única parada

> **Source:** Al sincronizar los servicios del día, el sistema debe identificar aquellos que comparten el mismo destinatario y punto de entrega, agrupándolos en una única parada.

## Servicio entregas > Certificación de Servicios Agrupados por Destinatario en Parada Única > Interfaz unificada de certificación  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe presentar una vista consolidada que permita al auxiliar certificar todos los servicios agrupados mediante captura de firma del destinatario, registro de observaciones generales o específicas por servicio, y toma de evidencia fotográfica única o por servicio según configuración

> **Source:** La aplicación debe presentar una vista consolidada que permita al auxiliar certificar todos los servicios agrupados mediante:Captura de firma del destinatario.Registro de observaciones generales o específicas por servicio.Toma de evidencia fotográfica única o por servicio, según configuración.

## Servicio entregas > Certificación de Servicios Agrupados por Destinatario en Parada Única > Validación de certificación  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** El sistema debe validar que todos los servicios agrupados hayan sido correctamente certificados antes de permitir el cierre de la parada

> **Source:** El sistema debe validar que todos los servicios agrupados hayan sido correctamente certificados antes de permitir el cierre de la parada.

## Servicio entregas > Certificación de la entrega a destinatario  (6)

### ✓ 1  · conf 0.95 p.2
**Statement:** La pantalla debe tener la opción de captura y almacenamiento de fotografía

> **Source:** La pantalla debe de tener la opción de captura y almacenamiento de fotografía, la aplicación debe tener la capacidad de capturar máx. 10 fotografías.

### ✓ 2  · conf 0.95 p.2
**Statement:** La aplicación debe tener la capacidad de capturar máximo 10 fotografías

> **Source:** la aplicación debe tener la capacidad de capturar máx. 10 fotografías.

### ✓ 3  · conf 0.95 p.2
**Statement:** La pantalla debe tener la opción de captura y almacenamiento de firma digital

> **Source:** La pantalla debe de tener la opción de captura y almacenamiento de firma digital.

### ✓ 4  · conf 0.95 p.2
**Statement:** La aplicación debe permitir seleccionar el tipo de destinatario entre las opciones: Unidad residencial, Domicilio, Empresa, Tercero autorizado

> **Source:** Opción que permita seleccionar de acuerdo con el tipo de destinario, relacionando las siguientes opciones:1.Unidad residencial 2.Domicilio3.Empresa4.Tercero autorizado.

### ✓ 5  · conf 0.95 p.2
**Statement:** La aplicación debe capturar relacionado a la certificación el usuario, fecha, hora, latitud, longitud y vehículo

> **Source:** Que la aplicación capture relacionado a la certificación, el usuario, fecha, hora, latitud, longitud y vehículo.

### ✓ 6  · conf 0.95 p.2
**Statement:** La aplicación debe tener la opción de pick to voice para capturar información en el campo observaciones

> **Source:** La aplicación tenga la opción de pick to voice para capturar información en el campo observaciones.

## Servicio entregas > Envío de prueba digital (POD)  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe generar el servicio con los datos capturados en la certificación para la estructuración del POD (Comprobante de prueba digital)

> **Source:** La aplicación genere el servicio con los datos capturados en la certificación,  para la estructuración del POD (Comprobante de prueba digital).

### ✓ 2  · conf 0.95 p.2
**Statement:** La aplicación debe generar el evento de notificación para el cliente destinatario con el POD

> **Source:** La aplicación genere el evento de notificación para el cliente destinatario con el POD.

## Servicio entregas > Finalización de certificación con éxito  (4)

### ✓ 1  · conf 0.90 p.2
**Statement:** La aplicación debe evidenciar la culminación exitosa de la certificación

> **Source:** Opción donde se evidencie la culminación exitosa, y evidencie las opciones de navegar a la próxima parada, o ver servicios del día.

### ✓ 2  · conf 0.90 p.2
**Statement:** La aplicación debe evidenciar las opciones de navegar a la próxima parada o ver servicios del día

> **Source:** Opción donde se evidencie la culminación exitosa, y evidencie las opciones de navegar a la próxima parada, o ver servicios del día.

### ✓ 3  · conf 0.95 p.2
**Statement:** Al ejecutar la opción navegar próxima parada, la aplicación debe llevarlo a ejecutar el próximo servicio que sugiere la planeación

> **Source:** Al ejecutar la opción navegar próxima parada, la aplicación debe llevarlo a ejecutar el próximo servicio que sugiere la planeación.

### ✓ 4  · conf 0.95 p.2
**Statement:** La aplicación debe emitir una notificación automática al destinatario al seleccionar próximo servicio desde el cierre en la certificación del servicio

> **Source:** La aplicación debe emitir una notificación automática al destinatario, relacionado al seleccionar próximo servicio desde el cierre en la certificación del servicio.

## Servicio entregas > Funcionalidad de diligenciamiento de documentos digitales  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir capturar, parametrizar y enviar documentos digitales tales como Boomerang y dardos

> **Source:** Permita capturar, parametrizar y enviar documentos digitales. Tales como Boomerang y dardos.

## Servicio entregas > Registro de motivos de no entrega (Novedad)  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir al usuario seleccionar un motivo de no entrega desde una lista predefinida (destinatario ausente, dirección incorrecta, mercancía averiada, rechazo del cliente, entre otros)

> **Source:** La aplicación debe permitir al usuario seleccionar un motivo de no entrega desde una lista predefinida (por ejemplo: destinatario ausente, dirección incorrecta, mercancía averiada, rechazo del cliente, entre otros).

### ✓ 2  · conf 0.95 p.2
**Statement:** La aplicación debe permitir al usuario registrar por pick to voice la descripción del evento, registrarlo e integrarlo con servicio de planteamiento

> **Source:** La aplicación debe permitir al usuario registrar por pick to voice la descripción del evento, registrarlo e integrarlo con servicio de planteamiento.

## Servicio entregas > Registro de motivos de no entrega (Novedad) > Asociación con el Servicio  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** Cada motivo de no entrega, fotografía y observación debe estar vinculado al número de guía o servicio correspondiente

> **Source:** Cada motivo de no entrega, fotografía y observación debe estar vinculado al número de guía o servicio correspondiente.

### ✓ 2  · conf 0.95 p.2
**Statement:** Debe existir trazabilidad completa de cada intento de entrega fallido

> **Source:** Debe existir trazabilidad completa de cada intento de entrega fallido.

## Servicio entregas > Registro de motivos de no entrega (Novedad) > Captura de Evidencia Fotográfica  (3)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir tomar fotografías directamente desde el dispositivo móvil en el momento de registrar la novedad

> **Source:** La aplicación debe permitir tomar fotografías directamente desde el dispositivo móvil en el momento de registrar la novedad.

### ✓ 2  · conf 0.95 p.2
**Statement:** La aplicación debe tener la capacidad de validar que la fotografía tomada sea tomada de manera correcta y coherente con el motivo de no entrega planteado

> **Source:** La aplicación debe tener la capacidad de validar que la fotografía tomada sea tomada de manera correcta y coherente con el motivo de no entrega planteado.

### ✓ 3  · conf 0.95 p.2
**Statement:** Las imágenes deben almacenarse junto con la información del servicio y estar disponibles para consulta posterior

> **Source:** Las imágenes deben almacenarse junto con la información del servicio y estar disponibles para consulta posterior.

## Servicio entregas > Registro de motivos de no entrega (Novedad) > Descripción de validaciones y reglas  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe realizar validaciones para dejar plantear motivos de no entrega, tal como validación de ubicación (cercanía a parada)

> **Source:** La aplicación debe realizar validaciónes para dejar plantear motivos de no entrega, tal como validación de ubicación (cercania a parada).

### ✓ 2  · conf 0.95 p.2
**Statement:** La aplicación debe realizar validaciones para dejar plantear motivos de no entrega, tal como validación de franja horaria

> **Source:** La aplicación debe realizar validaciones para dejar plantear motivos de no entrega, tal como validación de franja horaria.

## Servicio entregas > Registro de motivos de no entrega (Novedad) > Descripción de validaciones y reglas de campos obligatorios  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir parametrizar campos de planteamiento de obligación tal como observación, fotografía y cantidad de caracteres

> **Source:** La aplicación debe permitir parametrizar campos de planteamiento de obligación tal como observación, fotografía y cantidad de caracteres.

## Servicio entregas > Registro de motivos de no entrega (Novedad) > Ingreso de Observaciones  (2)

### ✓ 1  · conf 0.95 p.2
**Statement:** El usuario debe poder ingresar observaciones adicionales en texto libre para complementar la información del motivo de no entrega

> **Source:** El usuario debe poder ingresar observaciones adicionales en texto libre para complementar la información del motivo de no entrega.

### ✓ 2  · conf 0.95 p.2
**Statement:** Las observaciones deben incluir campos como: nombre de la persona que informa, hora del intento de entrega, y cualquier detalle relevante

> **Source:** Las observaciones deben incluir campos como: nombre de la persona que informa, hora del intento de entrega, y cualquier detalle relevante.

## Servicio entregas > Registro de motivos de no entrega a nivel de IUP (Novedad)  (1)

### ✓ 1  · conf 0.95 p.2
**Statement:** La aplicación debe permitir el registro de novedades a nivel de IUP (Unidad)

> **Source:** La aplicación permita el registro de novedades a nivel de IUP  (Unidad).

## Soporte - ANS  (1)

### ✓ 1  · conf 0.93 p.3
**Statement:** Se deben definir tiempos de respuesta de acuerdo a prioridad e impacto ANS indicando cuáles son las categorías que se atienden en el soporte y cuál es el tiempo del primer contacto y de la solución definitiva por categoría.

> **Source:** Tiempos de respuesta de acuerdo a prioridad e impacto ANS (cuales son las categorías que se atienden en el soporte y cuales es el tiempo de el primer contacto y de la  solución definitiva por categoría.).

## Soporte - Alcance del soporte  (2)

### ✓ 1  · conf 0.90 p.3
**Statement:** Se debe indicar el alcance del soporte entendiendo si es solo para incidentes (una interrupción del servicio) o incluso para mejoras o nuevas funcionalidades, incluyendo horarios de atención y costos de horas adicionales.

> **Source:** (entender si es solo para incidentes una interrupción del servicio, o incluso para mejoras o nuevas funcionalidades?) Horarios de atención y en caso de ser necesario costos de horas adicionales de soporte..

### ✓ 2  · conf 0.93 p.3
**Statement:** Se debe explicar si el soporte es sólo para interrupción del servicio o incluso para mejoras o nuevas funcionalidades, incluir en la propuesta según sea el caso.

> **Source:** Explicar si el soporte es sólo para interrupción del servicio o incluso para mejoras o nuevas funcionalidades, incluir en la propuestas según sea el caso.

## Soporte - Disponibilidad  (1)

### ✓ 1  · conf 0.98 p.3
**Statement:** La disponibilidad mínima debe ser del 99,95%.

> **Source:** Disponibilidad mínima del 99,95%.

## Soporte - Herramienta de seguimiento  (2)

### ✓ 1  · conf 0.92 p.3
**Statement:** Se debe indicar por cuáles medios se registra un incidente o requerimiento (llamada, ITSM, correo).

> **Source:** Por cuales medios se registra un incidente o requerimiento. llamada, ITSM, correo?).

### ✓ 2  · conf 0.92 p.3
**Statement:** En el caso en que el reporte del requerimiento sea a través de un ITSM, se debe indicar si se puede integrar con Service Desk con la idea de tener una idea general y centralizada del servicio.

> **Source:** Ene el caso en que el reporte del requerimiento sea atreves de un ITSM indicar si se puede integrar con Service Desk; esto con la idea de tener una idea general y centralizada del servicio.

## Soporte - Matriz de escalamiento  (3)

### ✓ 1  · conf 0.94 p.3
**Statement:** Se debe entregar matriz de escalamiento que contenga niveles.

> **Source:** Entregar matriz de escalamiento que contenga: (Niveles, tiempos para escalar al siguiente nivel, cuales son los eventos en que escala al siguiente nivel ejemplo cuando no contestan o  cuando no se encuentra una solución en el nivel anterior.).

### ✓ 2  · conf 0.94 p.3
**Statement:** Se debe entregar matriz de escalamiento que contenga tiempos para escalar al siguiente nivel.

> **Source:** Entregar matriz de escalamiento que contenga: (Niveles, tiempos para escalar al siguiente nivel, cuales son los eventos en que escala al siguiente nivel ejemplo cuando no contestan o  cuando no se encuentra una solución en el nivel anterior.).

### ✓ 3  · conf 0.94 p.3
**Statement:** Se debe entregar matriz de escalamiento que contenga cuáles son los eventos en que escala al siguiente nivel.

> **Source:** Entregar matriz de escalamiento que contenga: (Niveles, tiempos para escalar al siguiente nivel, cuales son los eventos en que escala al siguiente nivel ejemplo cuando no contestan o  cuando no se encuentra una solución en el nivel anterior.).

## Soporte - Modalidad de Soporte  (1)

### ✓ 1  · conf 0.93 p.3
**Statement:** Se debe indicar cómo se prestará el soporte (de manera remota o en sitio) indicando cuál será la programación del recurso asignado.

> **Source:** Indicar como se prestará el soporte (de manera remoto, o en sitio) en cualquiera de los caso indicar cual será programación del recurso asignados.

## Soporte - Monitoreo  (2)

### ✓ 1  · conf 0.95 p.3
**Statement:** Se debe realizar el registro de eventos críticos en herramientas de monitoreo o permitir el entregable a estos (Transaccionales y de accesos).

> **Source:** Registro de eventos críticos en herramientas de monitoreo o permitir el entregable a estos (Transaccionales y de accesos) Indicar como se garantiza.

### ✓ 2  · conf 0.96 p.3
**Statement:** Se debe permitir la integración con herramientas de monitoreo TCC para la identificación temprana de fallos en los componentes de la solución (servicios, Kubernetes, plataformas, etc.).

> **Source:** Permitir la integración con herramientas de monitoreo TCC para la identificación temprana de fallos en los componentes de la solución (servicios, Kubernetes, plataformas, etc.).

## Soporte - Monitoreo y observabilidad  (2)

### ✓ 1  · conf 0.96 p.3
**Statement:** Se deben adjuntar soportes de las herramientas de monitoreo de los servicios en tiempo real que incluya la herramienta ofertada.

> **Source:** Adjuntar soportes de las herramientas de monitoreo de los servicios en tiempo real que incluya la herramienta ofertada.

### ✓ 2  · conf 0.94 p.3
**Statement:** Se debe indicar si la herramienta de monitoreo se integra con aplicaciones de observabilidad y especificar cómo sería.

> **Source:** Indicar si la herramienta se integra con aplicaciones se observabilidad y especificar cómo sería..
