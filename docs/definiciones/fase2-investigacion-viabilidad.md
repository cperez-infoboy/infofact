# Fase 2 — Investigación de Viabilidad: Generación de Software con Modelos Pequeños

> **Fecha:** 2026-08-06
> **Autor:** Análisis de viabilidad para InfoFact
> **Estado:** Investigación completada. Piloto validado.

---

## Resumen ejecutivo

Se investigó si es viable que InfoFact, tras capturar requerimientos (Fase 1), pueda **analizar, diseñar y generar código** descomponiendo el software en componentes pequeños implementables por modelos de lenguaje locales (SLMs), ensamblando el resultado en proyectos convencionales trabajables por desarrolladores humanos.

**Conclusión:** cada pieza del esquema está probada individualmente en papers académicos y proyectos reales. El piloto realizado con Qwen3.5:9B validó el eslabón fundamental. El riesgo está en la integración a escala, no en los mecanismos individuales.

---

## 1. El concepto propuesto

InfoFact extendería su pipeline más allá de la captura de requerimientos:

```
SRS (Fase 1, existe)
    ↓
Análisis del sistema (MER, procesos, ADRs)
    ↓
Particionamiento en sub-proyectos (frontend, backend, API, etc.)
    ↓
Descomposición hexagonal por sub-proyecto
    ↓
Generación de contratos por componente hoja
    ↓
Implementación con SLMs (modelos locales 7B-14B)
    ↓
Ensamblaje + pruebas de integración jerárquicas
    ↓
Workspace de repos convencionales con CLAUDE.md
```

El desarrollador humano entra al final: abre cualquier sub-proyecto en VSCode con Claude Code y encuentra código funcionando, tests, y contexto completo para refinar.

### Principios de diseño

- **La simpleza es el criterio PRIMARIO de descomposición**, no el presupuesto de tokens. Un componente es hoja cuando una persona podría implementarlo leyendo solo el contrato, sin hacer preguntas adicionales.
- **Arquitectura hexagonal** como marco: el dominio puro (implementable por SLM) va al centro; la infraestructura (andamiada con plantillas) va a los bordes.
- **Contratos con tests concretos** como mecanismo de verificación automática.
- **Cadena de escalado**: SLM 7B → SLM 14B → LLM grande → revisión humana.

---

## 2. Evidencia académica

### 2.1. La descomposición permite modelos 5x más pequeños

