from __future__ import annotations

import json
from typing import Any


def _inline_json(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_binding_browser_page(payload: dict[str, Any]) -> str:
    config_json = _inline_json(payload)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Agent Labbook Binding Chooser</title>
    <style>
      :root {{
        --bg: #faf7f2;
        --paper: #fffdf9;
        --ink: #1a1410;
        --muted: #6b5e52;
        --line: #e6ddd2;
        --accent: #c4532d;
        --accent-hover: #a8432a;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: Inter, system-ui, sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(196,83,45,0.10), transparent 30%),
          linear-gradient(180deg, #fffaf5, var(--bg));
      }}
      .shell {{
        max-width: 1240px;
        margin: 0 auto;
        padding: 32px 20px 48px;
      }}
      .hero {{
        display: flex;
        justify-content: space-between;
        gap: 20px;
        align-items: flex-end;
        margin-bottom: 24px;
      }}
      h1 {{
        margin: 0;
        font-size: clamp(2rem, 4vw, 3rem);
        font-family: "DM Serif Display", "Noto Serif SC", "Noto Serif TC", serif;
        line-height: 1;
      }}
      .subtle {{
        color: var(--muted);
        margin-top: 10px;
        max-width: 64ch;
      }}
      .workspace {{
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 10px 14px;
        background: rgba(255,255,255,0.8);
        color: var(--muted);
        font-size: 0.95rem;
      }}
      .recommendation {{
        margin-bottom: 18px;
        border: 1px solid #e5c7b8;
        background: linear-gradient(180deg, #fffaf4, #fff6ef);
      }}
      .recommendation strong {{
        display: block;
        margin-bottom: 8px;
      }}
      .options {{
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 12px;
      }}
      .option {{
        border: 1px solid var(--line);
        border-radius: 14px;
        background: #fff;
        padding: 10px 12px;
        min-width: 180px;
        flex: 1 1 220px;
      }}
      .option-title {{
        font-weight: 600;
        margin-bottom: 4px;
      }}
      .option-title .recommended {{
        margin-left: 6px;
        color: var(--accent);
      }}
      .layout {{
        display: grid;
        grid-template-columns: minmax(0, 1.6fr) minmax(320px, 0.9fr);
        gap: 20px;
      }}
      .card {{
        background: var(--paper);
        border: 1px solid var(--line);
        border-radius: 22px;
        padding: 18px;
        box-shadow: 0 20px 60px rgba(26,20,16,0.05);
      }}
      .controls {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto auto;
        gap: 12px;
        margin-bottom: 16px;
      }}
      input[type="search"], textarea, select {{
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 12px 14px;
        font: inherit;
        color: var(--ink);
        background: #fff;
      }}
      textarea {{
        min-height: 120px;
        resize: vertical;
      }}
      button {{
        border: 0;
        border-radius: 14px;
        background: var(--accent);
        color: #fff;
        font: inherit;
        font-weight: 600;
        padding: 12px 16px;
        cursor: pointer;
      }}
      button.secondary {{
        background: #efe5da;
        color: var(--ink);
      }}
      button:hover {{ background: var(--accent-hover); }}
      button.secondary:hover {{ background: #e6d7c7; }}
      .section-title {{
        margin: 0 0 12px;
        font-size: 1.1rem;
      }}
      .list {{
        display: flex;
        flex-direction: column;
        gap: 10px;
      }}
      .item {{
        border: 1px solid var(--line);
        border-radius: 16px;
        background: #fff;
        padding: 12px;
      }}
      .item-head {{
        display: grid;
        grid-template-columns: auto auto 1fr auto auto;
        gap: 10px;
        align-items: start;
      }}
      .depth {{
        display: inline-flex;
        width: 1.25rem;
        justify-content: center;
        color: var(--muted);
      }}
      .title {{
        font-weight: 600;
        margin-bottom: 4px;
      }}
      .meta {{
        color: var(--muted);
        font-size: 0.9rem;
      }}
      .crumb {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 0.85rem;
        color: var(--muted);
        margin-top: 4px;
        flex-wrap: wrap;
      }}
      .crumb span {{
        border-radius: 999px;
        background: #f3eee8;
        padding: 4px 8px;
      }}
      .children {{
        margin-top: 10px;
        padding-left: 22px;
        border-left: 1px dashed var(--line);
      }}
      .toolbar {{
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
        margin-bottom: 12px;
      }}
      .pill {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 7px 10px;
        border-radius: 999px;
        background: #f2ebe3;
        color: var(--muted);
        font-size: 0.9rem;
      }}
      .selected-list {{
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-bottom: 16px;
      }}
      .selected-item {{
        display: grid;
        gap: 8px;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 12px;
        background: #fff;
      }}
      .hint, .empty {{
        color: var(--muted);
        font-size: 0.95rem;
      }}
      .status {{
        min-height: 24px;
        color: var(--muted);
        margin-top: 8px;
      }}
      .ok {{ color: #17643d; }}
      .error {{ color: #9d2f2f; }}
      a {{ color: var(--accent); }}
      @media (max-width: 960px) {{
        .layout {{ grid-template-columns: 1fr; }}
        .hero {{ flex-direction: column; align-items: start; }}
        .controls {{ grid-template-columns: 1fr; }}
        .item-head {{ grid-template-columns: auto 1fr; }}
      }}
    </style>
  </head>
  <body>
    <div id="app" class="shell"></div>
    <script>
      const config = {config_json};

      const translations = {{
        en: {{
          title: "Choose Notion Roots",
          subtitle: "Search, expand, and bind the exact pages or data sources this project should use. You can also paste one or more Notion URLs directly.",
          searchPlaceholder: "Search accessible pages or data sources",
          refresh: "Refresh",
          reloadBindings: "Reload bindings",
          available: "Available content",
          selected: "Pending selection",
          bound: "Current bindings",
          noResults: "No resources matched the current search.",
          noSelection: "Nothing selected yet.",
          noBindings: "No resources are currently bound.",
          pastedUrls: "Paste Notion URLs",
          pastedUrlsHint: "One page or data source URL per line. These are added directly as binding roots.",
          bindSelected: "Bind selected resources",
          page: "Page",
          data_source: "Data source",
          expand: "Expand",
          collapse: "Collapse",
          selectionScope: "Selection scope",
          resource: "resource",
          subtree: "subtree",
          loading: "Loading…",
          bindingSuccess: "Bindings saved.",
          workspace: "Workspace",
          chooser: "Local chooser",
          partialChildren: "Showing fast results. Deeper nested content may still exist.",
          scanDeeper: "Scan deeper",
          scanningDeeper: "Scanning deeper…",
          addedMore: (count) => `Added ${{count}} more item${{count === 1 ? "" : "s"}}.`,
          noAdditional: "No additional nested items were found.",
          recommendationTitle: "Recommended binding path",
          recommendationFallback: "Choose the fastest binding path for this environment and user preference.",
          recommendationQuestion: "Best next question",
          recommendationQuestionFallback: "Can you paste one or more exact Notion links, or do you want me to open the local chooser?",
          filters: "Filters",
          filter_all: "All",
          filter_pages: "Pages",
          filter_data_sources: "Data sources",
          filter_roots: "Root-like",
          filter_unbound: "Unbound only",
          suggestedRoots: "Suggested roots",
          moreMatches: "More matches",
          boundBadge: "Already bound",
          recommendationModes: {{
            wait_for_auth: "Finish auth first",
            url: "Paste exact links",
            local_browser: "Open local chooser",
            manual_mcp: "Search in chat",
          }},
        }},
        "zh-hans": {{
          title: "选择 Notion 根节点",
          subtitle: "搜索、展开并绑定项目真正需要使用的页面或数据库。你也可以直接粘贴一个或多个 Notion 链接。",
          searchPlaceholder: "搜索可访问的页面或数据库",
          refresh: "刷新",
          reloadBindings: "重新加载绑定",
          available: "可选内容",
          selected: "待绑定选择",
          bound: "当前绑定",
          noResults: "当前搜索没有匹配结果。",
          noSelection: "还没有选择任何内容。",
          noBindings: "当前还没有任何绑定。",
          pastedUrls: "粘贴 Notion 链接",
          pastedUrlsHint: "每行一个页面或数据库链接。它们会直接作为绑定根节点加入。",
          bindSelected: "绑定所选内容",
          page: "页面",
          data_source: "数据库",
          expand: "展开",
          collapse: "收起",
          selectionScope: "绑定范围",
          resource: "resource",
          subtree: "subtree",
          loading: "加载中…",
          bindingSuccess: "绑定已保存。",
          workspace: "工作区",
          chooser: "本地选择器",
          partialChildren: "当前先显示快速结果，可能还有更深层的嵌套内容。",
          scanDeeper: "继续深度扫描",
          scanningDeeper: "深度扫描中…",
          addedMore: (count) => `新增 ${{count}} 个结果。`,
          noAdditional: "没有发现更多嵌套内容。",
          recommendationTitle: "推荐绑定路径",
          recommendationFallback: "请根据当前环境和用户偏好选择最合适的绑定方式。",
          recommendationQuestion: "建议下一句先问",
          recommendationQuestionFallback: "你能直接粘贴一个或多个 Notion 链接吗？如果不能，我可以打开本地选择器。",
          filters: "筛选",
          filter_all: "全部",
          filter_pages: "页面",
          filter_data_sources: "数据库",
          filter_roots: "更像根节点",
          filter_unbound: "只看未绑定",
          suggestedRoots: "推荐根节点",
          moreMatches: "更多匹配结果",
          boundBadge: "已绑定",
          recommendationModes: {{
            wait_for_auth: "先完成认证",
            url: "粘贴精确链接",
            local_browser: "打开本地选择器",
            manual_mcp: "在聊天里搜索",
          }},
        }},
        "zh-hant": {{
          title: "選擇 Notion 根節點",
          subtitle: "搜尋、展開並綁定專案真正需要使用的頁面或資料庫。你也可以直接貼上一個或多個 Notion 連結。",
          searchPlaceholder: "搜尋可存取的頁面或資料庫",
          refresh: "重新整理",
          reloadBindings: "重新載入綁定",
          available: "可選內容",
          selected: "待綁定選擇",
          bound: "目前綁定",
          noResults: "目前搜尋沒有符合結果。",
          noSelection: "尚未選擇任何內容。",
          noBindings: "目前尚未綁定任何資源。",
          pastedUrls: "貼上 Notion 連結",
          pastedUrlsHint: "每行一個頁面或資料庫連結。它們會直接作為綁定根節點加入。",
          bindSelected: "綁定所選內容",
          page: "頁面",
          data_source: "資料庫",
          expand: "展開",
          collapse: "收合",
          selectionScope: "綁定範圍",
          resource: "resource",
          subtree: "subtree",
          loading: "載入中…",
          bindingSuccess: "綁定已儲存。",
          workspace: "工作區",
          chooser: "本機選擇器",
          partialChildren: "目前先顯示快速結果，可能還有更深層的巢狀內容。",
          scanDeeper: "繼續深度掃描",
          scanningDeeper: "深度掃描中…",
          addedMore: (count) => `新增 ${{count}} 個結果。`,
          noAdditional: "沒有找到更多巢狀內容。",
          recommendationTitle: "推薦綁定路徑",
          recommendationFallback: "請依照目前環境與使用者偏好選擇最合適的綁定方式。",
          recommendationQuestion: "建議下一句先問",
          recommendationQuestionFallback: "你能直接貼上一個或多個 Notion 連結嗎？如果不能，我可以開啟本機選擇器。",
          filters: "篩選",
          filter_all: "全部",
          filter_pages: "頁面",
          filter_data_sources: "資料庫",
          filter_roots: "更像根節點",
          filter_unbound: "只看未綁定",
          suggestedRoots: "推薦根節點",
          moreMatches: "更多符合結果",
          boundBadge: "已綁定",
          recommendationModes: {{
            wait_for_auth: "先完成驗證",
            url: "貼上精確連結",
            local_browser: "開啟本機選擇器",
            manual_mcp: "在聊天中搜尋",
          }},
        }},
      }};

      function detectLang() {{
        try {{
          const saved = localStorage.getItem("sp-lang");
          if (saved && translations[saved]) return saved;
        }} catch {{}}
        const nav = navigator.language || "";
        if (/^zh[-_](tw|hk|mo|hant)/i.test(nav) || nav === "zh-Hant") return "zh-hant";
        if (/^zh/i.test(nav)) return "zh-hans";
        return "en";
      }}

      const t = translations[detectLang()];
      const state = {{
        query: "",
        catalog: [],
        children: new Map(),
        expanded: new Set(),
        selected: new Map(),
        bindings: [],
        loading: false,
        message: "",
        messageClass: "",
        searchTimer: null,
        filter: "all",
        hideBound: false,
        childMeta: new Map(),
        deepLoading: new Set(),
      }};

      function api(path, options = {{}}) {{
        return fetch(path, {{
          headers: {{ "Content-Type": "application/json" }},
          ...options,
        }}).then(async (response) => {{
          const payload = await response.json().catch(() => ({{ error: "Invalid JSON response." }}));
          if (!response.ok) throw new Error(payload.error || `HTTP ${{response.status}}`);
          return payload;
        }});
      }}

      function parsePastedUrls() {{
        const textarea = document.getElementById("url-input");
        const scope = document.getElementById("url-scope").value;
        return textarea.value
          .split(/\\n+/)
          .map((line) => line.trim())
          .filter(Boolean)
          .map((url) => ({{
            key: `url:${{url}}`,
            title: url,
            resource_id_or_url: url,
            resource_type: null,
            selection_scope: scope,
            isUrl: true,
          }}));
      }}

      function toggleSelection(item) {{
        const existing = state.selected.get(item.key || item.resource_id);
        if (existing) {{
          state.selected.delete(item.key || item.resource_id);
        }} else {{
          state.selected.set(item.key || item.resource_id, {{
            resource_id_or_url: item.resource_url || item.resource_id_or_url || item.resource_id,
            resource_type: item.resource_type || null,
            title: item.title,
            selection_scope: item.selection_scope || "subtree",
            isUrl: Boolean(item.isUrl),
          }});
        }}
        render();
      }}

      function describeParent(item) {{
        const parent = item.parent || null;
        if (!parent || typeof parent !== "object") return "";
        if (parent.type === "workspace") return "workspace";
        if (parent.type === "page_id" && parent.page_id) return `page · ${{parent.page_id}}`;
        if (parent.type === "data_source_id" && parent.data_source_id) return `data source · ${{parent.data_source_id}}`;
        if (parent.type === "database_id" && parent.database_id) return `database · ${{parent.database_id}}`;
        return parent.type || "";
      }}

      function isRootLike(item) {{
        const parent = item.parent || null;
        return !parent || parent.type === "workspace";
      }}

      function isBound(item) {{
        return state.bindings.some((binding) => binding.resource_id === item.resource_id);
      }}

      function mergeByResourceId(existingItems, nextItems) {{
        const byId = new Map();
        for (const item of existingItems || []) {{
          byId.set(item.resource_id, item);
        }}
        for (const item of nextItems || []) {{
          byId.set(item.resource_id, item);
        }}
        return Array.from(byId.values());
      }}

      function scheduleSearch() {{
        if (state.searchTimer) clearTimeout(state.searchTimer);
        state.searchTimer = setTimeout(() => {{
          loadCatalog();
        }}, 250);
      }}

      function updateSelectedScope(key, scope) {{
        const selected = state.selected.get(key);
        if (!selected) return;
        selected.selection_scope = scope;
        render();
      }}

      async function loadBindings() {{
        const payload = await api("/api/bindings");
        state.bindings = payload.resources || [];
        render();
      }}

      async function loadCatalog() {{
        state.loading = true;
        render();
        try {{
          const params = new URLSearchParams();
          params.set("page_size", String(config.page_size));
          if (state.query.trim()) params.set("query", state.query.trim());
          const payload = await api(`/api/search?${{params.toString()}}`);
          state.catalog = payload.results || [];
          state.message = "";
          state.messageClass = "";
        }} catch (error) {{
          state.message = String(error.message || error);
          state.messageClass = "error";
        }} finally {{
          state.loading = false;
          render();
        }}
      }}

      async function toggleExpand(item) {{
        const key = item.resource_id;
        if (state.expanded.has(key)) {{
          state.expanded.delete(key);
          render();
          return;
        }}
        if (!state.children.has(key)) {{
          state.loading = true;
          render();
          try {{
            const params = new URLSearchParams();
            params.set("resource_id_or_url", item.resource_url || item.resource_id);
            params.set("resource_type", item.resource_type);
            params.set("limit", "50");
            params.set("mode", "shallow");
            const payload = await api(`/api/children?${{params.toString()}}`);
            state.children.set(key, payload.results || []);
            state.childMeta.set(key, {{
              partial: Boolean(payload.partial),
              mode: payload.mode || "shallow",
            }});
          }} catch (error) {{
            state.message = String(error.message || error);
            state.messageClass = "error";
          }} finally {{
            state.loading = false;
          }}
        }}
        state.expanded.add(key);
        render();
      }}

      async function scanDeeper(item) {{
        const key = item.resource_id;
        state.deepLoading.add(key);
        render();
        try {{
          const params = new URLSearchParams();
          params.set("resource_id_or_url", item.resource_url || item.resource_id);
          params.set("resource_type", item.resource_type);
          params.set("limit", "50");
          params.set("mode", "deep");
          const payload = await api(`/api/children?${{params.toString()}}`);
          const previous = state.children.get(key) || [];
          const merged = mergeByResourceId(previous, payload.results || []);
          state.children.set(key, merged);
          state.childMeta.set(key, {{
            partial: Boolean(payload.partial),
            mode: payload.mode || "deep",
            addedCount: Math.max(0, merged.length - previous.length),
          }});
        }} catch (error) {{
          state.message = String(error.message || error);
          state.messageClass = "error";
        }} finally {{
          state.deepLoading.delete(key);
          render();
        }}
      }}

      function renderNode(item, depth = 0) {{
        const key = item.resource_id;
        const children = state.children.get(key) || [];
        const selected = state.selected.get(key);
        const wrapper = document.createElement("div");
        wrapper.className = "item";

        const head = document.createElement("div");
        head.className = "item-head";

        const depthEl = document.createElement("div");
        depthEl.className = "depth";
        depthEl.textContent = depth ? "└" : "";
        head.appendChild(depthEl);

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = Boolean(selected);
        checkbox.addEventListener("change", () => toggleSelection(item));
        head.appendChild(checkbox);

        const body = document.createElement("div");
        const title = document.createElement("div");
        title.className = "title";
        title.textContent = item.title;
        body.appendChild(title);
        const crumb = document.createElement("div");
        crumb.className = "crumb";
        const typeChip = document.createElement("span");
        typeChip.textContent = t[item.resource_type] || item.resource_type;
        crumb.appendChild(typeChip);
        if (isRootLike(item)) {{
          const rootChip = document.createElement("span");
          rootChip.textContent = t.filter_roots;
          crumb.appendChild(rootChip);
        }}
        if (isBound(item)) {{
          const boundChip = document.createElement("span");
          boundChip.textContent = t.boundBadge;
          crumb.appendChild(boundChip);
        }}
        body.appendChild(crumb);
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = `${{t[item.resource_type] || item.resource_type}} · ${{item.resource_id}}`;
        body.appendChild(meta);
        const parentText = describeParent(item);
        if (parentText || item.last_edited_time) {{
          const details = document.createElement("div");
          details.className = "meta";
          details.textContent = [parentText, item.last_edited_time].filter(Boolean).join(" · ");
          body.appendChild(details);
        }}
        if (item.resource_url) {{
          const link = document.createElement("a");
          link.href = item.resource_url;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.textContent = item.resource_url;
          body.appendChild(link);
        }}
        head.appendChild(body);

        const scope = document.createElement("select");
        scope.innerHTML = `
          <option value="resource">${{t.resource}}</option>
          <option value="subtree">${{t.subtree}}</option>
        `;
        scope.value = selected?.selection_scope || "subtree";
        scope.addEventListener("change", (event) => {{
          if (!selected) toggleSelection(item);
          updateSelectedScope(key, event.target.value);
        }});
        head.appendChild(scope);

        const expand = document.createElement("button");
        expand.className = "secondary";
        expand.textContent = state.expanded.has(key) ? t.collapse : t.expand;
        expand.addEventListener("click", () => toggleExpand(item));
        head.appendChild(expand);

        wrapper.appendChild(head);

        if (state.expanded.has(key)) {{
          const childWrap = document.createElement("div");
          childWrap.className = "children";
          const childMeta = state.childMeta.get(key) || {{ partial: false, mode: "shallow" }};
          if (!children.length) {{
            const empty = document.createElement("div");
            empty.className = "empty";
            empty.textContent = t.noResults;
            childWrap.appendChild(empty);
          }} else {{
            for (const child of children) {{
              childWrap.appendChild(renderNode(child, depth + 1));
            }}
          }}
          if (childMeta.partial && childMeta.mode !== "deep") {{
            const partial = document.createElement("div");
            partial.className = "toolbar";
            const hint = document.createElement("div");
            hint.className = "hint";
            hint.textContent = t.partialChildren;
            partial.appendChild(hint);
            const deeper = document.createElement("button");
            deeper.className = "secondary";
            deeper.textContent = state.deepLoading.has(key) ? t.scanningDeeper : t.scanDeeper;
            deeper.disabled = state.deepLoading.has(key);
            deeper.addEventListener("click", () => scanDeeper(item));
            partial.appendChild(deeper);
            childWrap.appendChild(partial);
          }} else if (childMeta.mode === "deep") {{
            const partial = document.createElement("div");
            partial.className = "hint";
            partial.textContent = childMeta.addedCount
              ? t.addedMore(childMeta.addedCount)
              : t.noAdditional;
            childWrap.appendChild(partial);
          }}
          wrapper.appendChild(childWrap);
        }}

        return wrapper;
      }}

      async function bindSelected() {{
        const resourceRefs = [
          ...Array.from(state.selected.values()).map((item) => ({{
            resource_id_or_url: item.resource_id_or_url,
            resource_type: item.resource_type,
            title: item.isUrl ? undefined : item.title,
            selection_scope: item.selection_scope,
          }})),
          ...parsePastedUrls().map((item) => ({{
            resource_id_or_url: item.resource_id_or_url,
            selection_scope: item.selection_scope,
          }})),
        ];
        if (!resourceRefs.length) {{
          state.message = t.noSelection;
          state.messageClass = "error";
          render();
          return;
        }}

        state.loading = true;
        render();
        try {{
          await api("/api/bind", {{
            method: "POST",
            body: JSON.stringify({{ resource_refs: resourceRefs }}),
          }});
          state.message = t.bindingSuccess;
          state.messageClass = "ok";
          state.selected.clear();
          document.getElementById("url-input").value = "";
          await loadBindings();
        }} catch (error) {{
          state.message = String(error.message || error);
          state.messageClass = "error";
        }} finally {{
          state.loading = false;
          render();
        }}
      }}

      function render() {{
        const app = document.getElementById("app");
        app.innerHTML = "";

        const hero = document.createElement("div");
        hero.className = "hero";
        hero.innerHTML = `
          <div>
            <div class="pill">${{t.chooser}}</div>
            <h1>${{t.title}}</h1>
            <div class="subtle">${{t.subtitle}}</div>
          </div>
          <div class="workspace">${{t.workspace}}: ${{config.workspace_name || config.project_root}}</div>
        `;
        app.appendChild(hero);

        const recommendation = document.createElement("div");
        recommendation.className = "card recommendation";
        const bindingRecommendation = config.binding_recommendation || null;
        const bindingOptions = Array.isArray(config.binding_options) ? config.binding_options : [];
        recommendation.innerHTML = `
          <strong>${{t.recommendationTitle}}</strong>
          <div class="subtle">${{bindingRecommendation?.reason || t.recommendationFallback}}</div>
          <div class="hint"><strong>${{t.recommendationQuestion}}:</strong> ${{config.binding_question || t.recommendationQuestionFallback}}</div>
          <div class="options"></div>
        `;
        const optionsWrap = recommendation.querySelector(".options");
        for (const option of bindingOptions) {{
          const node = document.createElement("div");
          node.className = "option";
          const modeLabel = t.recommendationModes?.[option.mode] || option.label || option.mode;
          node.innerHTML = `
            <div class="option-title">
              ${{modeLabel}}
              ${{option.recommended ? '<span class="recommended">Recommended</span>' : ''}}
            </div>
            <div class="meta">${{option.reason}}</div>
          `;
          optionsWrap.appendChild(node);
        }}
        app.appendChild(recommendation);

        const layout = document.createElement("div");
        layout.className = "layout";

        const left = document.createElement("div");
        left.className = "card";
        left.innerHTML = `
          <div class="toolbar">
            <div class="pill">${{config.project_root}}</div>
            <div class="pill">${{state.catalog.length}} result${{state.catalog.length === 1 ? "" : "s"}}</div>
            <label class="pill"><input id="hide-bound" type="checkbox" ${{state.hideBound ? "checked" : ""}} /> ${{t.filter_unbound}}</label>
          </div>
          <div class="controls">
            <input id="search" type="search" placeholder="${{t.searchPlaceholder}}" value="${{state.query}}" />
            <select id="filter">
              <option value="all">${{t.filter_all}}</option>
              <option value="pages">${{t.filter_pages}}</option>
              <option value="data_sources">${{t.filter_data_sources}}</option>
              <option value="roots">${{t.filter_roots}}</option>
            </select>
            <button id="search-button">${{t.refresh}}</button>
            <button id="bindings-button" class="secondary">${{t.reloadBindings}}</button>
          </div>
          <h2 class="section-title">${{t.available}}</h2>
          <div id="results" class="list"></div>
        `;
        layout.appendChild(left);

        const right = document.createElement("div");
        right.className = "card";
        right.innerHTML = `
          <h2 class="section-title">${{t.selected}}</h2>
          <div id="selected" class="selected-list"></div>
          <h2 class="section-title">${{t.pastedUrls}}</h2>
          <div class="hint">${{t.pastedUrlsHint}}</div>
          <textarea id="url-input" spellcheck="false"></textarea>
          <div class="toolbar">
            <label class="pill">${{t.selectionScope}}
              <select id="url-scope">
                <option value="subtree">${{t.subtree}}</option>
                <option value="resource">${{t.resource}}</option>
              </select>
            </label>
          </div>
          <button id="bind-button">${{t.bindSelected}}</button>
          <div id="status" class="status ${{state.messageClass}}">${{state.loading ? t.loading : state.message}}</div>
          <h2 class="section-title">${{t.bound}}</h2>
          <div id="bindings" class="selected-list"></div>
        `;
        layout.appendChild(right);
        app.appendChild(layout);

        document.getElementById("search").addEventListener("input", (event) => {{
          state.query = event.target.value;
          scheduleSearch();
        }});
        document.getElementById("search").addEventListener("keydown", (event) => {{
          if (event.key === "Enter") {{
            event.preventDefault();
            loadCatalog();
          }}
        }});
        document.getElementById("filter").value = state.filter;
        document.getElementById("filter").addEventListener("change", (event) => {{
          state.filter = event.target.value;
          render();
        }});
        document.getElementById("hide-bound").addEventListener("change", (event) => {{
          state.hideBound = Boolean(event.target.checked);
          render();
        }});
        document.getElementById("search-button").addEventListener("click", () => loadCatalog());
        document.getElementById("bindings-button").addEventListener("click", () => loadBindings());
        document.getElementById("bind-button").addEventListener("click", () => bindSelected());

        const results = document.getElementById("results");
        let visibleCatalog = state.catalog;
        if (state.hideBound) {{
          visibleCatalog = visibleCatalog.filter((item) => !isBound(item));
        }}
        if (state.filter === "pages") {{
          visibleCatalog = visibleCatalog.filter((item) => item.resource_type === "page");
        }} else if (state.filter === "data_sources") {{
          visibleCatalog = visibleCatalog.filter((item) => item.resource_type === "data_source");
        }} else if (state.filter === "roots") {{
          visibleCatalog = visibleCatalog.filter((item) => isRootLike(item));
        }}
        if (!visibleCatalog.length) {{
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = state.loading ? t.loading : t.noResults;
          results.appendChild(empty);
        }} else {{
          const suggestedRoots = visibleCatalog.filter((item) => isRootLike(item));
          const remainingMatches = visibleCatalog.filter((item) => !isRootLike(item));

          if (suggestedRoots.length) {{
            const section = document.createElement("div");
            section.className = "list";
            const heading = document.createElement("div");
            heading.className = "pill";
            heading.textContent = `${{t.suggestedRoots}} · ${{suggestedRoots.length}}`;
            section.appendChild(heading);
            for (const item of suggestedRoots) {{
              section.appendChild(renderNode(item));
            }}
            results.appendChild(section);
          }}

          if (remainingMatches.length) {{
            const grouped = new Map();
            for (const item of remainingMatches) {{
              const key = item.resource_type || "other";
              if (!grouped.has(key)) grouped.set(key, []);
              grouped.get(key).push(item);
            }}
            for (const [type, items] of grouped.entries()) {{
              const section = document.createElement("div");
              section.className = "list";
              const heading = document.createElement("div");
              heading.className = "pill";
              heading.textContent = `${{t.moreMatches}} · ${{t[type] || type}} · ${{items.length}}`;
              section.appendChild(heading);
              for (const item of items) {{
                section.appendChild(renderNode(item));
              }}
              results.appendChild(section);
            }}
          }}
        }}

        const selected = document.getElementById("selected");
        if (!state.selected.size) {{
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = t.noSelection;
          selected.appendChild(empty);
        }} else {{
          for (const [key, item] of state.selected.entries()) {{
            const node = document.createElement("div");
            node.className = "selected-item";
            node.innerHTML = `
              <strong>${{item.title || item.resource_id_or_url}}</strong>
              <div class="meta">${{item.resource_type || "url"}}</div>
            `;
            const scope = document.createElement("select");
            scope.innerHTML = `
              <option value="resource">${{t.resource}}</option>
              <option value="subtree">${{t.subtree}}</option>
            `;
            scope.value = item.selection_scope;
            scope.addEventListener("change", (event) => updateSelectedScope(key, event.target.value));
            node.appendChild(scope);
            selected.appendChild(node);
          }}
        }}

        const bindings = document.getElementById("bindings");
        if (!state.bindings.length) {{
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = t.noBindings;
          bindings.appendChild(empty);
        }} else {{
          for (const item of state.bindings) {{
            const node = document.createElement("div");
            node.className = "selected-item";
            node.innerHTML = `
              <strong>${{item.title}}</strong>
              <div class="meta">${{item.resource_type}} · ${{item.selection_scope}} · ${{item.alias}}</div>
            `;
            bindings.appendChild(node);
          }}
        }}
      }}

      loadCatalog();
      loadBindings();
    </script>
  </body>
</html>
"""
