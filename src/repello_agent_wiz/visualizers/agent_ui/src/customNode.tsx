import { memo } from 'react';
import { Handle, Position } from 'reactflow';
import type { NodeProps } from 'reactflow';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import './customNode.css';

type Data = {
  label?: string;
  functionName?: string | null;
  docstring?: string | null;
  icon?: string;
  color?: string;
  nodeType?: string | null;
  dimmed?: boolean;
};

export default memo(function CustomNode({ data }: NodeProps<Data>) {
  const color = data.color ?? '#B39DDB';
  const dim = !!data.dimmed;
  const type = (data.nodeType || '').toLowerCase();

  // style classes by type to allow distinct shapes
  const classes = ['aw-node', `aw-node--${type || 'agent'}`, dim ? 'aw-node--dim' : ''].join(' ').trim();

  return (
    <Box
      className={classes} role="group" aria-label={data.label}
    sx={{
      borderRadius: 2,
      boxShadow: '0 6px 18px rgba(10,20,40,0.08)',
      padding: 1,
      minWidth: 120, maxWidth: 380, 
      display: 'flex', 
      gap: 1, 
      alignItems: 'center', 
      border: `1px solid ${color}33`, 
      background: `linear-gradient(180deg, ${color}12, ${color}06)`,
    }}>
      <div className="aw-node__left">
        <img src={data.icon || ''} alt={data.label} className="aw-node__icon" draggable={false} />
      </div>

      <div className="aw-node__body">
        <Typography className="aw-node__title">{data.label}</Typography>
        {data.functionName && <Typography className="aw-node__subtitle">{data.functionName}</Typography>}
        {data.docstring && <Typography className="aw-node__doc">{data.docstring.slice(0, 140)}{data.docstring.length > 140 ? '…' : ''}</Typography>}
      </div>

      {/* connector handles: top + bottom for simplicity */}
      <Handle type="target" position={Position.Top} className="aw-handle aw-handle--top" isConnectable />
      <Handle type="source" position={Position.Bottom} className="aw-handle aw-handle--bottom" isConnectable />
    </Box>
  );
});