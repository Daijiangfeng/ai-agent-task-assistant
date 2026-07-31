import styles from "./ProgressBar.module.css";

interface ProgressBarProps {
  /** 0-100 */
  value: number;
  label?: string;
}

/** 进度条，唯一强调色填充。 */
export function ProgressBar({ value, label }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className={styles.wrap}>
      <div
        className={styles.track}
        role="progressbar"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={styles.fill} style={{ width: `${clamped}%` }} />
      </div>
      <span className={styles.label}>{label ?? `${Math.round(clamped)}%`}</span>
    </div>
  );
}