**Paper:** [Articulating Assumptions in AI-Generated Scientific Code](https://arxiv.org/html/2607.05762v1) (2025)

> Separar la selección de helpers de la generación de código "mitiga sustancialmente la inestabilidad, haciendo posible la generación de código confiable con modelos de 14B parámetros, donde el enfoque anterior requería modelos de ~70B parámetros."

**Modelos probados:** Qwen 7B, 14B, 32B, 72B; Llama 8B, 70B.

**Limitación encontrada:** los modelos pequeños tienen mayor variación run-to-run. La detección de ambigüedad depende fuertemente del tamaño (72B detecta 28/30 casos; 14B solo 13/30). Esto refuerza la necesidad de un paso de crítica de contratos antes de enviarlos al SLM.

**Herramienta:** el paper publica un workbench ("LLM_For_scientific_Codes") con arquitectura multi-agente: selector de helpers, generador de código, ejecutor/corrector, trazador, crítico, y oráculo.

### 2.2. Modelos pequeños ya igualan a los grandes en código

**Paper:** [Qwen2.5-Coder Technical Report](https://arxiv.org/html/2409.12186v3)

| Benchmark | Qwen2.5-Coder-7B-Instruct | Referencia |
|---|---|---|
| HumanEval pass@1 | **88.4%** | Supera a Codestral 22B (81.1%) |
| MBPP pass@1 | **83.5%** | SOTA en escala 7B |

Un modelo de 7B ejecutable localmente iguala o supera a modelos 3x más grandes en generación de código.

**Nota de la comunidad (r/LocalLLaMA):** algunos usuarios reportan que en tareas reales el 7B puede alucinar más que DeepSeek-Coder-V2-Lite, sugiriendo que los benchmarks pueden ser optimistas. El Qwen3-Coder 3B logra solo 45.12% en HumanEval — el sweet spot práctico está en 7B.

### 2.3. Auto-corrección para modelos pequeños

**Paper:** [Self-Correcting Code Generation Using Small Language Models](https://arxiv.org/abs/2505.23060v3) (EMNLP 2025 Findings, CoCoS method)

- **+35.8% en MBPP** y **+27.7% en HumanEval** con auto-corrección entrenada (RL).
- Funciona con modelos de escala 1B.
- Los modelos pequeños **no se corrigen naturalmente** — necesitan entrenamiento específico o un bucle de feedback estructurado con información de qué test falló.

**Implicancia para InfoFact:** el bucle de self-debug (SLM intenta → tests fallan → feedback del error → reintento) está respaldado por evidencia. El paper muestra que este bucle mejora significativamente los resultados incluso en modelos muy pequeños.

### 2.4. Descomposición jerárquica como técnica fundacional

**Paper:** [ADaPT: As-Needed Decomposition and Planning with LLMs](https://public.intellimedia.ncsu.edu/pubmgr/pubdb/pdfs/_collaborator/engageAI/Prasad-NAACL-2024.pdf) (NAACL 2024, 228 citas)

Establece la descomposición jerárquica "según se necesita": el planificador descompone una tarea compleja en subtareas, y descompone recursivamente solo las que no puede resolver directamente. Es la base teórica del algoritmo de descomposición propuesto para InfoFact.

---

## 3. Proyectos que implementan conceptos similares

### 3.1. MetaGPT — Generación de proyectos completos

**Paper:** [MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework](https://arxiv.org/html/2308.00352v7) (ICLR 2024, 3598+ citas)

Toma un requerimiento de una línea como entrada y produce user stories, análisis competitivo, requerimientos, estructuras de datos, APIs, documentación y código.

| Métrica | Resultado |
|---|---|
| Pass@1 (benchmarks de SE colaborativo) | **85.9% y 87.7%** |
| Proyectos completos ejecutables | **57.14%** de éxito |
| Con ≤3 correcciones de bugs | **51.43%** |
| SWE-Bench Lite (MetaGPT X) | **46.67%** |

Usa **descomposición por roles** (PM, Arquitecto, Engineer, QA) — similar a las fases de InfoFact. La diferencia: InfoFact usaría descomposición hexagonal por contratos, que es más estructurada y produce componentes más predecibles.

**Repo:** [github.com/FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT)

### 3.2. ChatDev — Empresa virtual de software

**Paper:** [ChatDev: Communicative Agents for Software Development](https://www.researchgate.net/publication/384214451_ChatDev_Communicative_Agents_for_Software_Development)

Simula una empresa virtual donde múltiples agentes con roles distintos (CEO, CTO, programador, tester) colaboran vía chat para producir software completo. Unifica diseño, código, tests y documentación.

### 3.3. Devin — Ingeniero de software autónomo

**Empresa:** Cognition Labs

Arquitectura con Planner LLM para descomposición + sandbox con shell, editor y navegador. Modelo ~32B con fine-tuning específico y RL.

| Versión | SWE-Bench |
|---|---|
| Original (Mar 2024) | 13.86% |
| Update (mediados 2024) | ~30.08% |
| Devin 3 (2025) | ~90% Verified |

**Fuentes:** [cognition.com/blog/swe-bench-technical-report](https://cognition.com/blog/swe-bench-technical-report), [devin.ai](https://devin.ai/)

### 3.4. Smol Developer y GPT-Engineer

**[Smol Developer](https://github.com/smol-ai/developer):** genera un codebase completo desde un spec usando un archivo intermedio `shared_dependencies.md` para coordinar consistencia entre archivos generados. Es el patrón de "contrato compartido" que InfoFact adoptaría con los contratos inter-proyecto.

**[GPT-Engineer](https://sourceforge.net/software/compare/gpt-engineer-vs-smol-developer/):** scaffolding de proyectos completos desde un prompt. Puede generar y ejecutar código basándose en especificaciones.

---

## 4. Spec-Driven Development (movimiento formal)

La idea de usar especificaciones como contratos ejecutables para guiar la generación de código ya es un movimiento formal en la industria:

- **[GitHub spec-kit](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/):** toolkit open-source donde las specs son contratos que los agentes AI usan para generar, testear y validar código.
- **Amazon Kiro:** herramienta SDD de AWS.
- **[Paper arXiv: Spec-Driven Development: From Code to Contract](https://arxiv.org/html/2602.00180v1):** postula que en SDD la spec es la fuente de verdad y el código es un artefacto derivado.
- **[Martin Fowler explora SDD](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html):** compara Kiro y spec-kit, define SDD como "documentación primero."
- **[Contrapunto: C-DAD](https://medium.com/software-architecture-in-the-age-of-ai/why-spec-driven-development-has-reached-its-limit-6e9bfed9ee13):** argumenta que las specs estáticas tienen límites y propone "contratos vivos" (Contract-Driven Adaptive Development).

**Implicancia:** InfoFact no inventa SDD desde cero — se monta sobre un movimiento validado, extendiéndolo con descomposición jerárquica para modelos pequeños.

---

## 5. Modelos pequeños vs. grandes: evidencia económica

| Fuente | Hallazgo |
|---|---|
| [Amazon Science](https://www.amazon.science/blog/how-task-decomposition-and-smaller-llms-can-make-ai-more-affordable) | Múltiples LLMs fine-tuned para subtareas = **70-90% de ahorro** manteniendo calidad |
| [NVIDIA Developer](https://developer.nvidia.com/blog/how-small-language-models-are-key-to-scalable-agentic-ai/) | SLMs reducen costo de inferencia **10-30x** para tareas agentic especializadas |
| [arXiv: SLMs are the Future of Agentic AI](https://arxiv.org/html/2506.02153v1) | Algoritmo de 6 pasos LLM→SLM: clustering de tareas, fine-tuning PEFT, router especializado |
| [Centific](https://www.centific.com/blog/why-small-language-models-are-gaining-ground-as-agentic-ai-goes-mainstream) | SLMs manejan tareas rutinarias/estructuradas; LLMs reservados para razonamiento complejo |

---

## 6. Piloto realizado (Fase 0)

**Fecha:** 2026-08-06
**Script:** `scripts/smoke_slm_pilot.py`
**Modelo:** Qwen3.5:9B (9B parámetros, 8K contexto) via Ollama

### Resultado: ✅ 8/8 tests pasaron (100%)

El SLM implementó correctamente un contrato de calculadora de límite de crédito:
- 5 tests del contrato: todos pasaron
- 3 tests adicionales (no en el contrato, verificación de generalización): todos pasaron

### Hallazgos técnicos del piloto

1. **Endpoint nativo de Ollama requerido.** El endpoint OpenAI-compatible (`/v1/chat/completions`) devuelve `content` vacío para modelos razonadores. Usar `/api/chat` con `think: false`.
2. **2 ejemplos de test en el contrato bastaron** para que el SLM generalizara a 6 casos adicionales.
3. **Hallazgo crítico:** la primera corrida "falló" porque los valores esperados de los tests estaban mal calculados (error humano, no del SLM). Esto valida la necesidad de un paso de crítica de contratos antes de enviarlos al SLM.

---

## 7. Lo que InfoFact aporta de nuevo

Ningún proyecto existente combina todos estos elementos:

| Elemento | ¿Ya existe? | Aporte de InfoFact |
|---|---|---|
| Descomposición de tareas para SLMs | ✅ Papers académicos | Arquitectura hexagonal como marco de descomposición |
| Contratos como specs ejecutables | ✅ spec-kit, SDD | Contratos con tests concretos como verificación automática |
| Modelos pequeños generando código | ✅ Qwen2.5-Coder 88% | Cadena de escalado + criterio de simpleza medible |
| Generación de proyectos completos | ✅ MetaGPT 57% | Output como repos convencionales con CLAUDE.md |
| Auto-corrección de SLMs | ✅ CoCoS +27% | Bucle self-debug con feedback de tests específicos |
| Pipeline SRS → análisis → código | Parcial (MetaGPT) | Integración con captura de requerimientos real |

**La novedad no es ningún componente individual — es la integración completa:** un sistema que va desde requerimientos capturados (no un one-line prompt) hasta repos trabajables por humanos, con descomposición hexagonal y modelos pequeños guiados por contratos testeables.

---

## 8. Riesgos identificados

1. **Calidad de la descomposición del LLM arquitecto.** MetaGPT logra 57% de proyectos runneables — 43% no funcionan end-to-end. La calidad de la decisión arquitectónica de alto nivel determina todo lo downstream.
2. **Complejidad de integración O(N²).** Con N componentes hay potencialmente millones de interacciones. Los componentes pueden ser correctos individualmente pero tener comportamientos emergentes (race conditions, fallos en cascada).
3. **Variación de modelos pequeños.** Mayor inconsistencia run-to-run que modelos grandes. Mitigación: `temperature: 0.1` + múltiples intentos + crítica.
4. **Rendimiento invisible para el SLM.** Un componente funcionalmente correcto puede tener problemas de performance (N+1 queries, etc.) que el SLM no detecta.
5. **Especificación como cuello de botella.** La calidad del árbol completo está acotada por la calidad del SRS. Si hay gaps en requerimientos, hay gaps en todo.

---

## 9. Referencias

### Papers académicos

1. [Articulating Assumptions in AI-Generated Scientific Code](https://arxiv.org/html/2607.05762v1) — Descomposición permite 14B donde se necesitaba 70B
2. [Qwen2.5-Coder Technical Report](https://arxiv.org/html/2409.12186v3) — SOTA 7B en código (88.4% HumanEval)
3. [Self-Correcting Code Generation Using Small Language Models](https://arxiv.org/abs/2505.23060v3) — CoCoS, +35.8% MBPP con auto-corrección
4. [MetaGPT: Meta Programming for Multi-Agent Collaborative Framework](https://arxiv.org/html/2308.00352v7) — 57% éxito end-to-end, ICLR 2024
5. [ADaPT: As-Needed Decomposition and Planning with LLMs](https://public.intellimedia.ncsu.edu/pubmgr/pubdb/pdfs/_collaborator/engageAI/Prasad-NAACL-2024.pdf) — Descomposición jerárquica, NAACL 2024
6. [Small Language Models are the Future of Agentic AI](https://arxiv.org/html/2506.02153v1) — Algoritmo LLM→SLM de 6 pasos
7. [Spec-Driven Development: From Code to Contract](https://arxiv.org/html/2602.00180v1) — SDD como inversión de workflow
8. [CMU-CS-25-132: LLM-Based Approach to Supporting SE](http://ra.adm.cs.cmu.edu/anon/2025/CMU-CS-25-132.pdf) — Dataset de patrones de descomposición

### Proyectos y herramientas

9. [MetaGPT](https://github.com/FoundationAgents/MetaGPT) — Framework multi-agente, 3598+ citas
10. [Smol Developer](https://github.com/smol-ai/developer) — Generación desde spec con shared_dependencies.md
11. [GitHub spec-kit](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/) — SDD toolkit open-source
12. [Martin Fowler: Exploring SDD Tools](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)
13. [Devin](https://devin.ai/) / [Cognition SWE-bench report](https://cognition.com/blog/swe-bench-technical-report)

### Evidencia económica

14. [Amazon Science: Task Decomposition and Smaller LLMs](https://www.amazon.science/blog/how-task-decomposition-and-smaller-llms-can-make-ai-more-affordable)
15. [NVIDIA: SLMs for Scalable Agentic AI](https://developer.nvidia.com/blog/how-small-language-models-are-key-to-scalable-agentic-ai/)

### Piloto

16. `scripts/smoke_slm_pilot.py` — Piloto Fase 0 con Qwen3.5:9B, 8/8 tests
