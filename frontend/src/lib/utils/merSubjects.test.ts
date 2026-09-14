import { describe, expect, it } from 'vitest';
import {
  buildAreaDiagram,
  buildAreaGroups,
  buildCompactOverview,
  normalizeContextKey
} from './merSubjects';
import type {
  DomainEntity,
  DomainRelationship,
  SubProject
} from '$lib/api/analysis';

function ent(
  code: string,
  name: string,
  opts: Partial<DomainEntity> = {}
): DomainEntity {
  return {
    id: 0,
    code,
    name,
    description: `desc ${name}`,
    attributes: [
      { name: 'id', type: 'uuid', required: true, is_key: true, description: '' },
      { name: 'nombre', type: 'string', required: true, is_key: false, description: '' }
    ],
    aggregate_root: false,
    bounded_context: null,
    traced_req_codes: [],
    ...opts
  };
}

function rel(
  from: string,
  to: string,
  label = 'has',
  card = 'ONE_TO_MANY'
): DomainRelationship {
  return {
    id: 0,
    from_entity_code: from,
    to_entity_code: to,
    cardinality: card,
    label,
    description: null,
    traced_req_codes: []
  };
}

const ENTITIES = [
  ent('ENT-001', 'Orden', { aggregate_root: true, bounded_context: 'Ventas' }),
  ent('ENT-002', 'Cliente', { bounded_context: 'Ventas' }),
  ent('ENT-003', 'Camion', { bounded_context: 'Logística' }),
  ent('ENT-004', 'Huérfana', { bounded_context: null })
];

const RELATIONSHIPS = [
  rel('ENT-001', 'ENT-002', 'belongs_to', 'MANY_TO_ONE'),
  rel('ENT-003', 'ENT-001', 'asigna', 'ONE_TO_MANY'),
  rel('ENT-001', 'ENT-001', 'self', 'ONE_TO_MANY')
];

const SUBPROJECTS: SubProject[] = [
  {
    id: 1,
    code: 'SUB-001',
    name: 'Comercial',
    responsibility: 'ventas',
    stack: {},
    bounded_contexts: ['Ventas'],
    nfr_codes: [],
    entity_codes: ['ENT-001', 'ENT-002'],
    project_code: null
  },
  {
    id: 2,
    code: 'SUB-002',
    name: 'Flota',
    responsibility: 'logística',
    stack: {},
    bounded_contexts: ['Logística'],
    nfr_codes: [],
    entity_codes: ['ENT-003', 'ENT-999'],
    project_code: null
  }
];

describe('normalizeContextKey', () => {
  it('colapsa variantes ortográficas', () => {
    expect(normalizeContextKey('Gestión de Terreno')).toBe(
      normalizeContextKey('Gestion de Terreno')
    );
    expect(normalizeContextKey('VentaEnCampo')).toBe(
      normalizeContextKey('Venta en Campo')
    );
  });

  it('vacío y null devuelven cadena vacía', () => {
    expect(normalizeContextKey('')).toBe('');
    expect(normalizeContextKey(null)).toBe('');
    expect(normalizeContextKey(undefined)).toBe('');
  });
});

describe('buildAreaGroups', () => {
  it('agrupa por sub-proyecto y junta huérfanas', () => {
    const groups = buildAreaGroups(ENTITIES, SUBPROJECTS);
    expect(groups.map((g) => g.key)).toEqual([
      'SUB-001',
      'SUB-002',
      '__sin_area__'
    ]);
    expect(groups[0].entityCodes).toEqual(['ENT-001', 'ENT-002']);
    // ENT-999 no existe en las entidades -> filtrada; ENT-004 cae en huérfanas.
    expect(groups[1].entityCodes).toEqual(['ENT-003']);
    expect(groups[2].entityCodes).toEqual(['ENT-004']);
  });

  it('sin sub-proyectos agrupa por contexto normalizado', () => {
    const groups = buildAreaGroups(ENTITIES, null);
    const keys = groups.map((g) => g.key).sort();
    expect(keys).toEqual(['__sin_area__', 'logistica', 'ventas']);
    const ventas = groups.find((g) => g.key === 'ventas');
    expect(ventas?.label).toBe('Ventas');
  });

  it('sin contextos ni sub-proyectos devuelve un grupo único', () => {
    const groups = buildAreaGroups(
      [ent('ENT-001', 'A', { bounded_context: null })],
      []
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].entityCodes).toEqual(['ENT-001']);
  });
});

describe('buildAreaDiagram', () => {
  const groups = buildAreaGroups(ENTITIES, SUBPROJECTS);
  const comercial = groups[0];

  it('incluye entidades del área con atributos y PK', () => {
    const code = buildAreaDiagram(comercial, ENTITIES, RELATIONSHIPS);
    expect(code).toContain('%% Área: Comercial');
    expect(code).toContain('ORDEN {');
    expect(code).toContain('        uuid id PK');
    expect(code).toContain('        string nombre');
    expect(code).toContain('CLIENTE {');
    // La externa no aparece sin el flag.
    expect(code).not.toContain('CAMION');
    expect(code).toContain('ORDEN ||--o{ CLIENTE : "belongs_to"');
    expect(code).toContain('class ORDEN agg_root');
  });

  it('con includeExternal agrega la contraparte gris punteada', () => {
    const code = buildAreaDiagram(comercial, ENTITIES, RELATIONSHIPS, {
      includeExternal: true
    });
    expect(code).toContain('CAMION');
    expect(code).toContain('classDef extern fill:#2a2a2a,stroke:#666,stroke-dasharray:5 5');
    expect(code).toContain('class CAMION extern');
    expect(code).toContain('CAMION ||--o{ ORDEN : "asigna"');
  });

  it('es determinista', () => {
    const a = buildAreaDiagram(comercial, ENTITIES, RELATIONSHIPS);
    const b = buildAreaDiagram(comercial, ENTITIES, RELATIONSHIPS);
    expect(a).toBe(b);
  });
});

describe('buildCompactOverview', () => {
  const groups = buildAreaGroups(ENTITIES, SUBPROJECTS);

  it('entidades sin atributos, con relaciones y colores por área', () => {
    const code = buildCompactOverview(ENTITIES, RELATIONSHIPS, groups);
    expect(code).toContain('    ORDEN');
    expect(code).not.toContain('ORDEN {');
    // MANY_TO_ONE no está en el mapa -> notación default 1:N.
    expect(code).toContain('ORDEN ||--o{ CLIENTE : "belongs_to"');
    // El self-loop se descarta.
    expect(code).not.toContain('"self"');
    expect(code).toContain('classDef area');
    expect(code).toContain('class ORDEN area');
    expect(code).toContain('class ORDEN agg_root');
  });

  it('sin entidades devuelve erDiagram vacío', () => {
    expect(buildCompactOverview([], [], [])).toBe('erDiagram');
  });
});
