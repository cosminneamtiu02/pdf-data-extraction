/**
 * TanStack Query hook for fetching paginated widget list.
 */
import type { components } from "@repo/api-client";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/lib/api-client";

export type WidgetRead = components["schemas"]["WidgetRead"];
export type WidgetPage = components["schemas"]["Page_WidgetRead_"];

export function useWidgets(page = 1, size = 20) {
  return useQuery({
    queryKey: ["widgets", { page, size }],
    queryFn: async () => {
      const { data } = await apiClient.GET("/api/v1/widgets", {
        params: { query: { page, size } },
      });
      return data as WidgetPage;
    },
  });
}
