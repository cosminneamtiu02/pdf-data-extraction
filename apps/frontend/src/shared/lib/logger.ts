/**
 * Logger wrapper. Uses console in development, no-op in production.
 * Real frontend error tracking (Sentry, etc.) is a per-project decision.
 */

const isDev = import.meta.env?.DEV ?? true;

export const logger = {
  info: (...args: unknown[]) => {
    if (isDev) console.info(...args);
  },
  warn: (...args: unknown[]) => {
    if (isDev) console.warn(...args);
  },
  error: (...args: unknown[]) => {
    if (isDev) console.error(...args);
  },
};
