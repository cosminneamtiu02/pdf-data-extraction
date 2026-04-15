// THIS FILE IS GENERATED FROM errors.yaml
// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.

export type ErrorCode =
  | "NOT_FOUND"
  | "VALIDATION_FAILED"
  | "INTERNAL_ERROR"
  | "SKILL_VALIDATION_FAILED"
  | "SKILL_NOT_FOUND"
  | "INTELLIGENCE_UNAVAILABLE"
  | "STRUCTURED_OUTPUT_FAILED";

export interface ErrorParamsByCode {
  NOT_FOUND: Record<string, never>;
  VALIDATION_FAILED: { field: string; reason: string };
  INTERNAL_ERROR: Record<string, never>;
  SKILL_VALIDATION_FAILED: { file: string; reason: string };
  SKILL_NOT_FOUND: { name: string; version: string };
  INTELLIGENCE_UNAVAILABLE: Record<string, never>;
  STRUCTURED_OUTPUT_FAILED: Record<string, never>;
}

export interface ApiErrorPayload<C extends ErrorCode = ErrorCode> {
  code: C;
  params: ErrorParamsByCode[C];
  details: Array<{ field: string; reason: string }> | null;
  request_id: string;
}

export const ERROR_CODES: readonly ErrorCode[] = ["NOT_FOUND", "VALIDATION_FAILED", "INTERNAL_ERROR", "SKILL_VALIDATION_FAILED", "SKILL_NOT_FOUND", "INTELLIGENCE_UNAVAILABLE", "STRUCTURED_OUTPUT_FAILED"] as const;

export const HTTP_STATUS_BY_CODE: Record<ErrorCode, number> = {
  NOT_FOUND: 404,
  VALIDATION_FAILED: 422,
  INTERNAL_ERROR: 500,
  SKILL_VALIDATION_FAILED: 500,
  SKILL_NOT_FOUND: 404,
  INTELLIGENCE_UNAVAILABLE: 503,
  STRUCTURED_OUTPUT_FAILED: 502,
};
