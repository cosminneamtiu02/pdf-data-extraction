/**
 * Types for API error responses.
 * These match the backend's ErrorResponse shape.
 */

export type ErrorCode =
  | "NOT_FOUND"
  | "CONFLICT"
  | "VALIDATION_FAILED"
  | "INTERNAL_ERROR"
  | "RATE_LIMITED"
  | "WIDGET_NOT_FOUND"
  | "WIDGET_NAME_CONFLICT"
  | "WIDGET_NAME_TOO_LONG";

export interface ErrorDetail {
  field: string;
  reason: string;
}

export interface ApiErrorPayload {
  code: ErrorCode;
  params: Record<string, string | number | boolean>;
  details: ErrorDetail[] | null;
  request_id: string;
}

export interface ApiErrorResponse {
  error: ApiErrorPayload;
}
