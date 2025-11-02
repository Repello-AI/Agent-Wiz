import { useMemo } from 'react';
import { getBezierPath } from 'reactflow';
import type { ConnectionLineComponentProps } from 'reactflow';

export default function FloatingConnectionLine({
  toX,
  toY,
  fromX,
  fromY,
  fromPosition,
  toPosition,
}: ConnectionLineComponentProps) {
  const edgePath = useMemo(() => {
    const [path] = getBezierPath({
      sourceX: fromX,
      sourceY: fromY,
      targetX: toX,
      targetY: toY,
      sourcePosition: fromPosition,
      targetPosition: toPosition,
    });
    return path;
  }, [fromX, fromY, toX, toY, fromPosition, toPosition]);

  return (
    <g>
      <path
        fill="none"
        stroke="#b1b1b7"
        strokeWidth={2}
        className="animated"
        d={edgePath}
        style={{
          strokeDasharray: '5, 5',
        }}
      />
      <circle
        cx={toX}
        cy={toY}
        fill="#fff"
        r={6}
        stroke="#b1b1b7"
        strokeWidth={2}
      />
    </g>
  );
}

