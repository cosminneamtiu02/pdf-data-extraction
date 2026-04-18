// THIS FILE IS GENERATED FROM errors.yaml
// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.

export type ErrorCode =
  | "NOT_FOUND"
  | "VALIDATION_FAILED"
  | "INTERNAL_ERROR"
  | "SKILL_VALIDATION_FAILED"
  | "SKILL_NOT_FOUND"
  | "PDF_INVALID"
  | "PDF_PASSWORD_PROTECTED"
  | "PDF_TOO_MANY_PAGES"
  | "PDF_NO_TEXT_EXTRACTABLE"
  | "INTELLIGENCE_UNAVAILABLE"
  | "STRUCTURED_OUTPUT_FAILED"
  | "INTELLIGENCE_TIMEOUT"
  | "EXTRACTION_BUDGET_EXCEEDED"
  | "PDF_TOO_LARGE"
  | "PDF_PARSER_UNAVAILABLE"
  | "EXTRACTION_OVERLOADED";

export interface ErrorParamsByCode {
  NOT_FOUND: Record<string, never>;
  VALIDATION_FAILED: { field: string; reason: string };
  INTERNAL_ERROR: Record<string, never>;
  SKILL_VALIDATION_FAILED: { file: string; reason: string };
  SKILL_NOT_FOUND: { name: string; version: string };
  PDF_INVALID: Record<string, never>;
  PDF_PASSWORD_PROTECTED: Record<string, never>;
  PDF_TOO_MANY_PAGES: { limit: number; actual: number };
  PDF_NO_TEXT_EXTRACTABLE: Record<string, never>;
  INTELLIGENCE_UNAVAILABLE: Record<string, never>;
  STRUCTURED_OUTPUT_FAILED: Record<string, never>;
  INTELLIGENCE_TIMEOUT: { budget_seconds: number };
  EXTRACTION_BUDGET_EXCEEDED: { budget_seconds: number };
  PDF_TOO_LARGE: { max_bytes: number; actual_bytes: number };
  PDF_PARSER_UNAVAILABLE: { dependency: string };
  EXTRACTION_OVERLOADED: { max_concurrent: number };
}

export interface ApiErrorPayload<C extends ErrorCode = ErrorCode> {
  code: C;
  params: ErrorParamsByCode[C];
  details: Array<{ field: string; reason: string }> | null;
  request_id: string;
}

export const ERROR_CODES: readonly ErrorCode[] = ["NOT_FOUND", "VALIDATION_FAILED", "INTERNAL_ERROR", "SKILL_VALIDATION_FAILED", "SKILL_NOT_FOUND", "PDF_INVALID", "PDF_PASSWORD_PROTECTED", "PDF_TOO_MANY_PAGES", "PDF_NO_TEXT_EXTRACTABLE", "INTELLIGENCE_UNAVAILABLE", "STRUCTURED_OUTPUT_FAILED", "INTELLIGENCE_TIMEOUT", "EXTRACTION_BUDGET_EXCEEDED", "PDF_TOO_LARGE", "PDF_PARSER_UNAVAILABLE", "EXTRACTION_OVERLOADED"] as const;

export const HTTP_STATUS_BY_CODE: Record<ErrorCode, number> = {
  NOT_FOUND: 404,
  VALIDATION_FAILED: 422,
  INTERNAL_ERROR: 500,
  SKILL_VALIDATION_FAILED: 500,
  SKILL_NOT_FOUND: 404,
  PDF_INVALID: 400,
  PDF_PASSWORD_PROTECTED: 400,
  PDF_TOO_MANY_PAGES: 413,
  PDF_NO_TEXT_EXTRACTABLE: 422,
  INTELLIGENCE_UNAVAILABLE: 503,
  STRUCTURED_OUTPUT_FAILED: 502,
  INTELLIGENCE_TIMEOUT: 504,
  EXTRACTION_BUDGET_EXCEEDED: 504,
  PDF_TOO_LARGE: 413,
  PDF_PARSER_UNAVAILABLE: 500,
  EXTRACTION_OVERLOADED: 503,
};
