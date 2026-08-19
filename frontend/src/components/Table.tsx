import type { ReactNode } from "react";
import { cx } from "../lib/cx";
import styles from "./Table.module.css";

interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  width?: string;
  align?: "left" | "right" | "center";
}

interface TableProps<T> {
  columns: Array<Column<T>>;
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
}

/** 发丝分隔线数据表。 */
export function Table<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  rowClassName,
}: TableProps<T>) {
  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                style={{ width: col.width, textAlign: col.align ?? "left" }}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={cx(
                onRowClick ? styles.clickable : undefined,
                rowClassName?.(row),
              )}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td key={col.key} style={{ textAlign: col.align ?? "left" }}>
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
