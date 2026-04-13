import type { Meta, StoryObj } from "@storybook/react-vite";
import { MoneyDisplay } from "./money-display";

const meta: Meta<typeof MoneyDisplay> = {
  title: "Shared/MoneyDisplay",
  component: MoneyDisplay,
};

export default meta;
type Story = StoryObj<typeof MoneyDisplay>;

export const USD: Story = {
  args: { amountMinor: 1050, currency: "USD" },
};

export const EUR: Story = {
  args: { amountMinor: 2499, currency: "EUR" },
};
