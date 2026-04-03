import { startTransition, useDeferredValue, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
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
  selectionToken: string;
  workspaceName: string | null;
  resources: Resource[];
  truncated: boolean;
};

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
  ];
  return haystacks.some((value) => String(value || "").toLowerCase().includes(query));
}

function copySet(nextValues?: Iterable<string>) {
  return new Set(nextValues ? Array.from(nextValues) : []);
}

function copyMap<T>(entries?: Iterable<[string, T]>) {
  return new Map(entries ? Array.from(entries) : []);
}

function buildDescendantIndex(items: Resource[], rootId: string) {
  const index = new Map<string, Resource[]>();
  for (const item of items) {
    const parentId = normalizeNotionIdLike(item.discovered_parent_id || item.parent_id) || rootId;
    const next = index.get(parentId) || [];
    next.push(item);
    index.set(parentId, next);
  }
  for (const [key, value] of index.entries()) {
    index.set(key, [...value].sort(compareResources));
  }
  return index;
}

function buildChildIndex(items: Resource[]) {
  const byId = new Map(items.map((item) => [item.resource_id, item]));
  const index = new Map<string, Resource[]>();
  for (const item of items) {
    const parentId = normalizeNotionIdLike(item.discovered_parent_id || item.parent_id);
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
  includedCount,
  depth = 0,
  disabled = false,
  onToggle,
}: {
  resource: Resource;
  selectedState: "none" | "explicit" | "descendant";
  loading: boolean;
  includedCount: number;
  depth?: number;
  disabled?: boolean;
  onToggle?: (resource: Resource, checked: boolean) => Promise<void> | void;
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
    <label
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
          {selectedState === "descendant" ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px]">
              Included
            </Badge>
          ) : null}
          {includedCount > 0 ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full px-2 text-[10px]">
              +{includedCount} nested pages
            </Badge>
          ) : null}
        </div>
      </div>

      {loading ? <LoaderCircle className="size-3.5 shrink-0 animate-spin text-stone-500" /> : null}
    </label>
  );
}

