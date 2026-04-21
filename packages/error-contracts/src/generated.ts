// THIS FILE IS GENERATED FROM errors.yaml
// DO NOT EDIT BY HAND. Run `task errors:generate` to regenerate.

export type ErrorCode =
  | "EXTRACTION_BUDGET_EXCEEDED"
  | "EXTRACTION_OVERLOADED"
  | "INTELLIGENCE_TIMEOUT"
  | "INTELLIGENCE_UNAVAILABLE"
  | "INTERNAL_ERROR"
  | "NOT_FOUND"
  | "PDF_INVALID"
  | "PDF_NO_TEXT_EXTRACTABLE"
  | "PDF_PARSER_UNAVAILABLE"
  | "PDF_PASSWORD_PROTECTED"
  | "PDF_TOO_LARGE"
  | "PDF_TOO_MANY_PAGES"
  | "SKILL_NOT_FOUND"
  | "SKILL_VALIDATION_FAILED"
  | "STRUCTURED_OUTPUT_FAILED"
  | "VALIDATION_FAILED";

export interface ErrorParamsByCode {
  EXTRACTION_BUDGET_EXCEEDED: { budget_seconds: number };
  EXTRACTION_OVERLOADED: { max_concurrent: number };
  INTELLIGENCE_TIMEOUT: { budget_seconds: number };
  INTELLIGENCE_UNAVAILABLE: Record<string, never>;
  INTERNAL_ERROR: Record<string, never>;
  NOT_FOUND: Record<string, never>;
  PDF_INVALID: Record<string, never>;
  PDF_NO_TEXT_EXTRACTABLE: Record<string, never>;
  PDF_PARSER_UNAVAILABLE: { dependency: string };
  PDF_PASSWORD_PROTECTED: Record<string, never>;
  PDF_TOO_LARGE: { actual_bytes: number; max_bytes: number };
  PDF_TOO_MANY_PAGES: { actual: number; limit: number };
  SKILL_NOT_FOUND: { name: string; version: string };
  SKILL_VALIDATION_FAILED: { file: string; reason: string };
  STRUCTURED_OUTPUT_FAILED: Record<string, never>;
  VALIDATION_FAILED: Record<string, never>;
}

export interface ApiErrorPayload<C extends ErrorCode = ErrorCode> {
  code: C;
  params: ErrorParamsByCode[C];
  details: Array<{ field: string; reason: string }> | null;
  request_id: string;
}

export const ERROR_CODES: readonly ErrorCode[] = ["EXTRACTION_BUDGET_EXCEEDED", "EXTRACTION_OVERLOADED", "INTELLIGENCE_TIMEOUT", "INTELLIGENCE_UNAVAILABLE", "INTERNAL_ERROR", "NOT_FOUND", "PDF_INVALID", "PDF_NO_TEXT_EXTRACTABLE", "PDF_PARSER_UNAVAILABLE", "PDF_PASSWORD_PROTECTED", "PDF_TOO_LARGE", "PDF_TOO_MANY_PAGES", "SKILL_NOT_FOUND", "SKILL_VALIDATION_FAILED", "STRUCTURED_OUTPUT_FAILED", "VALIDATION_FAILED"] as const;

export const HTTP_STATUS_BY_CODE: Record<ErrorCode, number> = {
  EXTRACTION_BUDGET_EXCEEDED: 504,
  EXTRACTION_OVERLOADED: 503,
  INTELLIGENCE_TIMEOUT: 504,
  INTELLIGENCE_UNAVAILABLE: 503,
  INTERNAL_ERROR: 500,
  NOT_FOUND: 404,
  PDF_INVALID: 400,
  PDF_NO_TEXT_EXTRACTABLE: 422,
  PDF_PARSER_UNAVAILABLE: 500,
  PDF_PASSWORD_PROTECTED: 400,
  PDF_TOO_LARGE: 413,
  PDF_TOO_MANY_PAGES: 413,
  SKILL_NOT_FOUND: 404,
  SKILL_VALIDATION_FAILED: 500,
  STRUCTURED_OUTPUT_FAILED: 502,
  VALIDATION_FAILED: 422,
};
