// THIS FILE IS GENERATED FROM errors.yaml
// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.

export type ErrorCode =
  | "NOT_FOUND"
  | "CONFLICT"
  | "VALIDATION_FAILED"
  | "INTERNAL_ERROR"
  | "RATE_LIMITED"
  | "WIDGET_NOT_FOUND"
  | "WIDGET_NAME_CONFLICT"
  | "WIDGET_NAME_TOO_LONG";

export interface ErrorParamsByCode {
  NOT_FOUND: Record<string, never>;
  CONFLICT: Record<string, never>;
  VALIDATION_FAILED: { field: string; reason: string };
  INTERNAL_ERROR: Record<string, never>;
  RATE_LIMITED: { retry_after_seconds: number };
  WIDGET_NOT_FOUND: { widget_id: string };
  WIDGET_NAME_CONFLICT: { name: string };
  WIDGET_NAME_TOO_LONG: { name: string; max_length: number; actual_length: number };
}

export interface ApiErrorPayload<C extends ErrorCode = ErrorCode> {
  code: C;
  params: ErrorParamsByCode[C];
  details: Array<{ field: string; reason: string }> | null;
  request_id: string;
}

export const ERROR_CODES: readonly ErrorCode[] = ["NOT_FOUND", "CONFLICT", "VALIDATION_FAILED", "INTERNAL_ERROR", "RATE_LIMITED", "WIDGET_NOT_FOUND", "WIDGET_NAME_CONFLICT", "WIDGET_NAME_TOO_LONG"] as const;

export const HTTP_STATUS_BY_CODE: Record<ErrorCode, number> = {
  NOT_FOUND: 404,
  CONFLICT: 409,
  VALIDATION_FAILED: 422,
  INTERNAL_ERROR: 500,
  RATE_LIMITED: 429,
  WIDGET_NOT_FOUND: 404,
  WIDGET_NAME_CONFLICT: 409,
  WIDGET_NAME_TOO_LONG: 422,
};
