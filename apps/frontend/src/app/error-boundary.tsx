/**
 * Root error boundary — catches React render errors.
 * Uses a function component wrapper to access i18n hooks,
 * since class components cannot use hooks directly.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { logger } from "@/shared/lib/logger";

interface FallbackProps {
  error: Error | null;
}

function DefaultFallback({ error }: FallbackProps) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="text-center">
        <h1 className="text-2xl font-bold text-red-600">
          {t("common:error_boundary.title", "Something went wrong")}
        </h1>
        <p className="mt-2 text-gray-600">
          {error?.message ?? t("common:error_boundary.message", "An unexpected error occurred.")}
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-4 rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
        >
          {t("common:error_boundary.reload", "Reload")}
        </button>
      </div>
    </div>
  );
}

interface Props {
  children: ReactNode;
  fallback?: ReactNode | undefined;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    logger.error("Uncaught render error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? <DefaultFallback error={this.state.error} />;
    }
    return this.props.children;
  }
}
