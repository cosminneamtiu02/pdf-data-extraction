import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/shared/lib/api-client";
import { renderWithProviders, setTestLanguage } from "@/test-utils";
import { ErrorMessage } from "./error-message";

describe("ErrorMessage", () => {
  it("renders localized text with params (EN)", async () => {
    await setTestLanguage("en");
    const error = new ApiError(
      {
        code: "WIDGET_NOT_FOUND",
        params: { widget_id: "abc-123" },
        details: null,
        request_id: "req-1",
      },
      404,
    );

    renderWithProviders(<ErrorMessage error={error} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Widget 'abc-123' was not found.");
  });

  it("renders localized text in Romanian", async () => {
    await setTestLanguage("ro");
    const error = new ApiError(
      {
        code: "WIDGET_NOT_FOUND",
        params: { widget_id: "xyz-789" },
        details: null,
        request_id: "req-2",
      },
      404,
    );

    renderWithProviders(<ErrorMessage error={error} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Widget-ul 'xyz-789' nu a fost gasit.");
    // Reset to English for other tests
    await setTestLanguage("en");
  });

  it("renders request ID", async () => {
    await setTestLanguage("en");
    const error = new ApiError(
      {
        code: "INTERNAL_ERROR",
        params: {},
        details: null,
        request_id: "trace-abc",
      },
      500,
    );

    renderWithProviders(<ErrorMessage error={error} />);

    expect(screen.getByText(/trace-abc/)).toBeInTheDocument();
  });
});
