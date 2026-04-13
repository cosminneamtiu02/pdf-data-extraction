import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, setTestLanguage } from "@/test-utils";
import { WidgetForm } from "./widget-form";

// Mock the useCreateWidget hook
vi.mock("@/features/widgets/api/use-create-widget", () => ({
  useCreateWidget: vi.fn(),
}));

import { useCreateWidget } from "@/features/widgets/api/use-create-widget";

const mockUseCreateWidget = vi.mocked(useCreateWidget);

describe("WidgetForm", () => {
  it("submits valid input", async () => {
    await setTestLanguage("en");
    const mutateFn = vi.fn();
    mockUseCreateWidget.mockReturnValue({
      mutate: mutateFn,
      isPending: false,
      isError: false,
      error: null,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useCreateWidget>);

    const user = userEvent.setup();
    renderWithProviders(<WidgetForm />);

    await user.type(screen.getByLabelText(/name/i), "Test Widget");
    await user.click(screen.getByRole("button", { name: /create/i }));

    expect(mutateFn).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Test Widget" }),
      expect.anything(),
    );
  });

  it("shows validation error on empty name", async () => {
    await setTestLanguage("en");
    const mutateFn = vi.fn();
    mockUseCreateWidget.mockReturnValue({
      mutate: mutateFn,
      isPending: false,
      isError: false,
      error: null,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useCreateWidget>);

    const user = userEvent.setup();
    renderWithProviders(<WidgetForm />);

    // Click submit without filling name
    await user.click(screen.getByRole("button", { name: /create/i }));

    // Mutation should NOT have been called
    expect(mutateFn).not.toHaveBeenCalled();
  });

  it("disables submit while pending", async () => {
    await setTestLanguage("en");
    mockUseCreateWidget.mockReturnValue({
      mutate: vi.fn(),
      isPending: true,
      isError: false,
      error: null,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof useCreateWidget>);

    renderWithProviders(<WidgetForm />);

    expect(screen.getByRole("button", { name: /create/i })).toBeDisabled();
  });
});
