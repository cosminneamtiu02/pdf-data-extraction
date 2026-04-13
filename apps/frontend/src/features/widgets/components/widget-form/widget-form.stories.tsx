import type { Meta, StoryObj } from "@storybook/react-vite";
import { WidgetForm } from "./widget-form";

// TODO: WidgetForm calls useCreateWidget() internally, so Submitting and WithError
// story variants require mocking the TanStack Query mutation hook.
// Options to implement this properly:
// 1. Install MSW and use Storybook's MSW addon to intercept the POST request
//    with delays (Submitting) or error responses (WithError).
// 2. Use Storybook's experimental module mocking (storybook-addon-module-mock).
// 3. Refactor WidgetForm to accept mutation state as props (presentational component)
//    and create a container component that calls useCreateWidget().
//
// For now, Default renders the component with a real (or failing) mutation.
// When MSW is added to the project, replace these with proper story variants:
//   - Submitting: use MSW's `delay("infinite")` on the POST endpoint
//   - WithError: mock POST to return 422 with a validation ApiError payload

const meta: Meta<typeof WidgetForm> = {
  title: "Features/Widgets/WidgetForm",
  component: WidgetForm,
};

export default meta;
type Story = StoryObj<typeof WidgetForm>;

export const Default: Story = {};
