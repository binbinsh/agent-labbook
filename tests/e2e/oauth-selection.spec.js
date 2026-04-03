import http from "node:http";

import { expect, test } from "@playwright/test";

import { TEST_IDS, startWorkerTestServer } from "./support/worker-test-server.mjs";

let testServer;

function startLocalHandoffServer(expectedSessionId) {
  let resolveBody;
  const bodyPromise = new Promise((resolve) => {
    resolveBody = resolve;
  });

  const server = http.createServer((request, response) => {
    if (request.method !== "POST" || request.url !== "/oauth/handoff") {
      response.writeHead(404);
      response.end();
      return;
    }

    let rawBody = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      rawBody += chunk;
    });
    request.on("end", () => {
      const form = new URLSearchParams(rawBody);
      resolveBody({
        sessionId: form.get("session_id"),
        handoffBundle: form.get("handoff_bundle"),
      });
      response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      response.end(`<!doctype html><html><body><script>
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage(${JSON.stringify(
            {
              type: "agent-labbook-local-handoff-success",
              session_id: expectedSessionId,
            },
          )}, "*");
        }
      </script><h1>Connected</h1></body></html>`);
    });
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({
        close: () =>
          new Promise((closeResolve, closeReject) => {
            server.close((error) => {
              if (error) {
                closeReject(error);
                return;
              }
              closeResolve();
            });
          }),
        url: `http://127.0.0.1:${address.port}/oauth/handoff`,
        bodyPromise,
      });
    });
  });
}

test.beforeAll(async () => {
  testServer = await startWorkerTestServer();
});

test.afterAll(async () => {
  if (testServer) {
    await testServer.stop();
  }
});

test("oauth callback page supports remote search, on-demand expansion, and headless handoff", async ({
  page,
  request,
}) => {
  const sessionId = "e2e-session-1";
  const startResponse = await fetch(
    `${testServer.baseUrl}/oauth/start?mode=headless&session_id=${sessionId}&project_name=agent-labbook-e2e&page_limit=25`,
    {
      redirect: "manual",
    },
  );

  expect(startResponse.status).toBe(302);
  const location = startResponse.headers.get("location");
  expect(location).toBeTruthy();
  const state = new URL(location).searchParams.get("state");
  expect(state).toBeTruthy();

  await page.goto(`${testServer.baseUrl}/oauth/callback?code=fake-oauth-code&state=${encodeURIComponent(state)}`);

  await expect(page.getByRole("heading", { name: "Choose Notion Content" })).toBeVisible();
  await expect(page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`)).toContainText("Project Hub");
  await expect(page.locator(`[data-resource-id="${TEST_IDS.ENGINEERING_HANDBOOK_ID}"]`)).toHaveCount(0);
  await expect(page.locator(`[data-resource-id="${TEST_IDS.PYNINI_PAGE_ID}"]`)).toHaveCount(0);

  const searchInput = page.getByPlaceholder("Search workspace by title, type, or ID");
  await searchInput.fill("Engineering");
  await expect(page.locator(`[data-resource-id="${TEST_IDS.ENGINEERING_HANDBOOK_ID}"]`)).toContainText(
    "Engineering Handbook",
  );
  expect(testServer.mockState.searchBodies.some((body) => body.query === "engineering")).toBeTruthy();

  await searchInput.fill("pynini");
  await expect(page.locator(`[data-resource-id="${TEST_IDS.PYNINI_PAGE_ID}"]`)).toContainText("Pynini Notes");
  expect(testServer.mockState.searchBodies.some((body) => body.query === "pynini")).toBeTruthy();

  await searchInput.fill("");
  const projectHubRow = page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`);
  await expect(projectHubRow).toBeVisible();
  await projectHubRow.getByRole("button", { name: "Expand nested items" }).click();
  await expect(page.locator(`[data-resource-id="${TEST_IDS.ROADMAP_DOC_ID}"]`)).toContainText("Roadmap Doc");
  await expect(page.locator(`[data-resource-id="${TEST_IDS.SPECS_DATA_SOURCE_ID}"]`)).toContainText("Specs DB");

  await projectHubRow.getByRole("checkbox").click();
  await expect(projectHubRow).toContainText("Includes subtree");
  await page.getByRole("button", { name: "Connect Selected" }).click();

  const handoffBundle = await page.locator("textarea").inputValue();
  expect(handoffBundle).toBeTruthy();

  const consumeResponse = await request.post(`${testServer.baseUrl}/api/consume-handoff`, {
    data: {
      session_id: sessionId,
      handoff_bundle: handoffBundle,
    },
  });
  const consumePayload = await consumeResponse.json();
  expect(consumePayload.ok).toBe(true);
  expect(consumePayload.payload.selected_resources).toHaveLength(1);
  expect(consumePayload.payload.selected_resources[0].resource_id).toBe(TEST_IDS.PROJECT_HUB_ID);
  expect(consumePayload.payload.selected_resources[0].selection_scope).toBe("subtree");
});

test("local browser flow falls back to showing the handoff bundle when localhost callback is unreachable", async ({
  page,
}) => {
  const sessionId = "e2e-session-local-fallback";
  const returnTo = "http://127.0.0.1:8765/oauth/handoff";
  const startResponse = await fetch(
    `${testServer.baseUrl}/oauth/start?mode=local_browser&session_id=${sessionId}&project_name=agent-labbook-e2e&page_limit=25&return_to=${encodeURIComponent(returnTo)}`,
    {
      redirect: "manual",
    },
  );

  expect(startResponse.status).toBe(302);
  const location = startResponse.headers.get("location");
  expect(location).toBeTruthy();
  const state = new URL(location).searchParams.get("state");
  expect(state).toBeTruthy();

  await page.goto(`${testServer.baseUrl}/oauth/callback?code=fake-oauth-code&state=${encodeURIComponent(state)}`);
  const projectHubRow = page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`);
  await projectHubRow.getByRole("checkbox").click();
  await page.getByRole("button", { name: "Connect Selected" }).click();

  await expect(page.getByText("could not reach the MCP server on 127.0.0.1")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("textarea")).not.toHaveValue("");
});

test("local browser flow can deliver the handoff through a popup localhost navigation", async ({ page }) => {
  const sessionId = "e2e-session-local-success";
  const localServer = await startLocalHandoffServer(sessionId);
  const startResponse = await fetch(
    `${testServer.baseUrl}/oauth/start?mode=local_browser&session_id=${sessionId}&project_name=agent-labbook-e2e&page_limit=25&return_to=${encodeURIComponent(localServer.url)}`,
    {
      redirect: "manual",
    },
  );

  expect(startResponse.status).toBe(302);
  const location = startResponse.headers.get("location");
  expect(location).toBeTruthy();
  const state = new URL(location).searchParams.get("state");
  expect(state).toBeTruthy();

  try {
    await page.goto(`${testServer.baseUrl}/oauth/callback?code=fake-oauth-code&state=${encodeURIComponent(state)}`);
    const projectHubRow = page.locator(`[data-resource-id="${TEST_IDS.PROJECT_HUB_ID}"]`);
    await projectHubRow.getByRole("checkbox").click();

    const popupPromise = page.waitForEvent("popup");
    await page.getByRole("button", { name: "Connect Selected" }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState("domcontentloaded");

    await expect(page.getByText("The handoff was sent back to the local MCP server.")).toBeVisible();
    const delivered = await localServer.bodyPromise;
    expect(delivered.sessionId).toBe(sessionId);
    expect(delivered.handoffBundle).toBeTruthy();
  } finally {
    await localServer.close();
  }
});
