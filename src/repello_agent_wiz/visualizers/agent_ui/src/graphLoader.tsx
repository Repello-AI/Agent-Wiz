import graphData from './agent_graph.json';

type NodeType = 'agent' | 'tool' | 'orchestrator' | 'start' | 'end' | 'team';

const typeMeta: Record<NodeType, { icon: string; color: string }> = {
  agent:   { icon: '/assets/agent.svg', color: '#B39DDB' },
  tool:    { icon: '/assets/tool.svg', color: '#80CBC4' },
  orchestrator: { icon: '/assets/orchestrator.svg', color: '#FFB74D' },
  start:   { icon: '/assets/start.svg', color: '#90CAF9' },
  end:     { icon: '/assets/end.svg', color: '#90CAF9' },
  team:    { icon: '/assets/team.svg', color: '#CE93D8' },
};

function getTypeMeta(type: string) {
  if (typeMeta.hasOwnProperty(type)) {
    return typeMeta[type as NodeType];
  }
  // fallback for unknown types
  return { icon: '/assets/agent.svg', color: '#B39DDB' };
}

export function loadGraph() {
  const nodes = graphData.nodes
    .filter((n: any) => n.name)
    .map((node: any, idx: number) => {
      const typeKey = (node.node_type || '').toLowerCase();
      const meta = getTypeMeta(typeKey);
      return {
        id: node.name,
        type: typeKey || 'agent',
        data: {
          label: node.name,
          functionName: node.function_name,
          docstring: node.docstring,
          nodeType: node.node_type,
          sourceLocation: node.source_location,
          metadata: node.metadata,
          icon: meta.icon,
          color: meta.color,
        },
        position: { x: 200 + idx * 180, y: 120 + (idx % 5) * 120 },
      };
    });

  const edges = graphData.edges
    .filter((e: any) => e.source && e.target)
    .map((edge: any, idx: number) => ({
      id: `e${edge.source}-${edge.target}-${idx}`,
      source: edge.source,
      target: edge.target,
      label: edge.condition?.type || '',
      data: edge.metadata,
      animated: edge.metadata?.alert ? true : false,
      style: {
        stroke: edge.metadata?.alert ? '#E57373' : '#1976d2',
        strokeWidth: edge.metadata?.alert ? 3 : 2,
      },
    }));

  return { nodes, edges, framework: graphData.metadata?.framework || 'unknown' };
}