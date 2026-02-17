interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  borderRadius?: string;
  style?: React.CSSProperties;
}

function Skeleton({ width = '100%', height = '16px', borderRadius = '4px', style }: SkeletonProps) {
  return (
    <div
      data-testid="skeleton"
      style={{
        width,
        height,
        borderRadius,
        background: 'linear-gradient(90deg, #1E293B 25%, #2D3B4F 50%, #1E293B 75%)',
        backgroundSize: '200% 100%',
        animation: 'skeleton-shimmer 1.5s ease-in-out infinite',
        ...style,
      }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div
      style={{
        background: '#1E293B',
        borderRadius: '8px',
        padding: '16px 20px',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
      }}
    >
      <Skeleton width="12px" height="12px" borderRadius="50%" />
      <div style={{ flex: 1 }}>
        <Skeleton width="40%" height="14px" style={{ marginBottom: '8px' }} />
        <Skeleton width="60%" height="10px" />
      </div>
    </div>
  );
}

export function SkeletonRow() {
  return (
    <div
      style={{
        background: '#1E293B',
        borderRadius: '6px',
        padding: '12px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: '12px',
      }}
    >
      <Skeleton width="56px" height="14px" />
      <div style={{ flex: 1 }}>
        <Skeleton width="50%" height="12px" style={{ marginBottom: '6px' }} />
        <Skeleton width="30%" height="10px" />
      </div>
      <Skeleton width="48px" height="20px" borderRadius="4px" />
    </div>
  );
}

export default Skeleton;
