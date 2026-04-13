import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/shared/lib/api-client";
import { renderWithProviders, setTestLanguage } from "@/test-utils";
import { WidgetList } from "./widget-list";

// Mock the useWidgets hook
vi.mock("@/features/widgets/api/use-widgets", () => ({
  useWidgets: vi.fn(),
}));

import { useWidgets } from "@/features/widgets/api/use-widgets";

const mockUseWidgets = vi.mocked(useWidgets);

describe("WidgetList", () => {
  it("renders empty state when no widgets", async () => {
    await setTestLanguage("en");
    mockUseWidgets.mockReturnValue({
      data: { items: [], total: 0, page: 1, size: 20, pages: 0 },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useWidgets>);

    renderWithProviders(<WidgetList />);

    expect(screen.getByText("No widgets yet.")).toBeInTheDocument();
  });

  it("renders list of widgets", async () => {
    await setTestLanguage("en");
    mockUseWidgets.mockReturnValue({
      data: {
        items: [
          {
            id: "1",
            name: "Widget A",
            description: "Desc A",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
          {
            id: "2",
            name: "Widget B",
            description: null,
            created_at: "2026-01-02T00:00:00Z",
            updated_at: "2026-01-02T00:00:00Z",
          },
        ],
        total: 2,
        page: 1,
        size: 20,
        pages: 1,
      },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useWidgets>);

    renderWithProviders(<WidgetList />);

    expect(screen.getByText("Widget A")).toBeInTheDocument();
    expect(screen.getByText("Widget B")).toBeInTheDocument();
  });

  it("renders loading state", async () => {
    mockUseWidgets.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useWidgets>);

    renderWithProviders(<WidgetList />);

    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders error state with ErrorMessage", async () => {
    await setTestLanguage("en");
    const error = new ApiError(
      {
        code: "INTERNAL_ERROR",
        params: {},
        details: null,
        request_id: "err-req",
      },
      500,
    );

    mockUseWidgets.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error,
    } as unknown as ReturnType<typeof useWidgets>);

    renderWithProviders(<WidgetList />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
