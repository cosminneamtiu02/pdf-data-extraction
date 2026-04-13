import { describe, expect, it } from "vitest";
import { ApiError, handleResponse, NetworkError } from "./api-client";

describe("ApiError", () => {
  it("constructs from error payload", () => {
    const error = new ApiError(
      {
        code: "WIDGET_NOT_FOUND",
        params: { widget_id: "abc-123" },
        details: null,
        request_id: "req-uuid",
      },
      404,
    );

    expect(error.code).toBe("WIDGET_NOT_FOUND");
    expect(error.params).toEqual({ widget_id: "abc-123" });
    expect(error.requestId).toBe("req-uuid");
    expect(error.httpStatus).toBe(404);
    expect(error.details).toBeNull();
    expect(error).toBeInstanceOf(Error);
  });

  it("narrows params type with is() guard", () => {
    const error = new ApiError(
      {
        code: "WIDGET_NOT_FOUND",
        params: { widget_id: "abc-123" },
        details: null,
        request_id: "req-uuid",
      },
      404,
    );

    if (error.is("WIDGET_NOT_FOUND")) {
      // TypeScript should narrow params here
      expect(error.params.widget_id).toBe("abc-123");
    }

    expect(error.is("WIDGET_NOT_FOUND")).toBe(true);
    expect(error.is("INTERNAL_ERROR")).toBe(false);
  });

  it("throws ApiError on non-2xx response", async () => {
    // Mock fetch to return a 404
    const mockFetch = async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "WIDGET_NOT_FOUND",
            params: { widget_id: "test" },
            details: null,
            request_id: "req-123",
          },
        }),
        {
          status: 404,
          headers: {
            "Content-Type": "application/json",
            "X-Request-ID": "req-123",
          },
        },
      );

    const { handleResponse } = await import("./api-client");
    await expect(handleResponse(await mockFetch())).rejects.toThrow(ApiError);
  });

  it("includes request ID in ApiError", async () => {
    const mockFetch = async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "INTERNAL_ERROR",
            params: {},
            details: null,
            request_id: "traced-uuid",
          },
        }),
        {
          status: 500,
          headers: {
            "Content-Type": "application/json",
            "X-Request-ID": "traced-uuid",
          },
        },
      );

    try {
      await handleResponse(await mockFetch());
      expect.fail("Should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).requestId).toBe("traced-uuid");
    }
  });
});

describe("handleResponse", () => {
  it("returns undefined on 204 No Content", async () => {
    const response = new Response(null, { status: 204 });
    const result = await handleResponse<undefined>(response);
    expect(result).toBeUndefined();
  });

  it("parses JSON body on 2xx", async () => {
    const response = new Response(JSON.stringify({ id: "abc", name: "widget" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const result = await handleResponse<{ id: string; name: string }>(response);
    expect(result).toEqual({ id: "abc", name: "widget" });
  });

  it("throws NetworkError when error response has non-JSON body", async () => {
    const makeResponse = () =>
      new Response("Internal Server Error (plain text)", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      });
    await expect(handleResponse(makeResponse())).rejects.toThrow(NetworkError);
    await expect(handleResponse(makeResponse())).rejects.toThrow(/500.*non-JSON/);
  });
});
