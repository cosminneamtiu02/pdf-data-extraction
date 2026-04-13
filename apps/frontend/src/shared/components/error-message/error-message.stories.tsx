import type { Meta, StoryObj } from "@storybook/react-vite";
import { ApiError } from "@/shared/lib/api-client";
import { ErrorMessage } from "./error-message";

const meta: Meta<typeof ErrorMessage> = {
  title: "Shared/ErrorMessage",
  component: ErrorMessage,
};

export default meta;
type Story = StoryObj<typeof ErrorMessage>;

export const NotFound: Story = {
  args: {
    error: new ApiError(
      {
        code: "WIDGET_NOT_FOUND",
        params: { widget_id: "abc-123" },
        details: null,
        request_id: "req-demo-1",
      },
      404,
    ),
  },
};

export const InternalError: Story = {
  args: {
    error: new ApiError(
      {
        code: "INTERNAL_ERROR",
        params: {},
        details: null,
        request_id: "req-demo-2",
      },
      500,
    ),
  },
};

export const ValidationFailed: Story = {
  args: {
    error: new ApiError(
      {
        code: "VALIDATION_FAILED",
        params: { field: "name", reason: "Field required" },
        details: [{ field: "name", reason: "Field required" }],
        request_id: "req-demo-3",
      },
      422,
    ),
  },
};
