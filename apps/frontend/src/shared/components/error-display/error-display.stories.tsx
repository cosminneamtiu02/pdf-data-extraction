import type { Meta, StoryObj } from "@storybook/react-vite";
import { ApiError } from "@/shared/lib/api-client";
import { ErrorDisplay } from "./error-display";

const meta: Meta<typeof ErrorDisplay> = {
  title: "Shared/ErrorDisplay",
  component: ErrorDisplay,
};

export default meta;
type Story = StoryObj<typeof ErrorDisplay>;

export const Default: Story = {
  args: {
    error: new ApiError(
      {
        code: "WIDGET_NOT_FOUND",
        params: { widget_id: "abc-123" },
        details: null,
        request_id: "req-demo",
      },
      404,
    ),
  },
};
