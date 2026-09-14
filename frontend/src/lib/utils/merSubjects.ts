// Utilidades deterministas (0 LLM) para derivar vistas de diagramas ER desde
// los datos persistidos del análisis: un erDiagram compacto del modelo
// completo (entidades sin atributos, coloreadas por área) y un erDiagram por
// área temática (sub-proyecto o, en su ausencia, contexto acotado).
// Replica las convenciones del render backend (mer_pipeline._render_mermaid):
// ids MAYÚSCULAS_CON_GUION_BAJO, cardinalidades mermaid y classDef por grupo.
import type {
  DomainEntity,
  DomainRelationship,
  SubProject
} from '$lib/api/analysis';

/** Un área temática: sub-proyecto o grupo de contextos normalizados. */
export interface MerAreaGroup {
  /** Código de sub-proyecto, clave normalizada de contexto, o '__sin_area__'. */
  key: string;
  label: string;
  entityCodes: string[];
}

/** Cardinalidad persistida -> notación de arista de mermaid erDiagram. */
const CARDINALITY: Record<string, string> = {
  one_to_one: '||--||',
  '1:1': '||--||',
  one_to_many: '||--o{',
  '1:n': '||--o{',
  '1:m': '||--o{',
  many_to_many: '}o--o{',
  'n:m': '}o--o{'
};

/** Paleta (fill, stroke) por área, legible sobre tema oscuro. */
const AREA_PALETTE: [string, string][] = [
  ['#1e3a5f', '#3b82f6'],
  ['#1a4d3a', '#22c55e'],
  ['#4a3a1a', '#d97706'],
  ['#3a1a4a', '#a855f7'],
  ['#4a1a2a', '#ec4899'],
  ['#1a4a4a', '#14b8a6'],
  ['#4a4a1a', '#eab308'],
  ['#2a2a5f', '#6366f1'],
  ['#4a2a1a', '#f97316'],
  ['#1a3a3a', '#06b6d4'],
  ['#3a3a1a', '#a3a30f'],
  ['#4a1a4a', '#d946ef']
];

/** Clave de agrupación de un contexto: sin acentos, minúsculas, alfanumérico. */
export function normalizeContextKey(bc: string | null | undefined): string {
  if (!bc) return '';
  return bc
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
}

/** Identificador de entidad para mermaid (misma regla que el backend). */
function merId(name: string): string {
  return name.replace(/ /g, '_').toUpperCase();
}

function cardinalityOf(card: string): string {
  return CARDINALITY[card.trim().toLowerCase()] ?? '||--o{';
}

/** Classtable estable: los grupos ordenados por clave reciben su color. */
function areaClassOf(groups: MerAreaGroup[], key: string): number {
  const sorted = [...groups].sort((a, b) => a.key.localeCompare(b.key));
  return sorted.findIndex((g) => g.key === key);
}

function classDef(idx: number, fill: string, stroke: string): string {
  return `    classDef area${idx} fill:${fill},stroke:${stroke}`;
}

/**
 * Construye los grupos de áreas temáticas del modelo. Con sub-proyectos,
 * un grupo por sub-proyecto (las entidades no cubiertas caen en
 * «Sin sub-proyecto»); sin ellos, agrupa por contexto acotado normalizado.
 */
export function buildAreaGroups(
  entities: DomainEntity[],
  subProjects: SubProject[] | null
): MerAreaGroup[] {
  const codes = new Set(entities.map((e) => e.code));
  if (subProjects && subProjects.length > 0) {
    const groups: MerAreaGroup[] = [];
    const assigned = new Set<string>();
    for (const sp of [...subProjects].sort((a, b) =>
      a.code.localeCompare(b.code)
    )) {
      const members = sp.entity_codes.filter((c) => codes.has(c));
      if (members.length === 0) continue;
      members.forEach((c) => assigned.add(c));
      groups.push({ key: sp.code, label: sp.name, entityCodes: members });
    }
    const orphans = entities
      .filter((e) => !assigned.has(e.code))
      .map((e) => e.code);
    if (orphans.length > 0) {
      groups.push({
        key: '__sin_area__',
        label: 'Sin sub-proyecto',
        entityCodes: orphans
      });
    }
    return groups;
  }

  // Fallback: agrupar por contexto acotado normalizado.
  const byKey = new Map<string, MerAreaGroup>();
  for (const e of entities) {
    const bc = (e.bounded_context ?? '').trim();
    const key = normalizeContextKey(bc) || '__sin_area__';
    let g = byKey.get(key);
    if (!g) {
      g = {
        key,
        label: bc || 'Sin contexto',
        entityCodes: []
      };
      byKey.set(key, g);
    }
    g.entityCodes.push(e.code);
  }
  return [...byKey.values()];
}

/**
 * erDiagram compacto de TODO el modelo: entidades sin atributos (solo el
 * nombre), todas las relaciones y colores por área. Pensado como mapa
 * mental del dominio cuando el diagrama completo no es navegable.
 */
