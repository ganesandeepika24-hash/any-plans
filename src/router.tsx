import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { parseParams, stringifyParams } from "./lib/filters";

export const getRouter = () => {
  const queryClient = new QueryClient();

  const router = createRouter({
    routeTree,
    context: { queryClient },
    scrollRestoration: true,
    defaultPreloadStaleTime: 0,
    // Plain query strings with repeated keys (w=…&w=…), so windows read cleanly.
    parseSearch: (s) => parseParams(s),
    stringifySearch: (p) => stringifyParams(p),
  });

  return router;
};
