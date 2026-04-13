import type { components } from "@repo/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/shared/lib/api-client";
import type { WidgetRead } from "./use-widgets";

type WidgetUpdate = components["schemas"]["WidgetUpdate"];

export function useUpdateWidget(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: WidgetUpdate) => {
      const { data: result } = await apiClient.PATCH("/api/v1/widgets/{widget_id}", {
        params: { path: { widget_id: id } },
        body: data,
      });
      return result as WidgetRead;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["widgets"] });
    },
  });
}
