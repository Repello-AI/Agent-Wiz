import { memo } from 'react';
import { Handle, Position } from 'react-flow-renderer';

export default memo(({ data }: any) => (
  <div
    style={{
      background: data.color,
      borderRadius: 16,
      boxShadow: '0 2px 8px rgba(80,80,120,0.08)',
      padding: 16,
      minWidth: 120,
      maxWidth: 180,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      border: '2px solid #fff',
    }}
>
    <img src={data.icon} alt={data.nodeType} style={{ width: 32, height: 32, marginBottom: 8 }} />
    <div style={{
      fontWeight: 600,
      fontSize: 16,
      color: '#222',
      textAlign: 'center',
      wordBreak: 'break-word',
      marginBottom: 4,
      maxWidth: 140,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
    }}>{data.label}</div>
    <div style={{ fontSize: 12, color: '#555', marginBottom: 2 }}>
      {data.functionName}
    </div>
    <Handle type="target" position={Position.Top} style={{ background: '#1976d2' }} />
    <Handle type="source" position={Position.Bottom} style={{ background: '#1976d2' }} />
  </div>
));