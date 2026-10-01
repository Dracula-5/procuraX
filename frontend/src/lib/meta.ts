import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export type Meta = { version: string; environment: string; demo_mode: boolean; public_demo: boolean; registration_enabled: boolean };

/** Public deployment facts; retried because a free-tier API may be waking from sleep. */
export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: () => api.get<Meta>("/meta"), retry: 6, retryDelay: 5000, staleTime: 300_000 });
}
