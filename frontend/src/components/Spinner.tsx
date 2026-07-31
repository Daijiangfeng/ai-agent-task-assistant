import styles from "./Spinner.module.css";

interface SpinnerProps {
  label?: string;
}

/** 加载指示器。 */
export function Spinner({ label = "加载中…" }: SpinnerProps) {
  return (
    <div className={styles.wrap} role="status">
      <span className={styles.ring} aria-hidden />
      <span className={styles.label}>{label}</span>
    </div>
  );
}
