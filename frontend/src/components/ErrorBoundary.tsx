import { Component, type ErrorInfo, type ReactNode } from "react";
import { EmptyState } from "./EmptyState";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** 顶层错误边界：捕获渲染期异常，避免整页白屏。 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("界面渲染异常", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 48 }}>
          <EmptyState
            title="界面出现异常"
            description={this.state.error.message || "请刷新页面重试。"}
          />
        </div>
      );
    }
    return this.props.children;
  }
}
