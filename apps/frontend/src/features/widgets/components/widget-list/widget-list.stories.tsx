import type { Meta, StoryObj } from "@storybook/react-vite";
import { WidgetList } from "./widget-list";

// TODO: WidgetList calls useWidgets() internally, so proper story variants
// (Empty, WithWidgets, Loading, Error) require mocking the TanStack Query hook.
// Options to implement this properly:
// 1. Install MSW (Mock Service Worker) and use Storybook's MSW addon to intercept
//    API requests with different responses per story.
// 2. Use Storybook's experimental module mocking (storybook-addon-module-mock).
// 3. Refactor WidgetList to accept data/state as props (presentational component)
//    and create a container component that calls useWidgets().
//
// For now, Default renders the component with a real (or failing) API call.
// When MSW is added to the project, replace these with proper story variants:
//   - Empty: mock API returns { items: [], total: 0, page: 1, size: 20, pages: 0 }
//   - WithWidgets: mock API returns 2 sample widgets
//   - Loading: use MSW's `delay("infinite")` to keep the request pending
//   - Error: mock API returns 500 with an ApiError payload

const meta: Meta<typeof WidgetList> = {
  title: "Features/Widgets/WidgetList",
  component: WidgetList,
};

export default meta;
type Story = StoryObj<typeof WidgetList>;

export const Default: Story = {};
