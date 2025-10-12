const accentColors = {
  agent: '#164EAF',         // Repello blue
  tool: '#FFB026',          // Repello orange
  orchestrator: '#A738D6',  // Repello purple
  default: '#EBEBEB'
};

export { accentColors };

// CustomNode.jsx
// import { Handle } from 'react-flow-renderer';
// import agentIcon from './assets/agent.svg';
// import toolIcon from './assets/tool.svg';
// import orchestratorIcon from './assets/orchestrator.svg';

// const iconMap = {
//   agent: agentIcon,
//   tool: toolIcon,
//   orchestrator: orchestratorIcon
// };

// export default function CustomNode({ data, type, selected }) {
//   const color = accentColors[type] || accentColors.default;
//   return (
//     <div
//       style={{
//         borderRadius: 16,
//         boxShadow: selected ? '0 0 0 4px #00BFFF' : '0 2px 8px rgba(0,0,0,0.08)',
//         border: `2px solid ${color}`,
//         background: '#fff',
//         padding: 12,
//         minWidth: 120,
//         display: 'flex',
//         alignItems: 'center',
//         gap: 12
//       }}
//     >
//     <img src={iconMap[type] || iconMap.agent} alt={type} style={{ width: 32, height: 32 }} />
//       <div>
//         <div style={{ fontWeight: 700, color }}>{data.name}</div>
//         <div style={{ fontSize: 12, color: '#555' }}>{data.node_type}</div>
//         {data.function_name && <div style={{ fontSize: 11 }}>{data.function_name}</div>}
//       </div>
//       <Handle type="target" position="top" />
//       <Handle type="source" position="bottom" />
//     </div>
//   );
// }