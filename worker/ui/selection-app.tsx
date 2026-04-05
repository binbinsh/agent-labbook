import { startTransition, useDeferredValue, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ChevronDown,
  ChevronRight,
  Database,
  FileText,
  LoaderCircle,
  RefreshCw,
  Search,
} from "lucide-react";

import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Checkbox } from "./components/ui/checkbox";
import { Input } from "./components/ui/input";
import { cn } from "./lib/utils";

type Resource = {
  resource_id: string;
  resource_type: string;
  resource_url: string | null;
  title: string;
  parent_type: string | null;
  parent_id: string | null;
  parent_database_id: string | null;
  icon_emoji: string | null;
  last_edited_time: string | null;
  discovered_parent_id: string | null;
  discovered_root_id: string | null;
  discovered_depth: number | null;
};

type BundledResource = Resource & {
  selection_scope: "subtree";
  selected_via: "explicit" | "descendant";
  inherited_from: string | null;
};

type SelectionState = {
  mode: "local_browser" | "headless";
  session_id: string;
  return_to: string | null;
  project_name: string | null;
  page_limit?: number | null;
};

type SelectionConfig = {
  baseUrl: string;
  state: SelectionState;
  oauthSession: string;
  workspaceName: string | null;
  resources: Resource[];
  catalogLoaded: boolean;
  catalogError?: string | null;
};

const LOCAL_HANDOFF_SUCCESS_MESSAGE = "agent-labbook-local-handoff-success";
const LOCAL_HANDOFF_WINDOW_NAME = "agent_labbook_local_handoff";
const LOCAL_HANDOFF_WAIT_TIMEOUT_MS = 10000;

declare global {
  interface Window {
    __AGENT_LABBOOK_SELECTION__?: SelectionConfig;
  }
}

function normalizeNotionIdLike(value: string | null | undefined) {
  const raw = String(value || "").trim();
  let candidateSource = raw;
  try {
    const parsedUrl = new URL(raw);
    if (parsedUrl.pathname) {
      candidateSource = parsedUrl.pathname;
    }
  } catch {}
  const matches = candidateSource.match(
    /[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g,
  );
  const candidate = matches?.length ? matches[matches.length - 1] : raw;
  const collapsed = String(candidate || "")
    .replace(/-/g, "")
    .toLowerCase();
  if (/^[0-9a-f]{32}$/.test(collapsed)) {
    return [
      collapsed.slice(0, 8),
      collapsed.slice(8, 12),
      collapsed.slice(12, 16),
      collapsed.slice(16, 20),
      collapsed.slice(20),
    ].join("-");
  }
  return raw;
}

function normalizeResourceType(value: string | null | undefined) {
  const resourceType = String(value || "").trim().toLowerCase();
  if (resourceType === "database") {
    return "data_source";
  }
  return resourceType || "unknown";
}

function canResourceHaveChildren(resourceType: string | null | undefined) {
  const normalizedType = normalizeResourceType(resourceType);
  return normalizedType === "page" || normalizedType === "data_source";
}

function resourceTypeRank(type: string) {
  if (type === "page") {
    return 0;
  }
  if (type === "data_source") {
    return 1;
  }
  return 2;
}

function compareResources(left: Resource, right: Resource) {
  const typeRank = resourceTypeRank(left.resource_type) - resourceTypeRank(right.resource_type);
  if (typeRank !== 0) {
    return typeRank;
  }
  const titleCompare = String(left.title || "").localeCompare(String(right.title || ""), undefined, {
    sensitivity: "base",
  });
  if (titleCompare !== 0) {
    return titleCompare;
  }
  return String(left.resource_id || "").localeCompare(String(right.resource_id || ""));
}

function dedupeSortResources(items: Resource[]) {
  const byId = new Map<string, Resource>();
  for (const item of items) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const resourceId = normalizeNotionIdLike(item.resource_id);
    if (!resourceId) {
      continue;
    }
    const normalizedItem: Resource = {
      ...item,
      resource_id: resourceId,
      resource_type: normalizeResourceType(item.resource_type),
      parent_id: normalizeNotionIdLike(item.parent_id),
      parent_database_id: normalizeNotionIdLike(item.parent_database_id),
      discovered_parent_id: normalizeNotionIdLike(item.discovered_parent_id),
      discovered_root_id: normalizeNotionIdLike(item.discovered_root_id),
    };
    byId.set(resourceId, {
      ...(byId.get(resourceId) || normalizedItem),
      ...normalizedItem,
    });
  }
  return Array.from(byId.values()).sort(compareResources);
}

