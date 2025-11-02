import { useMemo } from 'react';
import { getBezierPath } from 'reactflow';
import type { EdgeProps } from 'reactflow';

export default function FloatingEdge({
  id,
  style,
  markerEnd,
  sourcePosition,
  targetPosition,
  sourceX,
  sourceY,
  targetX,
  targetY,
}: EdgeProps) {
  const edgePath = useMemo(() => {
    const [path] = getBezierPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition,
      targetPosition,
    });
    return path;
  }, [sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition]);

  return (
    <g className="floating-edge">
      <path
        id={id}
        style={style}
        className="react-flow__edge-path"
        d={edgePath}
        markerEnd={markerEnd}
      />
    </g>
  );
}
