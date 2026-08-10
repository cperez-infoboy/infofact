# Contexto para Implementación: Fase de Análisis y Diseño en InfoFact

> **Uso:** Pegar este texto como contexto inicial en una nueva sesión de Claude Code para implementar la fase de análisis y diseño en InfoFact.
>
> **Fecha de elaboración:** 2026-08-06
> **Sesión de origen:** Exploración de viabilidad para generación de software con modelos pequeños

---

## Qué implementar

InfoFact actualmente captura requerimientos (Fase 1, operativa). El siguiente paso es implementar la **fase de análisis y diseño del software** que esos requerimientos describen. Esta fase debe producir artefactos que **faciliten una futura fase de desarrollo automático** donde modelos pequeños (SLMs locales vía Ollama) generen código componente por componente.

## Por qué el análisis debe orientarse al desarrollo con SLMs

La meta de largo plazo es que InfoFact, después del análisis y diseño, pueda descomponer el software en **componentes hoja lo suficientemente simples** para que un modelo local de 7B-14B los implemente correctamente a partir de un contrato. Esto fue validado en piloto (Qwen3.5:9B, 8/8 tests pasaron con un contrato bien especificado).

Por lo tanto, **todo lo que se diseñe en esta fase debe estar estructurado para alimentar esa descomposición**. Los artefactos de análisis no son documentación pasiva — son la materia prima que permitirá (en la fase siguiente) dividir el sistema en componentes implementables por IA.

## Modelo a usar en esta fase

**IMPORTANTE:** Esta fase de análisis y diseño usa el **mismo LLM grande que la captura de requerimientos** (GLM-5.2 vía `backend/agents/llm.py`). El razonamiento arquitectónico — generar MER, identificar agregados, diseñar procesos, proponer sub-proyectos, redactar ADRs — requiere el modelo grande. **No usar Ollama ni modelos pequeños en esta fase.**

Los modelos pequeños (Ollama / Qwen3.5:9B) son exclusivos de la **futura fase de desarrollo**, donde implementarán componentes hoja individuales. Esa fase no se implementa ahora.

## Qué debe producir la fase de análisis y diseño

### 1. Modelo Entidad-Relación (MER)

Identificar las entidades del dominio desde los requerimientos capturados, sus atributos, relaciones, cardinalidades y **agregados** (grupos de entidades que se manejan como unidad de consistencia). El MER es la base para:

- Identificar contextos acotados (cada agregado raíz tiende a definir un contexto)
- Estimar complejidad por área del dominio (un contexto con 15 entidades es complejo; uno con 2 es trivial)
- Definir las entidades de dominio que luego serán componentes en la descomposición hexagonal

Formato: **Mermaid.js** (texto, renderizable en el navegador, comprensible por asistentes de IA como Claude Code).

### 2. Diagramas de procesos

Máquinas de estados para entidades con ciclo de vida, diagramas de secuencia para interacciones clave, flujos de eventos. Estos diagramas permiten:

- Identificar los casos de uso (cada transición de estado o paso de proceso es un caso de uso candidato)
- Identificar servicios de dominio (lógica que no pertenece a una sola entidad)
- Decidir patrones de comunicación entre componentes (¿síncrono o asíncrono?)

Formato: **Mermaid.js**.

### 3. Análisis de requerimientos no funcionales

Extraer y clasificar los RNF de los `RequirementItem` con tipo `non_functional`. Producir decisiones sobre:

- Stack tecnológico por área
- Patrones arquitectónicos (CQRS, event-driven, caching)
- Estrategia de consistencia de datos

### 4. Architecture Decision Records (ADRs)

Cada decisión arquitectónica importante con su contexto, alternativas consideradas y rationale. Esto será parte del `CLAUDE.md` de cada sub-proyecto generado.

### 5. Propuesta de componentes y sub-proyectos

A partir del MER, los procesos y los RNFs, el análisis debe producir una **propuesta de arquitectura de componentes** que define los sub-proyectos necesarios para resolver el desarrollo. Por ejemplo: un proyecto de frontend, uno de backend, uno de API gateway, uno de infraestructura, etc.

Esta propuesta debe incluir:

- **Lista de sub-proyectos identificados**, cada uno con:
  - Nombre y responsabilidad principal (ej: "Frontend SPA — interfaz de usuario", "Backend API — lógica de negocio y persistencia")
  - Stack tecnológico recomendado (ej: SvelteKit para frontend, FastAPI para backend)
  - Contextos acotados del MER que agrupa (qué entidades y procesos le corresponden)
  - RNFs que aplica a este sub-proyecto (performance, seguridad, etc.)
- **Contratos entre sub-proyectos** — las interfaces que cada sub-proyecto expone o consume de otros. Por ejemplo, si el frontend consume una API REST del backend, el contrato es un OpenAPI spec. Si dos servicios backend se comunican por eventos, el contrato es el esquema de eventos. Estos contratos inter-proyecto son **shared artifacts** que todos los sub-proyectos referencian.
- **Diagrama de componentes** (Mermaid) mostrando cómo se relacionan los sub-proyectos entre sí y qué contratos los conectan.

