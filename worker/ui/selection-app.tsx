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

type Lang = "en" | "zh-hans" | "zh-hant";

const LOCAL_HANDOFF_SUCCESS_MESSAGE = "agent-labbook-local-handoff-success";
const LOCAL_HANDOFF_WINDOW_NAME = "agent_labbook_local_handoff";
const LOCAL_HANDOFF_WAIT_TIMEOUT_MS = 10000;

declare global {
  interface Window {
    __AGENT_LABBOOK_SELECTION__?: SelectionConfig;
  }
}

// ---------------------------------------------------------------------------
// i18n translations
// ---------------------------------------------------------------------------
const translations = {
  en: {
    navBrand: "Agent Labbook",
    title: "Choose Notion Content",
    titleLine: (workspace: string, project: string) =>
      `Pick the pages and data sources from ${workspace} that ${project} should be allowed to use.`,
    selectTitle: "Select Pages and Data Sources",
    selectDesc:
      "Selecting a root binds that page or data source with subtree scope. Expand rows to inspect nested content on demand.",
    refresh: "Refresh",
    searchPlaceholder: "Search workspace by title, type, or ID",
    catalogNotice:
      "Search queries run against Notion search for the shared workspace. If something is missing, share it with the integration in Notion first, then click Refresh.",
    catalogError: (msg: string) => `Could not load available resources from Notion. ${msg}`,
    searchError: (msg: string) => `Search could not reach Notion. ${msg}`,
    loading: "Loading available pages and data sources...",
    emptyFilter: "No pages or data sources matched this filter.",
    headlessTitle: "Complete Setup in Your Agent",
    headlessCopy: "Paste this value into notion_complete_headless_auth to finish connecting this project.",
    fallbackNotice:
      "The local browser handoff could not reach the MCP server on 127.0.0.1. The handoff bundle is shown below so you can finish with notion_complete_headless_auth.",
    bundleReady: "The handoff bundle is ready. Copy it and paste it into notion_complete_headless_auth.",
    copyBundle: "Copy Bundle",
    searching: "Searching Notion...",
    delivering: "Trying to deliver the handoff through a local browser window...",
    delivered: "The handoff was sent back to the local MCP server. You can close this tab.",
    loadingNested: "Loading nested content...",
    bundleReadyBelow: "The handoff bundle is ready below.",
    rootsSelected: (count: number) => `${count} root${count === 1 ? "" : "s"} selected`,
    connectSelected: "Connect Selected",
    untitled: "Untitled",
    page: "Page",
    dataSource: "Data source",
    selected: "Selected",
    includesSubtree: "Includes subtree",
    inSelectedSubtree: "In selected subtree",
    collapseLabel: "Collapse nested items",
    expandLabel: "Expand nested items",
    editedDate: (d: string) => `Edited ${d}`,
    langEn: "EN",
    langZhHans: "简体",
    langZhHant: "繁體",
  },
  "zh-hans": {
    navBrand: "Agent Labbook",
    title: "选择 Notion 内容",
    titleLine: (workspace: string, project: string) =>
      `从 ${workspace} 中选择 ${project} 需要用到的页面和数据库。`,
    selectTitle: "选择页面和数据库",
    selectDesc: "选中一个根节点会以子树范围绑定该页面或数据库。展开行可按需查看嵌套内容。",
    refresh: "刷新",
    searchPlaceholder: "按标题、类型或 ID 搜索工作区",
    catalogNotice:
      "搜索会在已共享的工作区中查询 Notion。如果缺少某些内容，请先在 Notion 中将其共享给集成，再点击「刷新」。",
    catalogError: (msg: string) => `无法从 Notion 加载可用资源。${msg}`,
    searchError: (msg: string) => `搜索无法连接到 Notion。${msg}`,
    loading: "正在加载可用的页面和数据库……",
    emptyFilter: "没有页面或数据库匹配当前筛选条件。",
    headlessTitle: "在 AI 助手中完成设置",
    headlessCopy: "将此值粘贴到 notion_complete_headless_auth 以完成项目连接。",
    fallbackNotice:
      "本地浏览器交接无法连接到 127.0.0.1 上的 MCP 服务器。交接包已显示在下方，你可以通过 notion_complete_headless_auth 完成操作。",
    bundleReady: "交接包已就绪。复制并粘贴到 notion_complete_headless_auth 中。",
    copyBundle: "复制交接包",
    searching: "正在搜索 Notion……",
    delivering: "正在尝试通过本地浏览器窗口传递交接信息……",
    delivered: "交接信息已发送到本地 MCP 服务器。你可以关闭此标签页。",
    loadingNested: "正在加载嵌套内容……",
    bundleReadyBelow: "交接包已准备就绪，见下方。",
    rootsSelected: (count: number) => `已选择 ${count} 个根节点`,
    connectSelected: "连接所选内容",
    untitled: "无标题",
    page: "页面",
    dataSource: "数据库",
    selected: "已选",
    includesSubtree: "含子树",
    inSelectedSubtree: "在已选子树中",
    collapseLabel: "折叠嵌套项",
    expandLabel: "展开嵌套项",
    editedDate: (d: string) => `编辑于 ${d}`,
    langEn: "EN",
    langZhHans: "简体",
    langZhHant: "繁體",
  },
  "zh-hant": {
    navBrand: "Agent Labbook",
    title: "選擇 Notion 內容",
    titleLine: (workspace: string, project: string) =>
      `從 ${workspace} 中選擇 ${project} 需要使用的頁面與資料庫。`,
    selectTitle: "選擇頁面與資料庫",
    selectDesc: "選取一個根節點會以子樹範圍綁定該頁面或資料庫。展開列可依需求查看巢狀內容。",
    refresh: "重新整理",
    searchPlaceholder: "依標題、類型或 ID 搜尋工作區",
    catalogNotice:
      "搜尋會在已分享的工作區中查詢 Notion。如果缺少某些內容，請先在 Notion 中將其分享給整合，再點擊「重新整理」。",
    catalogError: (msg: string) => `無法從 Notion 載入可用資源。${msg}`,
    searchError: (msg: string) => `搜尋無法連線至 Notion。${msg}`,
    loading: "正在載入可用的頁面與資料庫……",
    emptyFilter: "沒有頁面或資料庫符合目前篩選條件。",
    headlessTitle: "在 AI 助手中完成設定",
    headlessCopy: "將此值貼入 notion_complete_headless_auth 以完成專案連線。",
    fallbackNotice:
      "本機瀏覽器交接無法連線至 127.0.0.1 上的 MCP 伺服器。交接包已顯示於下方，你可以透過 notion_complete_headless_auth 完成操作。",
    bundleReady: "交接包已就緒。複製並貼入 notion_complete_headless_auth。",
    copyBundle: "複製交接包",
    searching: "正在搜尋 Notion……",
    delivering: "正在嘗試透過本機瀏覽器視窗傳遞交接資訊……",
    delivered: "交接資訊已送回本機 MCP 伺服器。你可以關閉此分頁。",
    loadingNested: "正在載入巢狀內容……",
    bundleReadyBelow: "交接包已準備就緒，見下方。",
    rootsSelected: (count: number) => `已選擇 ${count} 個根節點`,
    connectSelected: "連接所選內容",
    untitled: "無標題",
    page: "頁面",
    dataSource: "資料庫",
    selected: "已選",
    includesSubtree: "含子樹",
    inSelectedSubtree: "在已選子樹中",
    collapseLabel: "收合巢狀項目",
    expandLabel: "展開巢狀項目",
    editedDate: (d: string) => `編輯於 ${d}`,
    langEn: "EN",
    langZhHans: "简体",
    langZhHant: "繁體",
  },
} as const;

