/** 轻量 classnames 拼接工具（避免额外依赖）。 */
export function cx(
  ...parts: Array<string | false | null | undefined>
): string {
  return parts.filter(Boolean).join(" ");
}