Esta propuesta es la **guía de partición** para la fase de desarrollo. Cuando la fase de implementación comience, cada sub-proyecto será un repo convencional independiente con su propio `CLAUDE.md`, estructura estándar, tests y contrato de interfaces.

**Relación con la descomposición futura:** dentro de cada sub-proyecto, se aplicará después la descomposición hexagonal para llegar a componentes hoja implementables por SLMs. Pero definir QUÉ sub-proyectos existen y CÓMO se conectan entre sí es responsabilidad de esta fase de análisis y diseño. Sin esta propuesta, la descomposición hexagonal no tiene límites claros.

## Qué debe NO hacer esta fase (todavía)

- **No usar modelos pequeños (Ollama).** Esta fase usa el LLM grande (GLM-5.2) igual que la captura. Los SLMs se reservan para la fase de desarrollo.
- **No descomponer en componentes hexagonales internos** — eso es la fase de desarrollo. Esta fase define los sub-proyectos y sus fronteras, no los componentes internos de cada uno.
- **No generar contratos de implementación ni código** — eso es la fase de desarrollo. Los contratos que se generan aquí son **contratos inter-proyecto** (API specs), no contratos de componentes hoja.

Esta fase produce **el diseño arquitectónico completo del sistema** — desde el entendimiento del dominio (MER, procesos) hasta la definición de la estructura de sub-proyectos y cómo se conectan.

## Cómo encaja con la arquitectura existente de InfoFact

### Patrones a seguir

InfoFact ya tiene patrones establecidos que esta fase debe replicar:

- **Subagente dedicado:** así como `requirements_capture_agent.py` maneja la captura, crear un `analysis_design_agent.py` que maneje el análisis. Mirar el patrón en `backend/agents/subagents/requirements_capture_agent.py` (43.7K, es la referencia principal de cómo se estructura un subagente con etapas).
- **Pipeline por etapas:** los pipelines en `backend/agents/pipelines/` (classification.py, extraction.py, ingestion.py, consolidation.py, critique.py) siguen un patrón consistente. El análisis debe crear pipelines nuevos: al menos `mer_generation.py`, `process_analysis.py` y `project_partitioning.py`.
- **Run holder con estado:** `capture_run_holder.py` y `srs_run_holder.py` mantienen estado por ejecución. Se necesita un `analysis_run_holder.py` equivalente.
- **Tools del agente:** `backend/agents/tools/` contiene las herramientas LangChain del agente. El análisis necesita tools nuevas para leer el SRS, leer RequirementItems y producir artefactos Mermaid.
- **LLM:** usar la misma configuración de `backend/agents/llm.py` que ya usa la captura. No configurar un LLM distinto.
- **Routers:** `backend/routers/srs.py` (20.7K) es la referencia para cómo se expone una fase del agente vía API. Un `analysis.py` router nuevo expondrá los endpoints.
- **Frontend:** hay stores en `frontend/src/lib/stores/` y componentes en `frontend/src/lib/components/`. Se necesita un store `analysis.ts` y un visor que renderice Mermaid.

### Modelos de datos existentes a consumir

La fase de análisis lee el output de la Fase 1:

- `RequirementItem` (backend/models/requirement.py) — requerimientos tipados, con MoSCoW, acceptance_criteria en Gherkin, source tracing
- `RequirementRelation` — relaciones entre requerimientos
- `SrsDocument` — el SRS ensamblado con secciones narrativas
- `ProjectDocument` — documentos fuente cargados en la captura

### Modelos de datos nuevos a crear

Persistir los artefactos del análisis requiere modelos nuevos:

- `AnalysisRun` — ejecución del análisis (estado, etapas, timings)
- `DomainEntity` — entidad del MER (nombre, atributos, relaciones)
- `DomainRelation` — relación entre entidades (cardinalidad, tipo)
- `AggregateRoot` — agregado DDD (entidades que lo componen, límite de consistencia)
- `ProcessDiagram` — diagrama de proceso (tipo, entidades involucradas, estados/transiciones)
- `ArchitectureDecision` — ADR (contexto, decisión, alternativas, rationale)
- `SubProject` — sub-proyecto propuesto (nombre, responsabilidad, stack, contextos agrupados, RNFs aplicables)
- `ProjectContract` — contrato entre sub-proyectos (tipo: REST/gRPC/eventos, spec: OpenAPI/proto/schema, sub-proyectos que lo usan)
- `AnalysisArtifact` — artefacto genérico (tipo, contenido Mermaid, metadatos)

## Decisiones de diseño ya tomadas en la exploración

Estas decisiones fueron discutidas y validadas en la sesión de exploración. Deben respetarse al implementar:

