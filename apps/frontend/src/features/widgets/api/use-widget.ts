import type { components } from "@repo/api-client";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/lib/api-client";

type WidgetRead = components["schemas"]["WidgetRead"];

export function useWidget(id: string) {
  return useQuery({
    queryKey: ["widgets", id],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/widgets/{widget_id}", {
        params: { path: { widget_id: id } },
      });
      return data as WidgetRead;
    },
    enabled: !!id,
  });
}
