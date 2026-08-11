// Cliente del análisis versiónado (Phase 2: Analysis & Design).
// Tipos espejan los response models de backend/routers/analysis.py.
// `credentials: 'include'` via apiFetch (cookie JWT HttpOnly).
import { apiFetch } from './client';

// --- Tipos (espejo de los response models de analysis.py) ------------------

export type AnalysisStatus = 'candidate' | 'in_review' | 'locked';

/** Diagrama de procesos (state_machine o sequence por entidad relevante). */
export interface ProcessDiagram {
  name: string;
  // "state_machine" | "sequence" | otro tipo soportado por el backend.
  type: string;
  mermaid: string;
  entity_name?: string;
  description?: string;
  traced_req_codes: string[];
}

/** Análisis de NFR: decisiones, stack, consistencia y patrones. */
export interface NfrAnalysis {
  decisions: Record<string, unknown>[];
  stack: Record<string, unknown>[];
  data_consistency: string;
  patterns: string;
}

/** Versión de análisis (diagramas llegan solo en el detalle de una versión). */
export interface AnalysisVersion {
  id: number;
  project_id: number;
  version: number;
  status: string;
  srs_version: number | null;
  mer_diagram: string | null;
  mer_diagram_description?: string | null;
  process_diagrams: ProcessDiagram[] | null;
  nfr_analysis: NfrAnalysis | null;
  component_diagram: string | null;
  component_diagram_description?: string | null;
  system_architecture_diagram?: string | null;
  system_architecture_description?: string | null;
  infrastructure_diagram?: string | null;
  infrastructure_description?: string | null;
  traceability: Record<string, unknown> | null;
  requirement_codes: string[];
  requirement_count: number;
  generated_at: string | null;
  locked_at: string | null;
}

/** Respuesta del commit: crea una versión CANDIDATE. */
export interface AnalysisCommit {
  project_id: number;
  version: number;
  status: string;
  requirement_count: number;
  mer_stats: { entities: number; relationships: number };
}

/** Entidad de dominio (MER) con atributos y trazabilidad. */
export interface DomainAttribute {
  name: string;
  type: string;
  required: boolean;
  is_key: boolean;
  description: string;
}

export interface DomainEntity {
  id: number;
  code: string;
  name: string;
  description: string;
  attributes: DomainAttribute[];
  aggregate_root: boolean;
  bounded_context: string | null;
  traced_req_codes: string[];
}

/** Relación entre entidades del MER. */
export interface DomainRelationship {
  id: number;
  from_entity_code: string;
  to_entity_code: string;
  cardinality: string;
  label: string | null;
  description: string | null;
  traced_req_codes: string[];
}

/** ADR (Architecture Decision Record). */
export interface Adr {
  id: number;
  code: string;
  title: string;
  status: string;
  context: string;
  decision: string;
  alternatives: Record<string, unknown>[];
  rationale: string;
  nfr_codes: string[];
}

/** Sub-proyecto (decomposición arquitectónica). */
export interface SubProject {
  id: number;
  code: string;
  name: string;
  responsibility: string;
  stack: Record<string, string>;
  bounded_contexts: string[];
  nfr_codes: string[];
  entity_codes: string[];
  project_code: string | null;
}

/** Proyecto (área funcional mayor = subdominio DDD). */
export interface AnalysisProject {
  id: number;
  code: string;
  name: string;
  description: string;
  domain_type: 'core' | 'supporting' | 'generic';
  bounded_contexts: string[];
  entity_codes: string[];
  traced_req_codes: string[];
}

/** Contrato entre dos sub-proyectos. */
export interface SubProjectContract {
  id: number;
  from_subproject_code: string;
  to_subproject_code: string;
  contract_type: string;
  name: string;
  spec: string;
  description: string | null;
}

// --- Bodies -----------------------------------------------------------------

export interface AnalysisStatusBody {
  status: 'in_review' | 'locked';
}

export type AnalysisPatchBody = AnalysisStatusBody;

// --- Funciones --------------------------------------------------------------

/** Genera un análisis CANDIDATE (orquesta MER + procesos + NFR + ADRs). */
export function commitAnalysis(projectId: number): Promise<AnalysisCommit> {
  return apiFetch<AnalysisCommit>(
    `/api/projects/${projectId}/analysis/commit`,
    { method: 'POST' }
  );
}

/** Lista las versiones del proyecto (sin diagramas). */
export function listAnalysisVersions(projectId: number): Promise<AnalysisVersion[]> {
  return apiFetch<AnalysisVersion[]>(
    `/api/projects/${projectId}/analysis/versions`
  );
}

/** Detalle de una versión (con diagramas). */
export function getAnalysisVersion(
  projectId: number,
  version: number
): Promise<AnalysisVersion> {
  return apiFetch<AnalysisVersion>(
    `/api/projects/${projectId}/analysis/versions/${version}`
  );
}

/** Mutación controlada de una versión (estado). */
export function patchAnalysisVersion(
  projectId: number,
  version: number,
  body: AnalysisPatchBody
): Promise<AnalysisVersion> {
  return apiFetch<AnalysisVersion>(
    `/api/projects/${projectId}/analysis/versions/${version}`,
    { method: 'PATCH', body: JSON.stringify(body) }
  );
}

/** Entidades de dominio (MER) de una versión. */
export function getAnalysisEntities(
  projectId: number,
  version: number
): Promise<DomainEntity[]> {
  return apiFetch<DomainEntity[]>(
    `/api/projects/${projectId}/analysis/versions/${version}/entities`
  );
}

/** Relaciones del MER de una versión. */
export function getAnalysisRelationships(
  projectId: number,
  version: number
): Promise<DomainRelationship[]> {
  return apiFetch<DomainRelationship[]>(
    `/api/projects/${projectId}/analysis/versions/${version}/relationships`
  );
}

/** ADRs de una versión. */
export function getAnalysisAdrs(
  projectId: number,
  version: number
): Promise<Adr[]> {
  return apiFetch<Adr[]>(
    `/api/projects/${projectId}/analysis/versions/${version}/adrs`
  );
}

/** Sub-proyectos + contratos + proyectos de una versión. */
export function getAnalysisSubProjects(
  projectId: number,
  version: number
): Promise<{ projects: AnalysisProject[]; subprojects: SubProject[]; contracts: SubProjectContract[] }> {
  return apiFetch<{
    projects: AnalysisProject[];
    subprojects: SubProject[];
    contracts: SubProjectContract[];
  }>(`/api/projects/${projectId}/analysis/versions/${version}/subprojects`);
}


/** Actualiza el codigo fuente de los diagramas (solo CANDIDATE). */
export async function patchAnalysisDiagrams(
  projectId: number,
  version: number,
  updates: Partial<Pick<AnalysisVersion, 'mer_diagram' | 'mer_diagram_description' | 'process_diagrams' | 'component_diagram' | 'component_diagram_description' | 'system_architecture_diagram' | 'system_architecture_description' | 'infrastructure_diagram' | 'infrastructure_description'>>
): Promise<AnalysisVersion> {
  return apiFetch<AnalysisVersion>(
    `/api/projects/${projectId}/analysis/versions/${version}/diagrams`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    }
  );
}