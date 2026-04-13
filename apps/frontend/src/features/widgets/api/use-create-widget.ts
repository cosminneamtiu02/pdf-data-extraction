/**
 * TanStack Query mutation hook for creating a widget.
 */
import type { components } from "@repo/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/shared/lib/api-client";
import type { WidgetRead } from "./use-widgets";

type WidgetCreate = components["schemas"]["WidgetCreate"];

export function useCreateWidget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: WidgetCreate) => {
      const { data: result } = await apiClient.POST("/api/v1/widgets", {
        body: data,
      });
      return result as WidgetRead;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["widgets"] });
    },
  });
}