1. **Arquitectura hexagonal como marco de descomposición futuro.** El MER debe identificar agregados DDD, porque cada agregado será un candidato a contexto acotado y luego a hexágono. El dominio puro (centro del hexágono) es lo que los SLMs implementarán; la infraestructura (adaptadores) se andamiará con plantillas.

2. **Simpleza como criterio de descomposición.** Cuando se llegue a la fase de generar contratos, el criterio primario de "es esto una hoja implementable" es: ¿una persona podría implementarlo leyendo solo el contrato? No es tamaño en tokens — es claridad y baja complejidad cognitiva.

3. **Contratos con tests concretos.** Los acceptance criteria en Gherkin (Given/When/Then) que ya existen en los RequirementItem se traducirán directamente a casos de prueba concretos en los contratos. El análisis debe preservar esta trazabilidad.

4. **Mermaid.js como formato de diagramas.** Texto versionable, renderizable en SvelteKit, comprensible por Claude Code cuando un humano abra el proyecto generado.

5. **Trazabilidad de extremo a extremo.** Cada entidad del MER debe trazar a los requerimientos que la mencionan. Cada proceso a los acceptance criteria que lo definen. Cada ADR a los RNFs que lo motivan. Cada sub-proyecto a los contextos acotados que agrupa.

6. **La salida orientada a repos humanos.** Todo lo que se produzca debe ser consumible tanto por el pipeline automático (fases siguientes) como por un humano que abra el proyecto en VSCode con Claude Code. Los ADRs y la propuesta de sub-proyectos serán parte del `CLAUDE.md` de cada repo generado.

7. **Los sub-proyectos son la frontera de partición.** Definir qué sub-proyectos existen y cómo se conectan (contratos inter-proyecto) es lo que permite que la descomposición hexagonal posterior tenga límites claros. Sin esta propuesta, no se sabe dónde empieza y termina cada hexágono.

8. **El LLM grande para análisis, SLM para desarrollo.** Esta fase (análisis y diseño) usa el mismo modelo que la captura de requerimientos (GLM-4.6 vía `backend/agents/llm.py`). Los modelos pequeños (Ollama) se reservan exclusivamente para la fase de desarrollo futuro, donde implementarán componentes hoja individuales.

## Evidence base

La investigación completa de viabilidad está en `docs/definiciones/fase2-investigacion-viabilidad.md`. Incluye:

- MetaGPT (ICLR 2024): 57% de proyectos completos runneables desde un requerimiento
- Qwen2.5-Coder-7B: 88.4% HumanEval pass@1 (SOTA en escala 7B)
- Descomposición permite modelos 14B donde antes se necesitaba 70B
- CoCoS (EMNLP 2025): auto-corrección +35.8% en MBPP para modelos pequeños
- GitHub spec-kit: spec-driven development es movimiento formal
- Piloto InfoFact: Qwen3.5:9B pasó 8/8 tests de contrato

## Ollama: referencia para fase futura (NO usar en esta fase)

El entorno de desarrollo tiene Ollama corriendo en `http://localhost:11434` con el modelo `qwen3.5:9b-8k`. Esto se usará en la **futura fase de desarrollo** para implementar componentes hoja con SLMs. Para invocarlo desde Python, usar la API nativa (`/api/chat`) con `think: false`. Ver `scripts/smoke_slm_pilot.py` como referencia. **No configurar ni invocar Ollama durante la implementación de esta fase de análisis y diseño.**

## Recursos de referencia en el repo

- `docs/definiciones/fase2-investigacion-viabilidad.md` — investigación completa con 16 referencias
- `scripts/smoke_slm_pilot.py` — piloto Fase 0, formato de contrato y ciclo SLM→tests (referencia futura)
- `backend/agents/llm.py` — configuración del LLM que esta fase debe reutilizar
- `backend/agents/subagents/requirements_capture_agent.py` — patrón de subagente por etapas
- `backend/agents/pipelines/` — patrón de pipelines (classification.py es buena referencia)
- `backend/routers/srs.py` — patrón de router para exponer fases del agente
- `backend/models/requirement.py` — modelo de RequirementItem que el análisis consumirá
- `backend/services/srs_assembler.py` — cómo se ensambla el SRS (referencia de ensamblaje de artefactos)

## Primer paso recomendado

Antes de escribir código de pipeline, crear los **modelos de datos SQLAlchemy** para persistir los artefactos del análisis (`DomainEntity`, `DomainRelation`, `AggregateRoot`, `ProcessDiagram`, `ArchitectureDecision`, `SubProject`, `ProjectContract`, `AnalysisArtifact`, `AnalysisRun`). Sin persistencia no hay trazabilidad ni UI. La migración sobre la DB existente debe ser limpia.

Luego, un pipeline mínimo que tome el SRS + RequirementItems de un proyecto y produzca un MER inicial en Mermaid, persistido en DB, con trazabilidad a requerimientos. Ese es el MVP de la fase de análisis.
