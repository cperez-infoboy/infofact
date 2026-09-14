// API client de paquetes de trabajo (molde api/analysis.ts).
// Todas las llamadas viajan con cookies (credentials include ya está en
// apiFetch del client base).
import { apiFetch } from './client';

export type PackageStatus = 'candidate' | 'in_review' | 'locked';

export interface CoherenceGate {
  gate: string;
  status: 'pass' | 'warn' | 'fail';
  blocking: boolean;
  details: string[];
}

export interface PackagesDocument {
  id: number;
  project_id: number;
  version: number;
  status: PackageStatus;
  analysis_id: number;
  analysis_version: number;
  coherence_report: { gates?: CoherenceGate[]; critique_findings?: unknown[] };
  requirement_count: number;
  generated_at: string | null;
  locked_at: string | null;
}

export interface WorkTask {
  id: number;
  code: string;
  title: string;
  description: string;
  req_codes: string[];
  entity_codes: string[];
  contract_names: string[];
  depends_on: string[];
  acceptance: string[];
  sort_order: number;
}

export interface WorkPackageInfo {
  id: number;
  code: string;
  sub_project_code: string;
  sub_project_name: string;
  project_code: string | null;
  mission: string;
  stack: Record<string, string>;
  counts: Record<string, number>;
  tasks?: WorkTask[];
  markdown?: string;
}

export interface PackagesVersionDetail extends PackagesDocument {
  packages: WorkPackageInfo[];
  master_markdown: string;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: 'project' | 'subproject' | 'entity' | 'req' | 'task';
  domain_type?: string;
  project?: string;
  entities?: number;
  reqs?: number;
  tasks?: number;
}

export interface GraphEdge {
  from: string;
  to: string;
  kind: 'belongs' | 'contract' | 'owns' | 'rel' | 'traces' | 'nfr_of' | 'task_of' | 'implements';
  label: string;
  contract_type?: string;
  cardinality?: string;
}

export interface TraceabilityGraph {
  focus: string | null;
  levels: number | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export async function listPackagesVersions(
  projectId: number
): Promise<PackagesDocument[]> {
  return apiFetch<PackagesDocument[]>(
    `/api/projects/${projectId}/packages/versions`
  );
}

export async function getPackagesVersion(
  projectId: number,
  version: number
): Promise<PackagesVersionDetail> {
  return apiFetch<PackagesVersionDetail>(
    `/api/projects/${projectId}/packages/versions/${version}`
  );
}

export async function getPackage(
  projectId: number,
  version: number,
  wpCode: string
): Promise<WorkPackageInfo> {
  return apiFetch<WorkPackageInfo>(
    `/api/projects/${projectId}/packages/versions/${version}/${wpCode}`
  );
}

export async function patchPackagesStatus(
  projectId: number,
  version: number,
  pkgStatus: PackageStatus
): Promise<PackagesDocument> {
  return apiFetch<PackagesDocument>(
    `/api/projects/${projectId}/packages/versions/${version}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: pkgStatus })
    }
  );
}

export function packageExportUrl(
  projectId: number,
  version: number,
  wpCode: string
): string {
  return `/api/projects/${projectId}/packages/versions/${version}/${wpCode}/export.md`;
}

export function masterExportUrl(
  projectId: number,
  version: number
): string {
  return `/api/projects/${projectId}/packages/versions/${version}/master/export.md`;
}

/** Nivel de vista del grafo: overview (descomposición) o full (todo). */
export type TraceabilityView = 'overview' | 'full';

export async function getTraceabilityGraph(
  projectId: number,
  version: number,
  focus = '',
  levels = 1,
  view: TraceabilityView = 'full'
): Promise<TraceabilityGraph> {
  const qs = new URLSearchParams();
  if (focus) qs.set('focus', focus);
  qs.set('levels', String(levels));
  qs.set('view', view);
  return apiFetch<TraceabilityGraph>(
    `/api/projects/${projectId}/analysis/versions/${version}/traceability/graph?${qs}`
  );
}