function formatDate(value: string | null) {
  if (!value) {
    return null;
  }
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
    }).format(new Date(value));
  } catch {
    return null;
  }
}

function formatTypeLabel(type: string) {
  if (type === "data_source") {
    return "Data source";
  }
  if (type === "page") {
    return "Page";
  }
  return String(type || "resource").replace(/_/g, " ");
}

function matchesQuery(resource: Resource, query: string) {
  if (!query) {
    return true;
  }
  const haystacks = [
    resource.title,
    resource.resource_id,
    resource.resource_type,
    resource.parent_id,
    resource.parent_type,
    resource.parent_database_id,
    resource.resource_url,
  ];
  return haystacks.some((value) => String(value || "").toLowerCase().includes(query));
}

function copySet(nextValues?: Iterable<string>) {
  return new Set(nextValues ? Array.from(nextValues) : []);
}

function copyMap<T>(entries?: Iterable<[string, T]>) {
  return new Map(entries ? Array.from(entries) : []);
}

function resolvedParentId(resource: Resource) {
  return normalizeNotionIdLike(resource.discovered_parent_id || resource.parent_id || resource.parent_database_id);
}

function buildChildIndex(items: Resource[]) {
  const byId = new Map(items.map((item) => [item.resource_id, item]));
  const index = new Map<string, Resource[]>();
  for (const item of items) {
    const parentId = resolvedParentId(item);
    if (!parentId || !byId.has(parentId)) {
      continue;
    }
    const next = index.get(parentId) || [];
    next.push(item);
    index.set(parentId, next);
  }
  for (const [key, value] of index.entries()) {
    index.set(key, [...value].sort(compareResources));
  }
  return index;
}

function resourceIcon(resource: Resource) {
  if (resource.icon_emoji) {
    return <span className="text-[13px] leading-none">{resource.icon_emoji}</span>;
  }
  if (resource.resource_type === "data_source") {
    return <Database className="size-3.5 text-stone-500" />;
  }
  return <FileText className="size-3.5 text-stone-500" />;
}