export function buildCompactOverview(
  entities: DomainEntity[],
  relationships: DomainRelationship[],
  groups: MerAreaGroup[]
): string {
  if (entities.length === 0) return 'erDiagram';

  const byCode = new Map(entities.map((e) => [e.code, e]));
  const classOfEntity = new Map<string, number>();
  groups.forEach((g) => {
    const idx = areaClassOf(groups, g.key);
    for (const c of g.entityCodes) {
      if (!classOfEntity.has(c)) classOfEntity.set(c, idx);
    }
  });

  const lines: string[] = ['erDiagram'];
  for (const e of entities) lines.push(`    ${merId(e.name)}`);

  const seen = new Set<string>();
  for (const rel of relationships) {
    const from = byCode.get(rel.from_entity_code);
    const to = byCode.get(rel.to_entity_code);
    if (!from || !to || from.code === to.code) continue;
    const key = `${from.code}|${to.code}|${rel.label ?? ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const card = cardinalityOf(rel.cardinality);
    const label = rel.label || 'relates_to';
    lines.push(
      `    ${merId(from.name)} ${card} ${merId(to.name)} : "${label}"`
    );
  }

  // Colores por área + highlight de aggregate roots.
  const usedIdx = new Set(classOfEntity.values());
  for (const idx of [...usedIdx].sort((a, b) => a - b)) {
    const [fill, stroke] = AREA_PALETTE[idx % AREA_PALETTE.length];
    lines.push(classDef(idx, fill, stroke));
  }
  lines.push(
    '    classDef agg_root stroke:#facc15,stroke-width:3px'
  );
  for (const e of entities) {
    const idx = classOfEntity.get(e.code);
    if (idx !== undefined) {
      lines.push(`    class ${merId(e.name)} area${idx}`);
    }
    if (e.aggregate_root) {
      lines.push(`    class ${merId(e.name)} agg_root`);
    }
  }
  return lines.join('\n');
}

export interface AreaDiagramOptions {
  /** Incluir relaciones que salen/entran del área (la contraparte queda
   *  como caja externa simple, gris punteada). Default: false. */
  includeExternal?: boolean;
}

/**
 * erDiagram de UN área temática: sus entidades con TODOS sus atributos,
 * las relaciones internas y (opcional) las aristas cruzadas con la entidad
 * remota como referencia simple. Práctica de «subject area» para modelos
 * que no caben en un solo diagrama legible.
 */
export function buildAreaDiagram(
  group: MerAreaGroup,
  entities: DomainEntity[],
  relationships: DomainRelationship[],
  opts: AreaDiagramOptions = {}
): string {
  const byCode = new Map(entities.map((e) => [e.code, e]));
  const inArea = new Set(group.entityCodes);

  const lines: string[] = [`%% Área: ${group.label}`, 'erDiagram'];

  const areaRoots: string[] = [];
  for (const code of group.entityCodes) {
    const ent = byCode.get(code);
    if (!ent) continue;
    if (ent.aggregate_root) areaRoots.push(merId(ent.name));
    lines.push(`    ${merId(ent.name)} {`);
    for (const a of ent.attributes) {
      const t = (a.type || 'string').trim();
      const n = (a.name || 'field').trim();
      lines.push(a.is_key ? `        ${t} ${n} PK` : `        ${t} ${n}`);
    }
    lines.push('    }');
  }

  const externalNames = new Set<string>();
  const seen = new Set<string>();
  for (const rel of relationships) {
    const fIn = inArea.has(rel.from_entity_code);
    const tIn = inArea.has(rel.to_entity_code);
    if (!fIn && !tIn) continue;
    if (fIn && tIn) {
      const from = byCode.get(rel.from_entity_code);
      const to = byCode.get(rel.to_entity_code);
      if (!from || !to) continue;
      const key = `${from.code}|${to.code}|${rel.label ?? ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const label = rel.label || 'relates_to';
      lines.push(
        `    ${merId(from.name)} ${cardinalityOf(rel.cardinality)} ${merId(
          to.name
        )} : "${label}"`
      );
    } else if (opts.includeExternal) {
      const innerCode = fIn ? rel.from_entity_code : rel.to_entity_code;
      const remoteCode = fIn ? rel.to_entity_code : rel.from_entity_code;
      const inner = byCode.get(innerCode);
      const remote = byCode.get(remoteCode);
      if (!inner || !remote) continue;
      const key = `${inner.code}|${remote.code}|${rel.label ?? ''}`;
      if (seen.has(key)) continue;
      seen.add(key);
      externalNames.add(remote.name);
      const label = rel.label || 'relates_to';
      const fromId = fIn ? merId(inner.name) : merId(remote.name);
      const toId = fIn ? merId(remote.name) : merId(inner.name);
      lines.push(`    ${fromId} ${cardinalityOf(rel.cardinality)} ${toId} : "${label}"`);
    }
  }

  // Entidades externas: cajas simples (sin atributos), grises punteadas.
  for (const name of [...externalNames].sort()) {
    lines.push(`    ${merId(name)}`);
  }

  lines.push(
    '    classDef agg_root stroke:#facc15,stroke-width:3px'
  );
  if (externalNames.size > 0) {
    lines.push(
      '    classDef extern fill:#2a2a2a,stroke:#666,stroke-dasharray:5 5'
    );
  }
  for (const id of areaRoots) lines.push(`    class ${id} agg_root`);
  for (const name of [...externalNames].sort()) {
    lines.push(`    class ${merId(name)} extern`);
  }
  return lines.join('\n');
}
