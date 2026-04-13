/**
 * API client wrapper — the single HTTP client for the entire frontend.
 * All API calls go through TanStack Query hooks that use this client.
 *
 * Uses openapi-fetch for compile-time type safety against the generated schema.
 * Never use fetch() outside this file.
 */

import type { paths } from "@repo/api-client";
import createClient, { type Middleware } from "openapi-fetch";
import type {
  ApiErrorPayload,
  ApiErrorResponse,
  ErrorCode,
  ErrorDetail,
} from "@/shared/types/api-error";

export class ApiError extends Error {
  readonly code: ErrorCode;
  readonly params: Record<string, string | number | boolean>;
  readonly details: ErrorDetail[] | null;
  readonly requestId: string;
  readonly httpStatus: number;

  constructor(payload: ApiErrorPayload, httpStatus: number) {
    super(`${payload.code}: ${JSON.stringify(payload.params)}`);
    this.name = "ApiError";
    this.code = payload.code;
    this.params = payload.params;
    this.details = payload.details;
    this.requestId = payload.request_id;
    this.httpStatus = httpStatus;
  }

  is(code: ErrorCode): boolean {
    return this.code === code;
  }
}

/**
 * Network-level error — wraps fetch() failures (DNS, offline, CORS preflight).
 * Distinct from ApiError (which represents a parsed error response from the server).
 */
export class NetworkError extends Error {
  constructor(
    message: string,
    public readonly cause?: unknown,
  ) {
    super(message);
    this.name = "NetworkError";
  }
}

/**
 * Process a fetch Response and throw ApiError on non-2xx.
 * Exported for testing.
 */
export async function handleResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  let body: ApiErrorResponse;
  try {
    body = (await response.json()) as ApiErrorResponse;
  } catch {
    throw new NetworkError(`Server returned ${response.status} with non-JSON body`);
  }
  throw new ApiError(body.error, response.status);
}

/**
 * Middleware that adds X-Request-ID and throws ApiError/NetworkError.
 */
const errorMiddleware: Middleware = {
  async onRequest({ request }) {
    request.headers.set("X-Request-ID", crypto.randomUUID());
    return request;
  },
  async onResponse({ response }) {
    if (!response.ok) {
      let body: ApiErrorResponse;
      try {
        body = (await response.clone().json()) as ApiErrorResponse;
      } catch {
        throw new NetworkError(`Server returned ${response.status} with non-JSON body`);
      }
      throw new ApiError(body.error, response.status);
    }
    return response;
  },
  onError({ error }) {
    throw new NetworkError(
      `Network request failed: ${error instanceof Error ? error.message : String(error)}`,
      error,
    );
  },
};

/**
 * Type-safe API client. All hooks should use this instead of raw fetch().
 * Paths, params, and response types are validated at compile time.
 */
export const apiClient = createClient<paths>();
apiClient.use(errorMiddleware);
