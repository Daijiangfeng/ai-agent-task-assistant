import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "../lib/cx";
import styles from "./Button.module.css";

type Variant = "primary" | "ghost" | "tool";
type Size = "md" | "sm";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  children: ReactNode;
}

/** pill 主按钮 / 幽灵按钮 / 暗色工具按钮，统一按压 scale(0.95)。 */
export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  children,
  className,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={cx(styles.btn, styles[variant], styles[size], className)}
      disabled={disabled || loading}
      aria-busy={loading}
      {...rest}
    >
      {loading ? <span className={styles.spinner} aria-hidden /> : null}
      <span>{children}</span>
    </button>
  );
}