function ResourceRow({
  resource,
  selectedState,
  loading,
  includesSubtree,
  canExpand,
  expanded,
  depth = 0,
  disabled = false,
  onToggle,
  onToggleExpand,
}: {
  resource: Resource;
  selectedState: "none" | "explicit" | "descendant";
  loading: boolean;
  includesSubtree: boolean;
  canExpand: boolean;
  expanded: boolean;
  depth?: number;
  disabled?: boolean;
  onToggle?: (resource: Resource, checked: boolean) => Promise<void> | void;
  onToggleExpand?: (resource: Resource) => Promise<void> | void;
}) {
  const selected = selectedState !== "none";
  const edited = formatDate(resource.last_edited_time);
  const details = [
    resource.title || "Untitled",
    formatTypeLabel(resource.resource_type),
    resource.resource_id,
    edited ? `Edited ${edited}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <div
      data-resource-id={resource.resource_id}
      data-selected-state={selectedState}
      className={cn(
        "flex min-h-9 items-center gap-2 rounded-lg px-2 py-1.5 transition",
        selectedState === "explicit"
          ? "bg-stone-100"
          : selectedState === "descendant"
            ? "bg-stone-50"
            : "hover:bg-stone-50",
      )}
      style={{ paddingLeft: `${8 + depth * 18}px` }}
      title={details}
    >
      {canExpand ? (
        <button
          type="button"
          className="flex size-4 shrink-0 items-center justify-center rounded text-stone-500 hover:bg-stone-200/60 hover:text-stone-700"
          aria-label={expanded ? "Collapse nested items" : "Expand nested items"}
          onClick={() => void onToggleExpand?.(resource)}
        >
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </button>
      ) : (
        <span className="size-4 shrink-0" aria-hidden="true" />
      )}

      <Checkbox
        checked={selected}
        disabled={disabled}
        onCheckedChange={(nextChecked) => {
          if (!onToggle || disabled) {
            return;
          }
          void onToggle(resource, nextChecked === true);
        }}
      />

      <span className="flex size-4 shrink-0 items-center justify-center">{resourceIcon(resource)}</span>

      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-[13px] text-stone-900">{resource.title || "Untitled"}</span>
          <Badge variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px] text-stone-500">
            {resource.resource_type === "data_source" ? "Data source" : "Page"}
          </Badge>
          {selectedState === "explicit" ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px]">
              Selected
            </Badge>
          ) : null}
          {selectedState === "explicit" && includesSubtree ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px]">
              Includes subtree
            </Badge>
          ) : null}
          {selectedState === "descendant" ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px]">
              In selected subtree
            </Badge>
          ) : null}
        </div>
      </div>

      {loading ? <LoaderCircle className="size-3.5 shrink-0 animate-spin text-stone-500" /> : null}
    </div>
  );
}

function SelectionApp({
  baseUrl,
  state,
  oauthSession,
  workspaceName,
  resources,
  catalogLoaded: initialCatalogLoaded,
  catalogError: initialCatalogError,
}: SelectionConfig) {
  const [catalog, setCatalog] = useState<Resource[]>(dedupeSortResources(resources));
  const rootIndex = new Map(catalog.map((resource) => [resource.resource_id, resource]));

  const [selectedRootIds, setSelectedRootIds] = useState<Set<string>>(new Set());
  const [discoveredByRoot, setDiscoveredByRoot] = useState<Map<string, Resource[]>>(new Map());
  const [loadingRootIds, setLoadingRootIds] = useState<Set<string>>(new Set());
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());
  const [inputValue, setInputValue] = useState("");
  const [handoffBundle, setHandoffBundle] = useState("");
  const [refreshingCatalog, setRefreshingCatalog] = useState(false);
  const [searchingCatalog, setSearchingCatalog] = useState(false);
  const [remoteSearchIds, setRemoteSearchIds] = useState<Set<string>>(new Set());
  const [catalogLoaded, setCatalogLoaded] = useState(Boolean(initialCatalogLoaded || initialCatalogError));
  const [catalogError, setCatalogError] = useState<string | null>(initialCatalogError || null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [localDeliveryStatus, setLocalDeliveryStatus] = useState<"idle" | "delivering" | "delivered" | "fallback">(
    "idle",
  );
  const outputRef = useRef<HTMLDivElement | null>(null);

  const deferredQuery = useDeferredValue(inputValue.trim().toLowerCase());

  function buildSessionPayload(extra: Record<string, unknown> = {}) {
    return {
      ...extra,
      oauth_session: oauthSession,
    };
  }

  function getRootResource(resourceId: string) {
    return rootIndex.get(normalizeNotionIdLike(resourceId));
  }

  async function ensureChildrenForRoot(rootId: string, resourceType: string = "page") {
    const normalizedId = normalizeNotionIdLike(rootId);
    const normalizedType = normalizeResourceType(resourceType);
    if (
      !normalizedId ||
      (normalizedType !== "page" && normalizedType !== "data_source") ||
      discoveredByRoot.has(normalizedId) ||
      loadingRootIds.has(normalizedId)
    ) {
      return;
    }

    setLoadingRootIds((current) => {
      const next = copySet(current);
      next.add(normalizedId);
      return next;
    });

    try {
      const response = await fetch(`${baseUrl}/api/discover-children`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(
          normalizedType === "page"
            ? buildSessionPayload({
                page_ids: [normalizedId],
              })
            : buildSessionPayload({
                data_source_ids: [normalizedId],
              }),
        ),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      const descendants = dedupeSortResources(Array.isArray(payload.resources) ? payload.resources : []);
      setCatalog((current) => dedupeSortResources([...current, ...descendants]));
      setDiscoveredByRoot((current) => {
        const next = copyMap(current.entries());
        next.set(normalizedId, descendants);
        return next;
      });
    } catch {
      setDiscoveredByRoot((current) => {
        const next = copyMap(current.entries());
        next.set(normalizedId, []);
        return next;
      });
    } finally {
      setLoadingRootIds((current) => {
        const next = copySet(current);
        next.delete(normalizedId);
        return next;
      });
    }
  }

  async function toggleResourceSelection(resource: Resource, nextChecked: boolean) {
    const resourceId = normalizeNotionIdLike(resource.resource_id);
    if (!resourceId) {
      return;
    }

    if (!nextChecked) {
      setSelectedRootIds((current) => {
        const next = copySet(current);
        next.delete(resourceId);
        return next;
      });
      return;
    }

    setSelectedRootIds((current) => {
      const next = copySet(current);
      next.add(resourceId);
      return next;
    });
  }

  function bundleResources() {
    const orderedIds: string[] = [];
    const byId = new Map<string, BundledResource>();

    const upsert = (
      resource: Resource,
      meta: {
        selection_scope: "subtree";
        selected_via: "explicit" | "descendant";
        inherited_from: string | null;
      },
    ) => {
      const resourceId = String(resource.resource_id || "").trim();
      if (!resourceId) {
        return;
      }
      const existing = byId.get(resourceId);
      if (existing && existing.selected_via === "explicit" && meta.selected_via !== "explicit") {
        return;
      }
      if (!existing) {
        orderedIds.push(resourceId);
      }
      byId.set(resourceId, {
        ...(existing || resource),
        ...resource,
        ...meta,
      });
    };

    for (const rootId of selectedRootIds) {
      const rootResource = getRootResource(rootId);
      if (!rootResource) {
        continue;
      }
      upsert(rootResource, {
        selection_scope: "subtree",
        selected_via: "explicit",
        inherited_from: null,
      });
    }

    return orderedIds.map((resourceId) => byId.get(resourceId)).filter(Boolean) as BundledResource[];
  }

  const finalBundle = bundleResources();
  const pageLimit = Number.isFinite(Number(state.page_limit)) ? Number(state.page_limit) : 200;
  const workspaceLabel = workspaceName || "your workspace";
  const projectLabel = state.project_name || "this project";
  const title = "Choose Notion Content";
  const titleLine = `Pick the pages and data sources from ${workspaceLabel} that ${projectLabel} should be allowed to use.`;
  const catalogNotice =
    "Search queries run against Notion search for the shared workspace. If something is missing, share it with the integration in Notion first, then click Refresh.";

  const childIndex = buildChildIndex(catalog);
  const parentIndex = new Map(catalog.map((resource) => [resource.resource_id, resolvedParentId(resource)]));

  function collectDescendants(rootId: string, bucket: Resource[] = []) {
    const children = childIndex.get(rootId) || [];
    for (const child of children) {
      bucket.push(child);
      collectDescendants(child.resource_id, bucket);
    }
    return bucket;
  }

  const autoIncludedIds = new Set<string>();
  for (const rootId of selectedRootIds) {
    for (const child of collectDescendants(rootId, [])) {
      if (!selectedRootIds.has(child.resource_id)) {
        autoIncludedIds.add(child.resource_id);
      }
    }
  }

  const searchActive = Boolean(deferredQuery);
  const visibleIds = new Set<string>(searchActive ? [] : catalog.map((resource) => resource.resource_id));
  for (const resource of catalog) {
    if (
      selectedRootIds.has(resource.resource_id) ||
      autoIncludedIds.has(resource.resource_id) ||
      (searchActive
        ? remoteSearchIds.has(resource.resource_id) || matchesQuery(resource, deferredQuery)
        : matchesQuery(resource, deferredQuery))
    ) {
      visibleIds.add(resource.resource_id);
    }
  }
  for (const resourceId of Array.from(visibleIds)) {
    let currentParentId = parentIndex.get(resourceId);
    while (currentParentId && rootIndex.has(currentParentId)) {
      if (visibleIds.has(currentParentId)) {
        currentParentId = parentIndex.get(currentParentId);
        continue;
      }
      visibleIds.add(currentParentId);
      currentParentId = parentIndex.get(currentParentId);
    }
  }

  const rootResources = [...catalog]
    .filter((resource) => {
      if (!visibleIds.has(resource.resource_id)) {
        return false;
      }
      const parentId = parentIndex.get(resource.resource_id);
      return !parentId || !rootIndex.has(parentId) || !visibleIds.has(parentId);
    })
    .sort((left, right) => {
      const leftSelected = selectedRootIds.has(left.resource_id) ? 2 : autoIncludedIds.has(left.resource_id) ? 1 : 0;
      const rightSelected = selectedRootIds.has(right.resource_id) ? 2 : autoIncludedIds.has(right.resource_id) ? 1 : 0;
      if (leftSelected !== rightSelected) {
        return rightSelected - leftSelected;
      }
      return compareResources(left, right);
    });

  const visibleRows: Array<{ resource: Resource; depth: number }> = [];
  function isExpanded(resourceId: string) {
    const hasKnownChildren = Boolean((childIndex.get(resourceId) || []).length);
    if (!hasKnownChildren && !discoveredByRoot.has(resourceId)) {
      return false;
    }
    if (searchActive) {
      return true;
    }
    return !collapsedIds.has(resourceId);
  }
  function appendVisibleRows(resource: Resource, depth: number) {
    if (!visibleIds.has(resource.resource_id)) {
      return;
    }
    visibleRows.push({ resource, depth });
    if (isExpanded(resource.resource_id)) {
      for (const child of childIndex.get(resource.resource_id) || []) {
        appendVisibleRows(child, depth + 1);
      }
    }
  }
  for (const resource of rootResources) {
    appendVisibleRows(resource, 0);
  }

  async function refreshCatalog() {
    setRefreshingCatalog(true);
    setCatalogError(null);
    try {
      const response = await fetch(`${baseUrl}/api/catalog`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(
          buildSessionPayload({
            page_limit: pageLimit,
          }),
        ),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      const selectedResources = Array.from(selectedRootIds)
        .map((resourceId) => getRootResource(resourceId))
        .filter(Boolean) as Resource[];
      const discoveredResources = Array.from(discoveredByRoot.values()).flat();

      setCatalog(
        dedupeSortResources([
          ...(Array.isArray(payload.resources) ? payload.resources : []),
          ...selectedResources,
          ...discoveredResources,
        ]),
      );
      setCatalogLoaded(true);
      setCatalogError(null);
    } catch (error) {
      setCatalogError(error instanceof Error ? error.message : String(error));
    } finally {
      setRefreshingCatalog(false);
    }
  }

  useEffect(() => {
    if (!catalogLoaded) {
      void refreshCatalog();
    }
  }, [catalogLoaded]);

  useEffect(() => {
    if (!deferredQuery) {
      setRemoteSearchIds(new Set());
      setSearchingCatalog(false);
      setSearchError(null);
      return;
    }

    setRemoteSearchIds(new Set());
    setSearchingCatalog(true);
    setSearchError(null);

    let cancelled = false;
    const timeoutId = window.setTimeout(async () => {
      try {
        const response = await fetch(`${baseUrl}/api/search`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify(
            buildSessionPayload({
              query: deferredQuery,
            }),
          ),
        });
        const payload = await response.json();
        if (cancelled) {
          return;
        }
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || `HTTP ${response.status}`);
        }

        const results = dedupeSortResources(Array.isArray(payload.resources) ? payload.resources : []);
        setCatalog((current) => dedupeSortResources([...current, ...results]));
        setRemoteSearchIds(new Set(results.map((resource) => resource.resource_id)));
        setSearchError(null);
      } catch (error) {
        if (!cancelled) {
          console.error("Remote Notion search failed", error);
          setRemoteSearchIds(new Set());
          setSearchError(error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!cancelled) {
          setSearchingCatalog(false);
        }
      }
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [baseUrl, deferredQuery, oauthSession]);

  async function toggleCollapsed(resource: Resource) {
    const resourceId = normalizeNotionIdLike(resource.resource_id);
    if (!resourceId) {
      return;
    }

    if (isExpanded(resourceId)) {
      setCollapsedIds((current) => {
        const next = copySet(current);
        next.add(resourceId);
        return next;
      });
      return;
    }

    setCollapsedIds((current) => {
      const next = copySet(current);
      next.delete(resourceId);
      return next;
    });

    if (canResourceHaveChildren(resource.resource_type) && !discoveredByRoot.has(resourceId)) {
      await ensureChildrenForRoot(resourceId, resource.resource_type);
    }
  }

  async function requestHandoffBundle(chosen: BundledResource[]) {
    const response = await fetch(`${baseUrl}/api/finalize-selection`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(
        buildSessionPayload({
          selected_resources: chosen,
        }),
      ),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    const handoffBundle = String(payload.handoff_bundle || "").trim();
    if (!handoffBundle) {
      throw new Error("Worker did not return a handoff bundle.");
    }
    return handoffBundle;
  }

  function openLocalDeliveryWindow() {
    if (!state.return_to) {
      return null;
    }
    try {
      const popup = window.open("", LOCAL_HANDOFF_WINDOW_NAME, "popup=yes,width=480,height=720");
      if (!popup) {
        return null;
      }
      popup.document.write(
        "<!doctype html><html><body><p style=\"font-family: sans-serif; padding: 24px;\">Connecting Agent Labbook to the local MCP listener...</p></body></html>",
      );
      popup.document.close();
      return popup;
    } catch {
      return null;
    }
  }

  function submitToLocalhostWindow(popup: Window, bundle: string) {
    if (!state.return_to) {
      return false;
    }
    try {
      const doc = popup.document;
      doc.open();
      doc.write("<!doctype html><html><body></body></html>");
      doc.close();

      const form = doc.createElement("form");
      form.method = "POST";
      form.action = String(state.return_to);
      form.target = "_self";

      const sessionInput = doc.createElement("input");
      sessionInput.type = "hidden";
      sessionInput.name = "session_id";
      sessionInput.value = state.session_id;

      const bundleInput = doc.createElement("input");
      bundleInput.type = "hidden";
      bundleInput.name = "handoff_bundle";
      bundleInput.value = bundle;

      form.appendChild(sessionInput);
      form.appendChild(bundleInput);
      doc.body.appendChild(form);
      form.submit();
      return true;
    } catch {
      return false;
    }
  }

  function waitForLocalDeliveryConfirmation() {
    if (!state.return_to) {
      return Promise.resolve(false);
    }

    const expectedOrigin = new URL(String(state.return_to)).origin;
    return new Promise<boolean>((resolve) => {
      let settled = false;
      const timeoutId = window.setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        window.removeEventListener("message", handleMessage);
        resolve(false);
      }, LOCAL_HANDOFF_WAIT_TIMEOUT_MS);

      function handleMessage(event: MessageEvent) {
        if (event.origin !== expectedOrigin) {
          return;
        }
        const payload = event.data;
        if (!(payload && typeof payload === "object")) {
          return;
        }
        if (
          payload.type !== LOCAL_HANDOFF_SUCCESS_MESSAGE ||
          String(payload.session_id || "") !== String(state.session_id || "")
        ) {
          return;
        }
        if (settled) {
          return;
        }
        settled = true;
        window.clearTimeout(timeoutId);
        window.removeEventListener("message", handleMessage);
        resolve(true);
      }

      window.addEventListener("message", handleMessage);
    });
  }

  async function finishBinding() {
    if (!finalBundle.length) {
      return;
    }
    setLocalDeliveryStatus("idle");
    setHandoffBundle("");

    const localWindow = state.mode === "local_browser" ? openLocalDeliveryWindow() : null;
    let bundle = "";
    try {
      bundle = await requestHandoffBundle(finalBundle);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : String(error));
      return;
    }
    if (state.mode === "local_browser") {
      setLocalDeliveryStatus("delivering");
      const submitted = localWindow ? submitToLocalhostWindow(localWindow, bundle) : false;
      const delivered = submitted ? await waitForLocalDeliveryConfirmation() : false;
      if (delivered) {
        setLocalDeliveryStatus("delivered");
        return;
      }
      setLocalDeliveryStatus("fallback");
    }
    setHandoffBundle(bundle);
    window.setTimeout(() => {
      outputRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }

  async function copyBundle() {
    if (!handoffBundle) {
      return;
    }
    await navigator.clipboard.writeText(handoffBundle);
  }

  return (
    <div className="mx-auto min-h-screen w-full max-w-3xl px-3 py-6 sm:px-5 sm:py-8">
      <div className="space-y-4 pb-28">
        <div className="space-y-1">
          <h1 className="max-w-2xl text-xl font-semibold tracking-tight text-stone-950 sm:text-2xl">
            {title}
          </h1>
          <p className="text-sm text-stone-500">{titleLine}</p>
        </div>

        <Card className="border-stone-200 shadow-sm">
          <CardHeader className="gap-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1">
                <CardTitle className="text-base">Select Pages and Data Sources</CardTitle>
                <CardDescription>
                  Selecting a root binds that page or data source with subtree scope. Expand rows to inspect nested content on demand.
                </CardDescription>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void refreshCatalog()}
                disabled={refreshingCatalog}
              >
                <RefreshCw className={cn("size-3.5", refreshingCatalog && "animate-spin")} />
                Refresh
              </Button>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-stone-400" />
                <Input
                  value={inputValue}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    startTransition(() => {
                      setInputValue(nextValue);
                    });
                  }}
                  className="pl-9"
                  placeholder="Search workspace by title, type, or ID"
                />
              </div>
            </div>
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900">
              {catalogNotice}
            </div>
            {catalogError ? (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-900">
                Could not load available resources from Notion. {catalogError}
              </div>
            ) : null}
            {searchError && searchActive ? (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-900">
                Search could not reach Notion. {searchError}
              </div>
            ) : null}
          </CardHeader>
          <CardContent>
            {!catalogLoaded && refreshingCatalog ? (
              <div className="rounded-xl border border-dashed border-stone-200 bg-stone-50 px-4 py-10 text-center text-sm text-stone-500">
                Loading available pages and data sources...
              </div>
            ) : visibleRows.length ? (
              <div className="space-y-2">
                {visibleRows.map(({ resource, depth }) => {
                  const selectedState = selectedRootIds.has(resource.resource_id)
                    ? "explicit"
                    : autoIncludedIds.has(resource.resource_id)
                      ? "descendant"
                      : "none";
                  const hasChildren = Boolean((childIndex.get(resource.resource_id) || []).length);
                  const canExpand =
                    hasChildren ||
                    loadingRootIds.has(resource.resource_id) ||
                    (canResourceHaveChildren(resource.resource_type) && !discoveredByRoot.has(resource.resource_id));
                  return (
                    <ResourceRow
                      key={resource.resource_id}
                      resource={resource}
                      selectedState={selectedState}
                      loading={loadingRootIds.has(resource.resource_id)}
                      includesSubtree={selectedState === "explicit" && canResourceHaveChildren(resource.resource_type)}
                      canExpand={canExpand}
                      expanded={isExpanded(resource.resource_id)}
                      depth={depth}
                      disabled={selectedState === "descendant"}
                      onToggle={toggleResourceSelection}
                      onToggleExpand={toggleCollapsed}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-stone-200 bg-stone-50 px-4 py-10 text-center text-sm text-stone-500">
                No pages or data sources matched this filter.
              </div>
            )}
          </CardContent>
        </Card>

        {handoffBundle ? (
          <div ref={outputRef}>
            <Card className="border-stone-200 shadow-sm">
              <CardHeader>
                <CardTitle className="text-base">Complete Setup in Codex</CardTitle>
                <CardDescription>
                  Paste this value into <code>notion_complete_headless_auth</code> to finish connecting this project.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600">
                  {localDeliveryStatus === "fallback"
                    ? "The local browser handoff could not reach the MCP server on 127.0.0.1. The handoff bundle is shown below so you can finish with notion_complete_headless_auth."
                    : "The handoff bundle is ready. Copy it and paste it into notion_complete_headless_auth."}
                </div>
                <textarea
                  readOnly
                  value={handoffBundle}
                  className="min-h-56 w-full rounded-xl border border-stone-200 bg-white px-3 py-3 font-mono text-xs text-stone-700 outline-none"
                />
                <Button variant="secondary" onClick={() => void copyBundle()}>
                  Copy Bundle
                </Button>
              </CardContent>
            </Card>
          </div>
        ) : null}
      </div>

      <div className="sticky bottom-3 z-20">
        <Card className="border-stone-200 bg-white/95 shadow-lg backdrop-blur">
          <CardContent className="flex items-center justify-between gap-3 pt-5">
            <div className="min-w-0">
              <p className="text-sm text-stone-500">
                {searchingCatalog
                  ? "Searching Notion..."
                  : localDeliveryStatus === "delivering"
                    ? "Trying to deliver the handoff through a local browser window..."
                  : localDeliveryStatus === "delivered"
                    ? "The handoff was sent back to the local MCP server. You can close this tab."
                  : loadingRootIds.size
                  ? "Loading nested content..."
                  : handoffBundle
                    ? "The handoff bundle is ready below."
                  : `${finalBundle.length} root${finalBundle.length === 1 ? "" : "s"} selected`}
              </p>
            </div>
            <Button disabled={!finalBundle.length} onClick={() => void finishBinding()}>
              Connect Selected
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

const config = window.__AGENT_LABBOOK_SELECTION__;
const rootElement = document.getElementById("selection-app-root");

if (!config || !rootElement) {
  throw new Error("Selection app bootstrap data is missing.");
}

createRoot(rootElement).render(<SelectionApp {...config} />);
