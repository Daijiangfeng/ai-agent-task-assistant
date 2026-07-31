import type {
  InputHTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from "react";
import { cx } from "../lib/cx";
import styles from "./Field.module.css";

interface BaseProps {
  label?: ReactNode;
  hint?: string;
  error?: string;
}

type InputProps = BaseProps &
  InputHTMLAttributes<HTMLInputElement> & {
    /** pill 圆角搜索框样式。 */
    pill?: boolean;
  };

/** 单行输入，支持 pill 搜索框变体。 */
export function Field({ label, hint, error, pill, className, id, ...rest }: InputProps) {
  const inputId = id ?? rest.name;
  return (
    <label className={styles.wrap} htmlFor={inputId}>
      {label ? <span className={styles.label}>{label}</span> : null}
      <input
        id={inputId}
        className={cx(styles.input, pill && styles.pill, error && styles.invalid, className)}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
      {error ? (
        <span className={styles.error}>{error}</span>
      ) : hint ? (
        <span className={styles.hint}>{hint}</span>
      ) : null}
    </label>
  );
}

type TextAreaProps = BaseProps & TextareaHTMLAttributes<HTMLTextAreaElement>;

/** 多行文本域。 */
export function TextArea({
  label,
  hint,
  error,
  className,
  id,
  ...rest
}: TextAreaProps) {
  const areaId = id ?? rest.name;
  return (
    <label className={styles.wrap} htmlFor={areaId}>
      {label ? <span className={styles.label}>{label}</span> : null}
      <textarea
        id={areaId}
        className={cx(styles.input, styles.area, error && styles.invalid, className)}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
      {error ? (
        <span className={styles.error}>{error}</span>
      ) : hint ? (
        <span className={styles.hint}>{hint}</span>
      ) : null}
    </label>
  );
}
