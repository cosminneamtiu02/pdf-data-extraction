// THIS FILE IS GENERATED FROM errors.yaml
// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.

export type ErrorCode =
  | "NOT_FOUND"
  | "CONFLICT"
  | "VALIDATION_FAILED"
  | "INTERNAL_ERROR"
  | "SKILL_VALIDATION_FAILED"
  | "SKILL_NOT_FOUND";

export interface ErrorParamsByCode {
  NOT_FOUND: Record<string, never>;
  CONFLICT: Record<string, never>;
  VALIDATION_FAILED: { field: string; reason: string };
  INTERNAL_ERROR: Record<string, never>;
  SKILL_VALIDATION_FAILED: { reason: string };
  SKILL_NOT_FOUND: { name: string; version: string };
}

export interface ApiErrorPayload<C extends ErrorCode = ErrorCode> {
  code: C;
  params: ErrorParamsByCode[C];
  details: Array<{ field: string; reason: string }> | null;
  request_id: string;
}

export const ERROR_CODES: readonly ErrorCode[] = ["NOT_FOUND", "CONFLICT", "VALIDATION_FAILED", "INTERNAL_ERROR", "SKILL_VALIDATION_FAILED", "SKILL_NOT_FOUND"] as const;

export const HTTP_STATUS_BY_CODE: Record<ErrorCode, number> = {
  NOT_FOUND: 404,
  CONFLICT: 409,
  VALIDATION_FAILED: 422,
  INTERNAL_ERROR: 500,
  SKILL_VALIDATION_FAILED: 500,
  SKILL_NOT_FOUND: 404,
};
