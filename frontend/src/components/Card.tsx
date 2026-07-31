import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../lib/cx";
import styles from "./Card.module.css";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** 是否使用唯一产品阴影（用于"内容卡放在表面"意象）。 */
  elevated?: boolean;
  children: ReactNode;
}

/** 18px 圆角 + 发丝边的内容卡（对应 store-utility-card）。 */
export function Card({ elevated = false, children, className, ...rest }: CardProps) {
  return (
    <div className={cx(styles.card, elevated && styles.elevated, className)} {...rest}>
      {children}
    </div>
  );
}
