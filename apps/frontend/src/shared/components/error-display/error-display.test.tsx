import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/shared/lib/api-client";
import { renderWithProviders, setTestLanguage } from "@/test-utils";
import { ErrorDisplay } from "./error-display";

describe("ErrorDisplay", () => {
  it("renders error message with request ID", async () => {
    await setTestLanguage("en");
    const error = new ApiError(
      {
        code: "WIDGET_NOT_FOUND",
        params: { widget_id: "test-id" },
        details: null,
        request_id: "display-req-123",
      },
      404,
    );

    renderWithProviders(<ErrorDisplay error={error} />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/display-req-123/)).toBeInTheDocument();
  });
});
