'use client';
import { useMemo } from 'react';
import { Background, Controls, Handle, Position, ReactFlow, type NodeProps } from '@xyflow/react';
import type { Graph, WordStatus } from '../lib/types';

// Neo4j Browser-style palette: one colour per label, words coloured by learning status.
const COLORS: Record<string, string> = {
  learner: '#173f34', goal: '#f79767', skill: '#57c7e3', level: '#8dcc93', lesson: '#4c8eda',
  context: '#ffc454', activity: '#ffd98e', mission: '#da7194', experience: '#569480', evidence: '#c9b18f', gap: '#f16667',
};
const WORD_COLORS: Record<WordStatus, string> = { new: '#d4d4d4', introduced: '#ffe081', practiced: '#8dcc93', fluent: '#167d60' };
const SIZES: Record<string, number> = { learner: 96, goal: 78, skill: 78, level: 66, lesson: 70, context: 72, activity: 64, mission: 74, experience: 68, evidence: 60, gap: 64, word: 50 };
const DARK = new Set(['learner', 'experience', 'lesson', 'mission', 'gap']);
const LIVE_EDGES = ['REVEALED', 'DEMONSTRATED', 'USED', 'STRUGGLED_WITH'];

type Node = Graph['nodes'][number];
const fill = (n: Node) => n.type === 'word' ? WORD_COLORS[n.data.status || 'new'] : COLORS[n.type] || '#a5abb6';
const size = (n: Node) => SIZES[n.type] || 60;

// Deterministic force-directed layout: learner pinned at the centre, springs along relationships,
// repulsion between all nodes. Same graph always produces the same picture.
// ponytail: O(n²) per iteration fits this learner graph; use an indexed layout if graph volume grows.
function layout(graph: Graph) {
  const ids = graph.nodes.map(n => n.id);
  const index = new Map(ids.map((id, i) => [id, i]));
  const pos = graph.nodes.map((n, i) => {
    const angle = (i / Math.max(1, ids.length)) * Math.PI * 2;
    const r = n.type === 'learner' ? 0 : n.type === 'word' ? 420 : 240;
    return { x: Math.cos(angle) * r, y: Math.sin(angle) * r };
  });
  const links = graph.edges.map(e => [index.get(e.source), index.get(e.target)]).filter((l): l is [number, number] => l[0] !== undefined && l[1] !== undefined);
  for (let step = 0; step < 400; step++) {
    const cool = 1 - step / 400;
    const force = pos.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < pos.length; i++) for (let j = i + 1; j < pos.length; j++) {
      const dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y;
      const d2 = Math.max(dx * dx + dy * dy, 100), d = Math.sqrt(d2);
      const push = 26000 / d2;
      force[i].x += dx / d * push; force[i].y += dy / d * push;
      force[j].x -= dx / d * push; force[j].y -= dy / d * push;
    }
    for (const [a, b] of links) {
      const dx = pos[b].x - pos[a].x, dy = pos[b].y - pos[a].y;
      const d = Math.max(Math.hypot(dx, dy), 1), pull = (d - 170) * 0.04;
      force[a].x += dx / d * pull; force[a].y += dy / d * pull;
      force[b].x -= dx / d * pull; force[b].y -= dy / d * pull;
    }
    pos.forEach((p, i) => {
      if (graph.nodes[i].type === 'learner') return;
      const fx = force[i].x - p.x * 0.004, fy = force[i].y - p.y * 0.004;
      const m = Math.hypot(fx, fy), cap = 30 * cool + 1;
      p.x += m > cap ? fx / m * cap : fx; p.y += m > cap ? fy / m * cap : fy;
    });
  }
  return pos;
}

function Circle({ data }: NodeProps) {
  const d = data as { label: string; caption: string; title: string; size: number; color: string; dark: boolean; pulse: boolean };
  const hidden = { opacity: 0, left: '50%', top: '50%', transform: 'translate(-50%, -50%)', width: 1, height: 1, minWidth: 0, minHeight: 0, border: 0 };
  return <div className={`neo-node${d.pulse ? ' pulse' : ''}`} title={d.title}
    style={{ width: d.size, height: d.size, background: d.color, color: d.dark ? '#fff' : '#1d2a24' }}>
    <Handle type="target" position={Position.Top} style={hidden} />
    <span>{d.label}</span>
    {d.caption && <small>{d.caption}</small>}
    <Handle type="source" position={Position.Top} style={hidden} />
  </div>;
}
const nodeTypes = { circle: Circle };
const short = (s: string, n: number) => s.length > n ? `${s.slice(0, n - 1)}…` : s;
const pct = (n = 0) => `${Math.round(n * 100)}%`;

export default function GraphView({ graph }: { graph: Graph }) {
  const { nodes, edges } = useMemo(() => {
    const pos = layout(graph);
    // Both stores project experiences in chronological order.
    const latestExperience = graph.nodes.filter(n => n.type === 'experience').at(-1)?.id;
    const latestEdges = new Set(graph.edges.filter(e => e.source === latestExperience && LIVE_EDGES.includes(e.label)).map(e => e.id));
    const touched = new Set(graph.edges.filter(e => latestEdges.has(e.id)).flatMap(e => [e.source, e.target]));
    const nodes = graph.nodes.map((n, i) => {
      const s = size(n);
      const caption = n.type === 'skill' ? `K ${pct(n.data.knowledge)} · A ${pct(n.data.application)}` : n.type === 'word' ? '' : n.type;
      const detail = [n.type.toUpperCase(), n.data.label, n.data.meaning, n.data.status, n.data.evidence && `“${n.data.evidence}”`].filter(Boolean).join('\n');
      return {
        id: n.id, type: 'circle', position: { x: pos[i].x - s / 2, y: pos[i].y - s / 2 },
        data: { label: short(n.data.label, n.type === 'word' ? 12 : 22), caption, title: detail, size: s, color: fill(n),
          dark: DARK.has(n.type) || (n.type === 'word' && n.data.status === 'fluent'), pulse: touched.has(n.id) },
      };
    });
    const edges = graph.edges.map(e => {
      const live = latestEdges.has(e.id);
      const color = e.label === 'STRUGGLED_WITH' || e.label === 'REVEALED' ? '#f16667' : live ? '#167d60' : '#a5abb6';
      return {
        id: e.id, source: e.source, target: e.target, type: 'straight', label: e.label, animated: live,
        style: { stroke: color, strokeWidth: live ? 2 : 1.2 },
        markerEnd: { type: 'arrowclosed' as const, color, width: 14, height: 14 },
        labelStyle: { fontSize: 8, fill: '#58625d', letterSpacing: '0.04em' },
        labelBgStyle: { fill: '#fbfcf8', fillOpacity: 0.85 }, labelBgPadding: [3, 1] as [number, number],
      };
    });
    return { nodes, edges };
  }, [graph]);
  const present = new Set(graph.nodes.map(n => n.type));
  return <div className="neo-graph">
    <div className="neo-legend">
      {Object.keys(COLORS).filter(t => present.has(t)).map(t => <span key={t}><i style={{ background: COLORS[t] }} />{t}</span>)}
      {(Object.keys(WORD_COLORS) as WordStatus[]).map(s => <span key={s}><i style={{ background: WORD_COLORS[s] }} />word · {s}</span>)}
    </div>
    <div className="graph" aria-label="Learning relationships">
      <ReactFlow key={graph.nodes.length} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.08 }}
        nodesDraggable nodesConnectable={false} minZoom={0.15}>
        <Background color="#dfe6df" gap={22} /><Controls showInteractive={false} />
      </ReactFlow>
    </div>
  </div>;
}