function SelectionApp({ baseUrl, state, selectionToken, workspaceName, resources, truncated }: SelectionConfig) {
  const [catalog, setCatalog] = useState<Resource[]>(dedupeSortResources(resources));
  const rootIndex = new Map(catalog.map((resource) => [resource.resource_id, resource]));

  const [selectedRootIds, setSelectedRootIds] = useState<Set<string>>(new Set());
  const [discoveredByRoot, setDiscoveredByRoot] = useState<Map<string, Resource[]>>(new Map());
  const [loadingRootIds, setLoadingRootIds] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [handoffBundle, setHandoffBundle] = useState("");
  const [refreshingCatalog, setRefreshingCatalog] = useState(false);
  const [resolvingManualResource, setResolvingManualResource] = useState(false);
  const [bundleStatus, setBundleStatus] = useState<"idle" | "ready" | "copied">("idle");
  const outputRef = useRef<HTMLDivElement | null>(null);

  const deferredQuery = useDeferredValue(query.trim().toLowerCase());

  function getRootResource(resourceId: string) {
    return rootIndex.get(normalizeNotionIdLike(resourceId));
  }

  async function ensureChildrenForRoot(rootId: string, resourceType: string = "page") {
    const normalizedId = normalizeNotionIdLike(rootId);
    if (
      !normalizedId ||
      normalizeResourceType(resourceType) !== "page" ||
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
        body: JSON.stringify({
          selection_token: selectionToken,
          page_ids: [normalizedId],
        }),
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

    if (resource.resource_type === "page") {
      await ensureChildrenForRoot(resourceId, resource.resource_type);
    }
  }

  function bundleResources() {
    const orderedIds: string[] = [];
    const byId = new Map<string, BundledResource>();

    const upsert = (
      resource: Resource,
      meta: {
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
        selected_via: "explicit",
        inherited_from: null,
      });
      for (const child of collectDescendants(rootId, [])) {
        upsert(child, {
          selected_via: "descendant",
          inherited_from: rootId,
        });
      }
    }

    return orderedIds.map((resourceId) => byId.get(resourceId)).filter(Boolean) as BundledResource[];
  }

  const finalBundle = bundleResources();
  const pageLimit = Number.isFinite(Number(state.page_limit)) ? Number(state.page_limit) : 500;
  const workspaceLabel = workspaceName || "your workspace";
  const projectLabel = state.project_name || "this project";
  const title = "Choose Notion Resources";
  const titleLine = `${workspaceLabel} for ${projectLabel}`;

  const childIndex = buildChildIndex(catalog);

  function collectDescendants(rootId: string, bucket: Resource[] = []) {
    const children = childIndex.get(rootId) || [];
    for (const child of children) {
      bucket.push(child);
      collectDescendants(child.resource_id, bucket);
    }
    return bucket;
  }

  const descendantCountCache = new Map<string, number>();
  function descendantCount(resourceId: string) {
    const cached = descendantCountCache.get(resourceId);
    if (cached !== undefined) {
      return cached;
    }
    const count = collectDescendants(resourceId, []).length;
    descendantCountCache.set(resourceId, count);
    return count;
  }

  const autoIncludedIds = new Set<string>();
  for (const rootId of selectedRootIds) {
    for (const child of collectDescendants(rootId, [])) {
      if (!selectedRootIds.has(child.resource_id)) {
        autoIncludedIds.add(child.resource_id);
      }
    }
  }

  const visibleResources = [...catalog]
    .filter((resource) => {
      if (selectedRootIds.has(resource.resource_id) || autoIncludedIds.has(resource.resource_id)) {
        return true;
      }
      return matchesQuery(resource, deferredQuery);
    })
    .sort((left, right) => {
      const leftSelected = selectedRootIds.has(left.resource_id) ? 2 : autoIncludedIds.has(left.resource_id) ? 1 : 0;
      const rightSelected = selectedRootIds.has(right.resource_id) ? 2 : autoIncludedIds.has(right.resource_id) ? 1 : 0;
      if (leftSelected !== rightSelected) {
        return rightSelected - leftSelected;
      }
      return compareResources(left, right);
    });

  async function refreshCatalog() {
    setRefreshingCatalog(true);
    try {
      const response = await fetch(`${baseUrl}/api/catalog`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          selection_token: selectionToken,
          page_limit: pageLimit,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      const selectedResources = Array.from(selectedRootIds)
        .map((resourceId) => getRootResource(resourceId))
        .filter(Boolean) as Resource[];

      setCatalog(
        dedupeSortResources([
          ...(Array.isArray(payload.resources) ? payload.resources : []),
          ...selectedResources,
        ]),
      );
    } catch (error) {
      window.alert(error instanceof Error ? error.message : String(error));
    } finally {
      setRefreshingCatalog(false);
    }
  }

  async function addResourceByReference() {
    const resourceRef = query.trim();
    if (!resourceRef) {
      return;
    }
    setResolvingManualResource(true);
    try {
      const response = await fetch(`${baseUrl}/api/resolve-resource`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          selection_token: selectionToken,
          resource_id_or_url: resourceRef,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      const resolved = dedupeSortResources(payload.resource ? [payload.resource] : [])[0];
      if (!resolved) {
        throw new Error("Worker did not return a Notion resource.");
      }

      setCatalog((current) => dedupeSortResources([...current, resolved]));
      setSelectedRootIds((current) => {
        const next = copySet(current);
        next.add(resolved.resource_id);
        return next;
      });
      setQuery("");

      if (resolved.resource_type === "page") {
        await ensureChildrenForRoot(resolved.resource_id, resolved.resource_type);
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : String(error));
    } finally {
      setResolvingManualResource(false);
    }
  }

  async function requestHandoffBundle(chosen: BundledResource[]) {
    const response = await fetch(`${baseUrl}/api/finalize-selection`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        selection_token: selectionToken,
        selected_resources: chosen,
      }),
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

  function submitToLocalhost(bundle: string) {
    const form = document.createElement("form");
    form.method = "POST";
    form.action = String(state.return_to || "");
    form.innerHTML = [
      `<input type="hidden" name="session_id" value="${state.session_id}" />`,
      `<input type="hidden" name="handoff_bundle" value="${bundle}" />`,
    ].join("");
    document.body.appendChild(form);
    form.submit();
  }

  async function finishBinding() {
    if (!finalBundle.length || loadingRootIds.size) {
      return;
    }
    let bundle = "";
    try {
      bundle = await requestHandoffBundle(finalBundle);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : String(error));
      return;
    }
    if (state.mode === "local_browser") {
      submitToLocalhost(bundle);
      return;
    }
    setHandoffBundle(bundle);
    setBundleStatus("ready");
    try {
      await navigator.clipboard.writeText(bundle);
      setBundleStatus("copied");
    } catch {
      setBundleStatus("ready");
    }
    window.setTimeout(() => {
      outputRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }

  async function copyBundle() {
    if (!handoffBundle) {
      return;
    }
    await navigator.clipboard.writeText(handoffBundle);
    setBundleStatus("copied");
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
                <CardTitle className="text-base">Pages and Data Sources</CardTitle>
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
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-stone-400" />
              <Input
                value={query}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  startTransition(() => {
                    setQuery(nextValue);
                  });
                }}
                className="pl-9"
                placeholder="Search, or paste a Notion page or data source URL or ID"
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void addResourceByReference();
                  }
                }}
              />
            </div>
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900">
              Newly shared pages and data sources may not appear right away because this list comes from Notion search. Click <strong>Refresh</strong>, or paste its URL or ID here.
            </div>
            <div className="flex justify-end">
              <Button variant="secondary" onClick={() => void addResourceByReference()} disabled={!query.trim() || resolvingManualResource}>
                {resolvingManualResource ? <LoaderCircle className="size-3.5 animate-spin" /> : null}
                Add URL
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {visibleResources.length ? (
              <div className="space-y-2">
                {visibleResources.map((resource) => {
                  const selectedState = selectedRootIds.has(resource.resource_id)
                    ? "explicit"
                    : autoIncludedIds.has(resource.resource_id)
                      ? "descendant"
                      : "none";
                  return (
                    <ResourceRow
                      key={resource.resource_id}
                      resource={resource}
                      selectedState={selectedState}
                      loading={loadingRootIds.has(resource.resource_id)}
                      includedCount={resource.resource_type === "page" ? descendantCount(resource.resource_id) : 0}
                      disabled={selectedState === "descendant"}
                      onToggle={toggleResourceSelection}
                    />
                  );
                })}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-stone-200 bg-stone-50 px-4 py-10 text-center text-sm text-stone-500">
                No accessible resources matched this filter.
              </div>
            )}
          </CardContent>
        </Card>

        {handoffBundle ? (
          <div ref={outputRef}>
            <Card className="border-stone-200 shadow-sm">
              <CardHeader>
                <CardTitle className="text-base">Handoff Bundle</CardTitle>
                <CardDescription>
                  Paste this value into <code>notion_complete_headless_auth</code>.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600">
                  {bundleStatus === "copied"
                    ? "Bundle generated and copied to your clipboard."
                    : "Bundle generated. Copy it and paste it into notion_complete_headless_auth."}
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
                {loadingRootIds.size
                  ? "Loading selected pages..."
                  : handoffBundle
                    ? bundleStatus === "copied"
                      ? "Bundle copied to clipboard"
                      : "Bundle ready below"
                  : `${finalBundle.length} resource${finalBundle.length === 1 ? "" : "s"} selected`}
              </p>
            </div>
            <Button disabled={!finalBundle.length || loadingRootIds.size > 0} onClick={() => void finishBinding()}>
              Finish Binding
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