type Translations = (typeof translations)[Lang];

function detectLang(): Lang {
  try {
    const saved = localStorage.getItem("sp-lang");
    if (saved === "zh-hans" || saved === "zh-hant") return saved;
    if (saved === "en") return "en";
  } catch {}
  const nav = navigator.language || "";
  if (/^zh[\-_](tw|hk|mo|hant)/i.test(nav) || nav === "zh-Hant") return "zh-hant";
  if (/^zh/i.test(nav)) return "zh-hans";
  return "en";
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
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

function formatTypeLabel(type: string, t: Translations) {
  if (type === "data_source") {
    return t.dataSource;
  }
  if (type === "page") {
    return t.page;
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
    return <Database className="size-3.5 text-[#9a8d80]" />;
  }
  return <FileText className="size-3.5 text-[#9a8d80]" />;
}

// ---------------------------------------------------------------------------
// Language Switcher
// ---------------------------------------------------------------------------
function LangSwitcher({ lang, setLang }: { lang: Lang; setLang: (l: Lang) => void }) {
  const t = translations[lang];
  const btnBase =
    "px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer border-none";
  const active = "bg-[rgba(196,83,45,0.10)] text-[#c4532d] font-semibold";
  const inactive = "bg-transparent text-[#9a8d80] hover:bg-[rgba(196,83,45,0.06)] hover:text-[#c4532d]";

  return (
    <div className="flex items-center gap-1">
      <button type="button" className={cn(btnBase, lang === "en" ? active : inactive)} onClick={() => setLang("en")}>
        {t.langEn}
      </button>
      <button
        type="button"
        className={cn(btnBase, lang === "zh-hans" ? active : inactive)}
        onClick={() => setLang("zh-hans")}
      >
        {t.langZhHans}
      </button>
      <button
        type="button"
        className={cn(btnBase, lang === "zh-hant" ? active : inactive)}
        onClick={() => setLang("zh-hant")}
      >
        {t.langZhHant}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ResourceRow
// ---------------------------------------------------------------------------
function ResourceRow({
  resource,
  selectedState,
  loading,
  includesSubtree,
  canExpand,
  expanded,
  depth = 0,
  disabled = false,
  t,
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
  t: Translations;
  onToggle?: (resource: Resource, checked: boolean) => Promise<void> | void;
  onToggleExpand?: (resource: Resource) => Promise<void> | void;
}) {
  const selected = selectedState !== "none";
  const edited = formatDate(resource.last_edited_time);
  const details = [
    resource.title || t.untitled,
    formatTypeLabel(resource.resource_type, t),
    resource.resource_id,
    edited ? t.editedDate(edited) : null,
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
          ? "bg-[#fef3ee]"
          : selectedState === "descendant"
            ? "bg-[#faf7f2]"
            : "hover:bg-[#faf7f2]",
      )}
      style={{ paddingLeft: `${8 + depth * 18}px` }}
      title={details}
    >
      {canExpand ? (
        <button
          type="button"
          className="flex size-4 shrink-0 items-center justify-center rounded text-[#9a8d80] hover:bg-[rgba(196,83,45,0.08)] hover:text-[#6b5e52]"
          aria-label={expanded ? t.collapseLabel : t.expandLabel}
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
          <span className="truncate text-[13px] text-[#1a1410]">{resource.title || t.untitled}</span>
          <Badge variant="outline" className="h-5 shrink-0 rounded-full border-[rgba(28,20,15,0.12)] px-2 text-[10px] text-[#9a8d80]">
            {resource.resource_type === "data_source" ? t.dataSource : t.page}
          </Badge>
          {selectedState === "explicit" ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full border-[rgba(196,83,45,0.2)] px-2 text-[10px] text-[#c4532d]">
              {t.selected}
            </Badge>
          ) : null}
          {selectedState === "explicit" && includesSubtree ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full border-[rgba(196,83,45,0.2)] px-2 text-[10px] text-[#c4532d]">
              {t.includesSubtree}
            </Badge>
          ) : null}
          {selectedState === "descendant" ? (
            <Badge variant="outline" className="h-5 shrink-0 rounded-full border-[rgba(28,20,15,0.12)] px-2 text-[10px] text-[#9a8d80]">
              {t.inSelectedSubtree}
            </Badge>
          ) : null}
        </div>
      </div>

      {loading ? <LoaderCircle className="size-3.5 shrink-0 animate-spin text-[#c4532d]" /> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SelectionApp
// ---------------------------------------------------------------------------
function SelectionApp({
  baseUrl,
  state,
  oauthSession,
  workspaceName,
  resources,
  catalogLoaded: initialCatalogLoaded,
  catalogError: initialCatalogError,
}: SelectionConfig) {
  const [lang, setLangState] = useState<Lang>(detectLang);
  const t = translations[lang];

  function setLang(nextLang: Lang) {
    setLangState(nextLang);
    try {
      localStorage.setItem("sp-lang", nextLang);
    } catch {}
  }

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
  const workspaceLabel = workspaceName || (lang === "en" ? "your workspace" : lang === "zh-hans" ? "你的工作区" : "你的工作區");
  const projectLabel = state.project_name || (lang === "en" ? "this project" : lang === "zh-hans" ? "此项目" : "此專案");

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
      const msg =
        lang === "zh-hans"
          ? "正在连接 Agent Labbook 到本地 MCP 监听器……"
          : lang === "zh-hant"
            ? "正在連線 Agent Labbook 到本機 MCP 監聽器……"
            : "Connecting Agent Labbook to the local MCP listener...";
      popup.document.write(
        `<!doctype html><html><body><p style="font-family: sans-serif; padding: 24px;">${msg}</p></body></html>`,
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
    <div className="min-h-screen bg-[#faf7f2]">
      {/* Brand nav */}
      <nav className="sticky top-0 z-30 border-b border-[rgba(28,20,15,0.08)] bg-[rgba(250,247,242,0.85)] backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-3 sm:px-5">
          <a
            href="https://superplanner.ai/notion/agent-labbook/"
            className="font-['DM_Serif_Display',serif] text-lg text-[#1a1410] no-underline"
          >
            {t.navBrand}
          </a>
          <LangSwitcher lang={lang} setLang={setLang} />
        </div>
      </nav>

      <div className="mx-auto w-full max-w-3xl px-3 py-6 sm:px-5 sm:py-8">
        <div className="space-y-4 pb-28">
          <div className="space-y-1">
            <h1 className="max-w-2xl font-['DM_Serif_Display',serif] text-xl font-normal tracking-tight text-[#1a1410] sm:text-2xl">
              {t.title}
            </h1>
            <p className="text-sm text-[#6b5e52]">{t.titleLine(workspaceLabel, projectLabel)}</p>
          </div>

          <Card className="border-[rgba(28,20,15,0.08)] bg-[rgba(255,253,250,0.82)] shadow-sm">
            <CardHeader className="gap-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="space-y-1">
                  <CardTitle className="text-base text-[#1a1410]">{t.selectTitle}</CardTitle>
                  <CardDescription className="text-[#6b5e52]">{t.selectDesc}</CardDescription>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void refreshCatalog()}
                  disabled={refreshingCatalog}
                >
                  <RefreshCw className={cn("size-3.5", refreshingCatalog && "animate-spin")} />
                  {t.refresh}
                </Button>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[#9a8d80]" />
                  <Input
                    value={inputValue}
                    onChange={(event) => {
                      const nextValue = event.target.value;
                      startTransition(() => {
                        setInputValue(nextValue);
                      });
                    }}
                    className="border-[rgba(28,20,15,0.12)] bg-white pl-9 focus-visible:ring-[rgba(196,83,45,0.2)]"
                    placeholder={t.searchPlaceholder}
                  />
                </div>
              </div>
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900">
                {t.catalogNotice}
              </div>
              {catalogError ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-900">
                  {t.catalogError(catalogError)}
                </div>
              ) : null}
              {searchError && searchActive ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-900">
                  {t.searchError(searchError)}
                </div>
              ) : null}
            </CardHeader>
            <CardContent>
              {!catalogLoaded && refreshingCatalog ? (
                <div className="rounded-xl border border-dashed border-[rgba(28,20,15,0.12)] bg-[#faf7f2] px-4 py-10 text-center text-sm text-[#6b5e52]">
                  {t.loading}
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
                        t={t}
                        onToggle={toggleResourceSelection}
                        onToggleExpand={toggleCollapsed}
                      />
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-[rgba(28,20,15,0.12)] bg-[#faf7f2] px-4 py-10 text-center text-sm text-[#6b5e52]">
                  {t.emptyFilter}
                </div>
              )}
            </CardContent>
          </Card>

          {handoffBundle ? (
            <div ref={outputRef}>
              <Card className="border-[rgba(28,20,15,0.08)] bg-[rgba(255,253,250,0.82)] shadow-sm">
                <CardHeader>
                  <CardTitle className="text-base text-[#1a1410]">{t.headlessTitle}</CardTitle>
                  <CardDescription className="text-[#6b5e52]">
                    {t.headlessCopy}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="rounded-xl border border-[rgba(28,20,15,0.08)] bg-[#faf7f2] px-3 py-2 text-sm text-[#6b5e52]">
                    {localDeliveryStatus === "fallback" ? t.fallbackNotice : t.bundleReady}
                  </div>
                  <textarea
                    readOnly
                    value={handoffBundle}
                    className="min-h-56 w-full rounded-xl border border-[rgba(28,20,15,0.12)] bg-white px-3 py-3 font-mono text-xs text-[#1a1410] outline-none"
                  />
                  <Button variant="secondary" onClick={() => void copyBundle()}>
                    {t.copyBundle}
                  </Button>
                </CardContent>
              </Card>
            </div>
          ) : null}
        </div>

        <div className="sticky bottom-3 z-20">
          <Card className="border-[rgba(28,20,15,0.08)] bg-[rgba(255,253,250,0.95)] shadow-lg backdrop-blur">
            <CardContent className="flex items-center justify-between gap-3 pt-5">
              <div className="min-w-0">
                <p className="text-sm text-[#6b5e52]">
                  {searchingCatalog
                    ? t.searching
                    : localDeliveryStatus === "delivering"
                      ? t.delivering
                    : localDeliveryStatus === "delivered"
                      ? t.delivered
                    : loadingRootIds.size
                    ? t.loadingNested
                    : handoffBundle
                      ? t.bundleReadyBelow
                    : t.rootsSelected(finalBundle.length)}
                </p>
              </div>
              <Button
                disabled={!finalBundle.length}
                onClick={() => void finishBinding()}
                className="bg-[#c4532d] text-white hover:bg-[#a8432a]"
              >
                {t.connectSelected}
              </Button>
            </CardContent>
          </Card>
        </div>
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
