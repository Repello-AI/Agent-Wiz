import dagre from 'dagre';
import agentIcon from './assets/agent.svg';
import toolIcon from './assets/tool.svg';
import plannerIcon from './assets/Planner.svg';
import startIcon from './assets/start.svg';
import endIcon from './assets/end.svg';
import genericIcon from './assets/generic.svg';

// Support runtime injection via window.AGENT_GRAPH_DATA
declare global {
  interface Window {
    AGENT_GRAPH_DATA?: any;
  }
}

const raw = window.AGENT_GRAPH_DATA || { nodes: [], edges: [], metadata: {} };

export type RFNode = any;
export type RFEdge = any;

type NodeType = 'agent' | 'tool' | 'orchestrator' | 'start' | 'end' | 'team';

const repelloPalette = {
  purple: '#7C4DFF',
  teal: '#00BFA6',
  amber: '#FFB300',
  blue: '#1976D2',
  softPurple: '#B39DDB',
};

const typeMeta: Record<NodeType, { icon: string; color: string }> = {
  agent: { icon: agentIcon, color: repelloPalette.softPurple },
  tool: { icon: toolIcon, color: repelloPalette.teal },
  orchestrator: { icon: plannerIcon, color: repelloPalette.amber },
  start: { icon: startIcon, color: repelloPalette.blue },
  end: { icon: endIcon, color: repelloPalette.blue },
  team: { icon: genericIcon, color: repelloPalette.purple },
};

function lookup(type?: string) {
  if (!type) return typeMeta.agent;
  const key = type.toLowerCase();
  if ((Object.keys(typeMeta) as string[]).includes(key)) {
    return typeMeta[key as NodeType];
  }
  return typeMeta.agent;
}

/**
 * Using dagre to compute node positions for a neat system diagram.
 * - graph: raw agent-graph JSON
 * - opts: tuning for spacing
 */
function dagreLayout(nodes: RFNode[], edges: RFEdge[], opts = { rankdir: 'LR', nodeSep: 220, rankSep: 120 }) {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: opts.rankdir, nodesep: opts.nodeSep, ranksep: opts.rankSep });
  g.setDefaultEdgeLabel(() => ({}));

  // Set node sizes for dagre (React Flow uses top-left anchor, dagre uses center)
  nodes.forEach((n) => g.setNode(n.id, { 
    width: n.data.width ?? 320, 
    height: n.data.height ?? 140 
  }));
  
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  // Convert dagre positions to React Flow positions
  const positionedNodes = nodes.map((n) => {
    const nd = g.node(n.id);
    return {
      ...n,
      position: {
        x: nd.x - (n.data.width ?? 320) / 2,
        y: nd.y - (n.data.height ?? 140) / 2,
      },
    };
  });

  return { nodes: positionedNodes, edges };
}

export function loadGraph() {
  const graphData: any = raw || { nodes: [], edges: [], metadata: {} };

  // Normalize nodes
  const nodes: RFNode[] =
    (graphData.nodes || []).map((node: any, i: number) => {
      const nt = (node.node_type || node.nodeType || 'agent').toString();
      const meta = lookup(nt);
      const nodeLabel = node.name ?? node.label ?? `node-${i}`;

      // optimized node sizes for visibility and clarity
      const baseSize = nt.toLowerCase() === 'tool' ? { width: 240, height: 100 } : nt.toLowerCase().includes('start') || nt.toLowerCase().includes('end') ? { width: 120, height: 120 } : { width: 320, height: 140 };

      return {
        id: nodeLabel,
        type: 'customNode',
        data: {
          label: nodeLabel,
          functionName: node.function_name || null,
          docstring: node.docstring || null,
          nodeType: nt,
          sourceLocation: node.source_location || null,
          metadata: node.metadata || {},
          icon: meta.icon,
          color: meta.color,
          width: baseSize.width,
          height: baseSize.height,
        },
        selectable: true,
        draggable: true,
        position: { x: 0 + (i % 4) * 200, y: 80 + Math.floor(i / 4) * 140 },
        ...baseSize,
      };
    }) || [];

  // Normalize edges
  const edges: RFEdge[] =
    (graphData.edges || []).map((edge: any, idx: number) => {
      const cond = edge.condition?.type || 'static';
      const styleColor = cond === 'member_of_team' ? repelloPalette.purple : cond === 'group_sequence' ? repelloPalette.amber : '#2f4f8f';
      const animated = cond === 'async' || false;
      const isDashed = cond === 'group_sequence';

      return {
        id: edge.id || `e-${edge.source}-${edge.target}-${idx}`,
        source: edge.source,
        target: edge.target,
        label: cond !== 'static' ? cond : undefined,
        animated,
        type: 'floating',
        pathOptions: {
          curvature: 0.6, // smooth curved edges
        },
        style: { 
          stroke: styleColor, 
          strokeWidth: isDashed ? 3 : 4,
          strokeDasharray: isDashed ? '8 5' : undefined,
          opacity: 0.9
        },
        markerEnd: {
          type: 'arrowclosed',
          color: styleColor,
          width: 32,
          height: 32,
        },
        labelStyle: {
          fill: '#fff',
          fontWeight: 600,
        },
        labelShowBg: true,
        labelBgStyle: {
          fill: styleColor,
          fillOpacity: 0.2,
        },
        data: edge.metadata || {},
      };
    }) || [];

  // semantic grouping pre-processing
  // placing Start nodes left and End nodes right by tagging rank
  // dagre will honor general spacing with rankdir LR by default

  // Optimal spacing to reduce node overlap and improve visibility
  const { nodes: positionedNodes, edges: positionedEdges } = dagreLayout(nodes, edges, { 
    rankdir: 'LR', 
    nodeSep: 380,  // Horizontal spacing for better separation
    rankSep: 240   // Vertical spacing to reduce overlaps
  });

  return { nodes: positionedNodes, edges: positionedEdges, framework: graphData.metadata?.framework || 'unknown' };
}
