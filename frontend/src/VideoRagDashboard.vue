<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const loading = ref(true);
const err = ref("");
const items = ref([]);
const selectedId = ref("");
/** Multi-select for bulk delete (row checkboxes). */
const checkedIds = ref([]);
const deleteBulkBusy = ref(false);
const statusMsg = ref("");

const createBusy = ref(false);
const createItemId = ref("");
const createQuestion = ref("");
const createAnswer = ref("");
const createFileInput = ref(null);

const editBusy = ref(false);
const editQuestion = ref("");
const editAnswer = ref("");
const editFileInput = ref(null);

const searchBusy = ref(false);
const searchQuery = ref("");
const searchResults = ref([]);
const searchActive = ref(false);
const qdrantStatus = ref(null);
const garageStatus = ref(null);
const vodServiceStatus = ref(null);
const videoCdnConfigured = ref(false);
/** @type {import('vue').Ref<Record<string, object>>} */
const vodStatusByFilename = ref({});
const vodCheckBusy = ref(false);
const transcribeConfigured = ref(false);
const videoError = ref("");
const pageReady = ref(false);
const processBusy = ref(false);
const processAllBusy = ref(false);

const offlineExportsEnabled = ref(false);
const ragGenerateConfigured = ref(false);
const askQuestion = ref("");
const askBusy = ref(false);
const askAnswer = ref("");
const askMatches = ref([]);
const askExportJob = ref(null);
const askExportError = ref("");
const askGenerateError = ref("");

const exportJobs = ref([]);
const exportsLoading = ref(false);
const exportsPollTimer = ref(null);

const AUTO_PROCESS_STORAGE_KEY = "videoRagAutoProcess";

function readAutoProcessPref() {
  if (typeof window === "undefined") return true;
  const raw = window.localStorage.getItem(AUTO_PROCESS_STORAGE_KEY);
  if (raw === "0") return false;
  if (raw === "1") return true;
  return true;
}

const autoProcessNew = ref(readAutoProcessPref());

function setAutoProcessNew(enabled) {
  autoProcessNew.value = enabled;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(AUTO_PROCESS_STORAGE_KEY, enabled ? "1" : "0");
  }
}

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  const base = import.meta.env.VITE_API_BASE;
  if (base) return `${String(base).replace(/\/$/, "")}${p}`;
  if (typeof window !== "undefined") {
    const port = window.location.port;
    const host = window.location.hostname;
    if (port === "5173" || port === "4173") {
      return `http://${host}:8080${p}`;
    }
  }
  return p;
}

function itemFilename(item) {
  if (!item) return "";
  const fn = String(item.filename || "").trim();
  if (fn) return fn.includes("/") ? fn.split("/").pop() : fn;
  const id = String(item.id || "").trim();
  return id ? `${id}.mp4` : "";
}

function vodStatusForItem(item) {
  const name = itemFilename(item);
  return name ? vodStatusByFilename.value[name] || null : null;
}

function videoSrc(item) {
  const vod = vodStatusForItem(item);
  if (vod?.vod_url) return String(vod.vod_url).trim();
  if (!item?.video_url) return "";
  const url = String(item.video_url).trim();
  if (/^https?:\/\//i.test(url)) return url;
  const path = url.startsWith("/") ? url : `/${url}`;
  return apiUrl(path);
}

function cdnBadgeClass(item) {
  const vod = vodStatusForItem(item);
  if (!vod) return "badge badge--pending";
  if (vod.available && vod.http_available) return "badge badge--ok";
  if (vod.error) return "badge badge--error";
  return "badge badge--warn";
}

function cdnBadgeLabel(item) {
  const vod = vodStatusForItem(item);
  if (vodCheckBusy.value && !vod) return "…";
  if (!vod) return videoCdnConfigured.value ? "—" : "off";
  if (vod.available && vod.http_available) return "CDN";
  if (vod.status) return String(vod.status);
  return "missing";
}

async function parseApiResponse(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail ?? data.msg;
    const msg = typeof d === "string" ? d : JSON.stringify(d);
    throw new Error(msg || res.statusText || "Request failed");
  }
  if (typeof data.code === "number" && data.code !== 0) {
    throw new Error(data.msg || "Request failed");
  }
  return data.data ?? data;
}

async function loadStatus() {
  try {
    const res = await fetch(apiUrl("/api/video-qa/status"));
    const data = await parseApiResponse(res);
    qdrantStatus.value = data?.qdrant || null;
    garageStatus.value = data?.garage || null;
    vodServiceStatus.value = data?.vod || null;
    videoCdnConfigured.value = Boolean(data?.video_cdn_configured);
    transcribeConfigured.value = Boolean(data?.transcribe_configured);
    offlineExportsEnabled.value = Boolean(data?.offline_exports?.enabled);
    ragGenerateConfigured.value = Boolean(data?.rag_generate_configured);
  } catch {
    qdrantStatus.value = null;
    garageStatus.value = null;
    vodServiceStatus.value = null;
    videoCdnConfigured.value = false;
    offlineExportsEnabled.value = false;
    ragGenerateConfigured.value = false;
  }
}

async function loadVodStatusForRows(rows) {
  const names = [...new Set((rows || []).map(itemFilename).filter(Boolean))];
  if (!names.length) {
    vodStatusByFilename.value = {};
    return;
  }
  vodCheckBusy.value = true;
  try {
    const res = await fetch(apiUrl("/api/video-qa/vod/check"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ filenames: names }),
    });
    const data = await parseApiResponse(res);
    vodStatusByFilename.value = data?.by_filename && typeof data.by_filename === "object" ? data.by_filename : {};
  } catch {
    /* keep prior map on transient failure */
  } finally {
    vodCheckBusy.value = false;
  }
}

async function loadItems() {
  loading.value = true;
  err.value = "";
  try {
    const res = await fetch(apiUrl("/api/video-qa?limit=100"));
    const data = await parseApiResponse(res);
    items.value = Array.isArray(data.items) ? data.items : [];
    await loadVodStatusForRows(items.value);
    pruneCheckedIds();
    if (selectedId.value && !items.value.some((i) => i.id === selectedId.value)) {
      selectedId.value = items.value[0]?.id || "";
    } else if (!selectedId.value && items.value.length) {
      selectedId.value = items.value[0].id;
    }
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
    items.value = [];
  } finally {
    loading.value = false;
  }
}

const selectedItem = computed(() => {
  const id = selectedId.value;
  if (!id) return null;
  return items.value.find((i) => i.id === id) || searchResults.value.find((i) => i.id === id) || null;
});

const displayItems = computed(() => (searchActive.value ? searchResults.value : items.value));

const checkedCount = computed(() => checkedIds.value.length);

const allDisplayedChecked = computed(() => {
  const rows = displayItems.value;
  if (!rows.length) return false;
  const set = new Set(checkedIds.value);
  return rows.every((i) => set.has(i.id));
});

function isItemChecked(id) {
  return checkedIds.value.includes(id);
}

function toggleItemCheck(id, event) {
  event?.stopPropagation?.();
  if (!id) return;
  if (isItemChecked(id)) {
    checkedIds.value = checkedIds.value.filter((x) => x !== id);
  } else {
    checkedIds.value = [...checkedIds.value, id];
  }
}

function toggleSelectAllDisplayed(event) {
  event?.stopPropagation?.();
  const ids = displayItems.value.map((i) => i.id);
  if (!ids.length) return;
  if (allDisplayedChecked.value) {
    const drop = new Set(ids);
    checkedIds.value = checkedIds.value.filter((x) => !drop.has(x));
  } else {
    const merge = new Set([...checkedIds.value, ...ids]);
    checkedIds.value = [...merge];
  }
}

function clearChecked() {
  checkedIds.value = [];
}

function pruneCheckedIds() {
  const valid = new Set(items.value.map((i) => i.id));
  checkedIds.value = checkedIds.value.filter((id) => valid.has(id));
}

function selectItem(id) {
  selectedId.value = id;
  videoError.value = "";
  const item =
    items.value.find((i) => i.id === id) || searchResults.value.find((i) => i.id === id);
  if (item) {
    editQuestion.value = item.question;
    editAnswer.value = item.answer;
  }
}

function onVideoError() {
  const vod = selectedItem.value ? vodStatusForItem(selectedItem.value) : null;
  if (vod && !vod.available) {
    videoError.value =
      vod.error ||
      `Video not on CDN (${vod.status || "unavailable"}). Check VOD status for ${vod.video_name || itemFilename(selectedItem.value)}.`;
    return;
  }
  videoError.value =
    "Unable to load video from CDN. Check VOD status and that the file is published on the stream CDN.";
}

function fmtBytes(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtTs(ts) {
  if (!ts) return "—";
  try {
    return new Date(Number(ts) * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

async function createItem() {
  const itemId = createItemId.value.trim();
  const question = createQuestion.value.trim();
  const answer = createAnswer.value.trim();
  const file = createFileInput.value?.files?.[0];
  const willAutoProcess = autoProcessNew.value && transcribeConfigured.value;
  if (!itemId || !question || !file) {
    statusMsg.value = "Item ID, question, and video file are required.";
    return;
  }
  if (!willAutoProcess && !answer) {
    statusMsg.value = "Answer is required (or enable auto-process).";
    return;
  }
  createBusy.value = true;
  statusMsg.value = willAutoProcess ? "Uploading and processing…" : "";
  try {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("item_id", itemId);
    fd.append("question", question);
    fd.append("answer", answer || "Pending transcription");
    if (willAutoProcess) fd.append("auto_process", "true");
    const res = await fetch(apiUrl("/api/video-qa"), { method: "POST", body: fd });
    const data = await parseApiResponse(res);
    if (data.auto_processed) {
      statusMsg.value = `Created & processed "${data.id}" (trimmed ${data.process?.trim_start_sec ?? 0}s).`;
    } else if (data.process_error) {
      statusMsg.value = `Created "${data.id}" but processing failed: ${data.process_error}`;
    } else if (willAutoProcess && data.process?.skipped) {
      statusMsg.value = `Created "${data.id}" (already processed).`;
    } else {
      statusMsg.value = willAutoProcess
        ? `Created "${data.id}".`
        : `Created "${data.id}".`;
    }
    createItemId.value = "";
    createQuestion.value = "";
    createAnswer.value = "";
    if (createFileInput.value) createFileInput.value.value = "";
    searchActive.value = false;
    searchResults.value = [];
    await loadItems();
    await loadStatus();
    selectItem(data.id);
    const ansCreate = data.item?.answer ?? data.answer;
    if (ansCreate) editAnswer.value = ansCreate;
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    createBusy.value = false;
  }
}

async function updateItem() {
  if (!selectedItem.value) return;
  const file = editFileInput.value?.files?.[0];
  const willAutoProcess = Boolean(file && autoProcessNew.value && transcribeConfigured.value);
  editBusy.value = true;
  statusMsg.value = willAutoProcess ? "Saving and processing new video…" : "";
  try {
    const fd = new FormData();
    fd.append("question", editQuestion.value.trim());
    fd.append("answer", editAnswer.value.trim());
    if (file) fd.append("file", file);
    if (willAutoProcess) fd.append("auto_process", "true");
    const res = await fetch(apiUrl(`/api/video-qa/${encodeURIComponent(selectedItem.value.id)}`), {
      method: "PUT",
      body: fd,
    });
    const data = await parseApiResponse(res);
    if (data.auto_processed) {
      statusMsg.value = `Updated & processed "${data.id}" (trimmed ${data.process?.trim_start_sec ?? 0}s).`;
    } else if (data.process_error) {
      statusMsg.value = `Updated "${data.id}" but processing failed: ${data.process_error}`;
    } else {
      statusMsg.value = `Updated "${data.id}".`;
    }
    if (editFileInput.value) editFileInput.value.value = "";
    await loadItems();
    await loadStatus();
    selectItem(data.id);
    const ans = data.item?.answer ?? data.answer;
    if (ans) editAnswer.value = ans;
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    editBusy.value = false;
  }
}

async function deleteItem(id) {
  if (!id || !window.confirm(`Delete video Q&A entry "${id}"?`)) return;
  statusMsg.value = "";
  try {
    const res = await fetch(apiUrl(`/api/video-qa/${encodeURIComponent(id)}`), { method: "DELETE" });
    await parseApiResponse(res);
    statusMsg.value = `Deleted "${id}".`;
    if (selectedId.value === id) selectedId.value = "";
    checkedIds.value = checkedIds.value.filter((x) => x !== id);
    searchActive.value = false;
    searchResults.value = [];
    await loadItems();
    await loadStatus();
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
  }
}

async function deleteSelected() {
  const ids = [...checkedIds.value];
  if (!ids.length) return;
  const label = ids.length === 1 ? `entry "${ids[0]}"` : `${ids.length} entries`;
  if (!window.confirm(`Delete ${label}? This removes Garage video and Qdrant vectors.`)) return;
  deleteBulkBusy.value = true;
  statusMsg.value = "";
  err.value = "";
  try {
    const res = await fetch(apiUrl("/api/video-qa/bulk-delete"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ ids }),
    });
    const data = await parseApiResponse(res);
    const deleted = data.deleted || [];
    const failed = data.failed || [];
    if (deleted.includes(selectedId.value)) selectedId.value = "";
    checkedIds.value = checkedIds.value.filter((id) => !deleted.includes(id));
    searchActive.value = false;
    searchResults.value = [];
    await loadItems();
    await loadStatus();
    if (failed.length) {
      statusMsg.value = `Deleted ${deleted.length}; ${failed.length} failed.`;
      err.value = failed.map((f) => `${f.id}: ${f.error}`).join("; ");
    } else {
      statusMsg.value = `Deleted ${deleted.length} entr${deleted.length === 1 ? "y" : "ies"}.`;
    }
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    deleteBulkBusy.value = false;
  }
}

async function runSearch() {
  const q = searchQuery.value.trim();
  if (!q) {
    searchActive.value = false;
    searchResults.value = [];
    return;
  }
  searchBusy.value = true;
  statusMsg.value = "";
  try {
    const res = await fetch(apiUrl("/api/video-qa/search"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ query: q, limit: 50 }),
    });
    const data = await parseApiResponse(res);
    searchResults.value = Array.isArray(data.items) ? data.items : [];
    await loadVodStatusForRows(searchResults.value);
    searchActive.value = true;
    statusMsg.value = `Found ${searchResults.value.length} result(s) for "${q}".`;
    if (searchResults.value.length) selectItem(searchResults.value[0].id);
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    searchBusy.value = false;
  }
}

function clearSearch() {
  searchQuery.value = "";
  searchActive.value = false;
  searchResults.value = [];
}

function exportStatusClass(status) {
  const s = String(status || "").toLowerCase();
  if (["done", "completed", "complete", "success", "succeeded"].includes(s)) return "badge--ok";
  if (["failed", "error", "cancelled", "canceled"].includes(s)) return "badge--error";
  return "badge--pending";
}

async function loadExportJobs() {
  if (!offlineExportsEnabled.value) {
    exportJobs.value = [];
    return;
  }
  exportsLoading.value = true;
  try {
    const res = await fetch(apiUrl("/api/video-qa/exports?limit=30"));
    const data = await parseApiResponse(res);
    exportJobs.value = Array.isArray(data.jobs) ? data.jobs : [];
  } catch {
    exportJobs.value = [];
  } finally {
    exportsLoading.value = false;
  }
}

async function refreshExportJob(jobId, { silent = false } = {}) {
  if (!jobId) return;
  try {
    const res = await fetch(
      apiUrl(`/api/video-qa/exports/${encodeURIComponent(jobId)}?refresh=1`),
      { headers: { Accept: "application/json" } }
    );
    const job = await parseApiResponse(res);
    const idx = exportJobs.value.findIndex((j) => j.job_id === jobId);
    if (idx >= 0) {
      exportJobs.value = [
        ...exportJobs.value.slice(0, idx),
        job,
        ...exportJobs.value.slice(idx + 1),
      ];
    } else {
      exportJobs.value = [job, ...exportJobs.value];
    }
    if (askExportJob.value?.job_id === jobId) {
      askExportJob.value = job;
    }
    return job;
  } catch (e) {
    if (!silent) {
      statusMsg.value = e instanceof Error ? e.message : String(e);
    }
    return null;
  }
}

function startExportsPolling() {
  stopExportsPolling();
  if (!offlineExportsEnabled.value) return;
  exportsPollTimer.value = window.setInterval(async () => {
    const active = exportJobs.value.filter((j) => {
      const s = String(j.status || "").toLowerCase();
      return !["done", "completed", "complete", "success", "succeeded", "failed", "error", "cancelled", "canceled"].includes(s);
    });
    if (!active.length && !askExportJob.value?.job_id) return;
    await loadExportJobs();
    const jobId = askExportJob.value?.job_id;
    if (jobId) {
      const j = exportJobs.value.find((x) => x.job_id === jobId);
      if (j) askExportJob.value = j;
      else await refreshExportJob(jobId, { silent: true });
    }
  }, 5000);
}

function stopExportsPolling() {
  if (exportsPollTimer.value) {
    window.clearInterval(exportsPollTimer.value);
    exportsPollTimer.value = null;
  }
}

async function runAsk() {
  const q = askQuestion.value.trim();
  if (!q) return;
  askBusy.value = true;
  askAnswer.value = "";
  askMatches.value = [];
  askExportJob.value = null;
  askExportError.value = "";
  askGenerateError.value = "";
  statusMsg.value = "";
  try {
    const res = await fetch(apiUrl("/api/video-qa/ask"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ question: q, limit: 6 }),
    });
    const data = await parseApiResponse(res);
    askAnswer.value = data.answer || "";
    askMatches.value = Array.isArray(data.matches) ? data.matches : [];
    askExportJob.value = data.export_job || null;
    askExportError.value = data.export_error || "";
    askGenerateError.value = data.generate_error || "";
    if (askExportJob.value?.job_id) {
      await loadExportJobs();
      startExportsPolling();
    }
    if (askAnswer.value) {
      statusMsg.value = askExportJob.value
        ? `Answer ready — offline export ${askExportJob.value.status || "queued"}.`
        : "Answer ready.";
    } else {
      statusMsg.value = askGenerateError.value || askExportError.value || "No answer generated.";
    }
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    askBusy.value = false;
  }
}

const unprocessedCount = computed(
  () => items.value.filter((i) => !i.processed).length
);

async function processSelected(force = false) {
  if (!selectedItem.value) return;
  if (!transcribeConfigured.value) {
    statusMsg.value = "Transcribe API is not configured (TRANSCRIBE_API_URL).";
    return;
  }
  processBusy.value = true;
  statusMsg.value = "";
  try {
    const q = force ? "?force=true" : "";
    const res = await fetch(
      apiUrl(`/api/video-qa/${encodeURIComponent(selectedItem.value.id)}/process${q}`),
      { method: "POST", headers: { Accept: "application/json" } }
    );
    const data = await parseApiResponse(res);
    if (data.skipped) {
      statusMsg.value = `Skipped "${data.id}" (already processed). Use force to re-run.`;
    } else {
      statusMsg.value = `Processed "${data.id}": trimmed ${data.trim_start_sec ?? 0}s, answer updated.`;
    }
    await loadItems();
    const id = data.id || data.item?.id || selectedItem.value.id;
    selectItem(id);
    const ans = data.item?.answer ?? data.answer;
    if (ans) editAnswer.value = ans;
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
    await loadItems();
  } finally {
    processBusy.value = false;
  }
}

async function processAllUnprocessed() {
  if (!transcribeConfigured.value) {
    statusMsg.value = "Transcribe API is not configured (TRANSCRIBE_API_URL).";
    return;
  }
  if (!unprocessedCount.value) {
    statusMsg.value = "All entries are already processed.";
    return;
  }
  if (
    !window.confirm(
      `Process ${unprocessedCount.value} unprocessed entr${unprocessedCount.value === 1 ? "y" : "ies"}? This trims silence, transcribes audio, and updates answers.`
    )
  ) {
    return;
  }
  processAllBusy.value = true;
  statusMsg.value = "";
  try {
    const res = await fetch(apiUrl("/api/video-qa/process"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ skip_processed: true, limit: 100 }),
    });
    const data = await parseApiResponse(res);
    statusMsg.value = `Batch done: ${data.processed_count} processed, ${data.skipped_count} skipped, ${data.error_count} error(s).`;
    await loadItems();
    await loadStatus();
  } catch (e) {
    statusMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    processAllBusy.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadStatus(), loadItems()]);
  await loadExportJobs();
  if (offlineExportsEnabled.value) {
    startExportsPolling();
  }
  if (selectedItem.value) {
    editQuestion.value = selectedItem.value.question;
    editAnswer.value = selectedItem.value.answer;
  }
  requestAnimationFrame(() => {
    pageReady.value = true;
  });
});

onUnmounted(() => {
  stopExportsPolling();
});
</script>

<template>
  <div class="dash" :class="{ 'dash--ready': pageReady }">
    <div class="dash__bg" aria-hidden="true">
      <span class="dash__orb dash__orb--a" />
      <span class="dash__orb dash__orb--b" />
    </div>

    <header class="dash__head dash__anim" style="--i: 0">
      <div class="dash__head-main">
        <h1 class="dash__title">Video RAG</h1>
        <nav class="dash__nav" aria-label="App sections">
          <router-link class="dash-link dash-link--ghost" to="/hologram">Live hologram</router-link>
          <router-link class="dash-link dash-link--ghost" to="/avatar">Avatar Studio</router-link>
          <router-link class="dash-link dash-link--ghost" to="/analytics">Analytics</router-link>
        </nav>
      </div>
      <div class="dash__meta">
        <span v-if="garageStatus?.reachable" class="dash__qdrant dash__qdrant--ok">
          Garage · {{ garageStatus.bucket }}
        </span>
        <span v-else-if="garageStatus?.configured" class="dash__qdrant dash__qdrant--warn">
          Garage unreachable
        </span>
        <span v-if="qdrantStatus?.reachable" class="dash__qdrant dash__qdrant--ok">
          Qdrant · {{ qdrantStatus.collection }} · {{ qdrantStatus.points_count ?? 0 }} pts
        </span>
        <span v-else-if="qdrantStatus?.configured" class="dash__qdrant dash__qdrant--warn">
          Qdrant unreachable
        </span>
        <span v-if="transcribeConfigured" class="dash__qdrant dash__qdrant--ok">Transcribe ready</span>
        <span v-else class="dash__qdrant dash__qdrant--warn">Transcribe not configured</span>
        <span v-if="offlineExportsEnabled" class="dash__qdrant dash__qdrant--ok">Offline exports on</span>
        <span v-else class="dash__qdrant dash__qdrant--warn">Offline exports off</span>
        <span v-if="vodServiceStatus?.configured" class="dash__qdrant dash__qdrant--ok">VOD status API</span>
        <span v-if="videoCdnConfigured" class="dash__qdrant dash__qdrant--ok">CDN playback</span>
        <span>{{ items.length }} entr{{ items.length === 1 ? "y" : "ies" }}</span>
        <span v-if="unprocessedCount" class="dash__qdrant dash__qdrant--warn">
          {{ unprocessedCount }} unprocessed
        </span>
        <label
          class="auto-toggle"
          :class="{ 'auto-toggle--disabled': !transcribeConfigured }"
          :title="
            transcribeConfigured
              ? 'Trim silence, transcribe, and update answer when a new video is uploaded'
              : 'Configure TRANSCRIBE_API_URL to enable auto-process'
          "
        >
          <input
            type="checkbox"
            :checked="autoProcessNew"
            :disabled="!transcribeConfigured"
            @change="setAutoProcessNew($event.target.checked)"
          />
          <span class="auto-toggle__track" aria-hidden="true" />
          <span class="auto-toggle__label">Auto-process new videos</span>
        </label>
        <button
          type="button"
          class="btn btn--ghost"
          :disabled="processAllBusy || !unprocessedCount || !transcribeConfigured"
          @click="processAllUnprocessed"
        >
          {{ processAllBusy ? "Processing…" : "Process all" }}
        </button>
        <button
          type="button"
          class="btn btn--ghost"
          :disabled="vodCheckBusy || !items.length"
          title="Re-check CDN/VOD availability for all entries"
          @click="loadVodStatusForRows(items)"
        >
          {{ vodCheckBusy ? "CDN check…" : "Check CDN" }}
        </button>
        <button type="button" class="btn btn--ghost" :disabled="loading" @click="loadItems">
          {{ loading ? "Loading…" : "Refresh" }}
        </button>
      </div>
    </header>

    <p v-if="err" class="dash__err dash__anim" role="alert" style="--i: 1">{{ err }}</p>
    <p v-if="statusMsg" class="dash__status dash__anim" style="--i: 1">{{ statusMsg }}</p>

    <section class="panel dash__anim" style="--i: 2">
      <header class="panel__head">
        <h2 class="panel__title">Ask (RAG)</h2>
        <p class="panel__sub">
          Search video Q&A, generate an answer
          <template v-if="ragGenerateConfigured"> via RAG stream</template>
          <template v-if="offlineExportsEnabled">, then queue offline lip-sync export</template>
        </p>
      </header>
      <label class="field">
        <span>Your question</span>
        <textarea
          v-model="askQuestion"
          rows="2"
          class="field-input"
          placeholder="Ask about your video knowledge base…"
          @keydown.ctrl.enter="runAsk"
          @keydown.meta.enter="runAsk"
        />
      </label>
      <div class="panel__row">
        <button type="button" class="btn" :disabled="askBusy || !askQuestion.trim()" @click="runAsk">
          {{ askBusy ? "Asking…" : "Ask" }}
        </button>
      </div>
      <div v-if="askAnswer" class="ask-result">
        <h3 class="ask-result__title">Answer</h3>
        <p class="ask-result__text">{{ askAnswer }}</p>
      </div>
      <p v-if="askGenerateError" class="preview-hero__err">{{ askGenerateError }}</p>
      <p v-if="askExportError" class="preview-hero__err">Export: {{ askExportError }}</p>
      <div v-if="askExportJob" class="ask-export">
        <h3 class="ask-result__title">Offline export</h3>
        <p class="ask-export__meta">
          <code>{{ askExportJob.job_id }}</code>
          <span :class="['badge', exportStatusClass(askExportJob.status)]">{{ askExportJob.status }}</span>
          <span v-if="askExportJob.stage">· {{ askExportJob.stage }}</span>
        </p>
        <p v-if="askExportJob.error" class="preview-hero__err">{{ askExportJob.error }}</p>
        <p v-if="askExportJob.output_path" class="ask-export__path">{{ askExportJob.output_path }}</p>
        <button
          type="button"
          class="btn btn--ghost btn--sm"
          @click="refreshExportJob(askExportJob.job_id)"
        >
          Refresh status
        </button>
      </div>
      <ul v-if="askMatches.length" class="ask-matches">
        <li v-for="m in askMatches" :key="m.id">
          <button type="button" class="btn btn--ghost btn--sm" @click="selectItem(m.id)">
            {{ m.id }} — {{ (m.question || m.answer || "").slice(0, 80) }}
          </button>
        </li>
      </ul>
    </section>

    <section v-if="offlineExportsEnabled" class="panel dash__anim" style="--i: 2">
      <header class="panel__head">
        <h2 class="panel__title">Offline exports</h2>
        <p class="panel__sub">Lip-sync jobs queued after Ask</p>
      </header>
      <div class="panel__row">
        <button type="button" class="btn btn--ghost" :disabled="exportsLoading" @click="loadExportJobs">
          {{ exportsLoading ? "Loading…" : "Refresh jobs" }}
        </button>
      </div>
      <p v-if="!exportJobs.length && !exportsLoading" class="panel__sub">No export jobs yet.</p>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Status</th>
              <th>Stage</th>
              <th>Item</th>
              <th>Updated</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="j in exportJobs" :key="j.job_id">
              <td><code>{{ j.job_id.slice(0, 12) }}…</code></td>
              <td><span :class="['badge', exportStatusClass(j.status)]">{{ j.status }}</span></td>
              <td>{{ j.stage || "—" }}</td>
              <td><code>{{ j.export_item_id }}</code></td>
              <td>{{ fmtTs(j.updated_at) }}</td>
              <td>
                <button type="button" class="btn btn--ghost btn--sm" @click="refreshExportJob(j.job_id)">
                  Refresh
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel dash__anim" style="--i: 2">
      <header class="panel__head">
        <h2 class="panel__title">Search</h2>
        <p class="panel__sub">Find entries by question, answer, or ID</p>
      </header>
      <div class="panel__row">
        <input
          v-model="searchQuery"
          type="search"
          class="field-input"
          placeholder="Search video Q&A…"
          @keydown.enter="runSearch"
        />
        <button type="button" class="btn" :disabled="searchBusy || !searchQuery.trim()" @click="runSearch">
          {{ searchBusy ? "Searching…" : "Search" }}
        </button>
        <button v-if="searchActive" type="button" class="btn btn--ghost" @click="clearSearch">Clear</button>
      </div>
    </section>

    <Transition name="preview">
      <section v-if="selectedItem" key="preview" class="preview-hero dash__anim" style="--i: 3">
        <div class="preview-hero__head">
          <div>
            <h2 class="preview-hero__title">Video preview</h2>
            <p class="preview-hero__sub">{{ selectedItem.question }}</p>
          </div>
          <code class="preview-hero__id">{{ selectedItem.id }}</code>
        </div>
        <div class="preview-hero__stage">
          <video
            :key="selectedItem.id + '-' + (selectedItem.updated_at || 0) + '-' + (selectedItem.size_bytes || 0)"
            class="preview-hero__player"
            controls
            playsinline
            preload="metadata"
            :src="videoSrc(selectedItem)"
            @error="onVideoError"
          />
        </div>
        <p v-if="videoError" class="preview-hero__err">{{ videoError }}</p>
        <p v-if="selectedItem.process_error" class="preview-hero__err">{{ selectedItem.process_error }}</p>
        <p v-if="vodStatusForItem(selectedItem)" class="preview-hero__meta preview-hero__cdn">
          <span :class="cdnBadgeClass(selectedItem)">{{ cdnBadgeLabel(selectedItem) }}</span>
          <template v-if="vodStatusForItem(selectedItem).vod_url">
            ·
            <a
              class="preview-hero__link"
              :href="vodStatusForItem(selectedItem).vod_url"
              target="_blank"
              rel="noopener noreferrer"
              @click.stop
            >CDN stream</a>
          </template>
          <template v-if="vodStatusForItem(selectedItem).file_size_bytes">
            · CDN {{ fmtBytes(vodStatusForItem(selectedItem).file_size_bytes) }}
          </template>
          <template v-if="vodStatusForItem(selectedItem).status_url">
            ·
            <a
              class="preview-hero__link"
              :href="vodStatusForItem(selectedItem).status_url"
              target="_blank"
              rel="noopener noreferrer"
              @click.stop
            >VOD status</a>
          </template>
        </p>
        <p class="preview-hero__meta">
          {{ selectedItem.filename }} · {{ fmtBytes(selectedItem.size_bytes) }} ·
          {{ selectedItem.garage_object_key }} · updated {{ fmtTs(selectedItem.updated_at) }}
          <span v-if="selectedItem.processed" class="badge badge--ok">Processed</span>
          <span v-else class="badge badge--pending">Not processed</span>
        </p>
      </section>
    </Transition>

    <div class="layout dash__anim" style="--i: 4">
      <section class="panel panel--create">
        <header class="panel__head">
          <h2 class="panel__title">Create</h2>
          <p class="panel__sub">
            Upload a video with question{{ autoProcessNew && transcribeConfigured ? " (answer optional — transcribed automatically)" : " and answer" }}
          </p>
        </header>
        <label class="field">
          <span>Item ID</span>
          <input v-model="createItemId" type="text" class="field-input" placeholder="video_1" />
        </label>
        <label class="field">
          <span>Question</span>
          <textarea v-model="createQuestion" rows="2" class="field-input" placeholder="What would you like to know?" />
        </label>
        <label class="field">
          <span>Answer{{ autoProcessNew && transcribeConfigured ? " (optional)" : "" }}</span>
          <textarea
            v-model="createAnswer"
            rows="3"
            class="field-input"
            :placeholder="
              autoProcessNew && transcribeConfigured
                ? 'Leave blank to transcribe from video…'
                : 'The avatar\'s response…'
            "
          />
        </label>
        <label class="field">
          <span>Video file</span>
          <input ref="createFileInput" type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov" />
        </label>
        <button type="button" class="btn" :disabled="createBusy" @click="createItem">
          {{
            createBusy
              ? autoProcessNew && transcribeConfigured
                ? "Creating & processing…"
                : "Creating…"
              : autoProcessNew && transcribeConfigured
                ? "Create & process"
                : "Create entry"
          }}
        </button>
      </section>

      <section class="panel panel--detail">
        <header class="panel__head">
          <h2 class="panel__title">Edit selected</h2>
          <p class="panel__sub">Update question, answer, or replace video</p>
        </header>
        <template v-if="selectedItem">
          <p class="detail-id">ID: <code>{{ selectedItem.id }}</code></p>
          <label class="field">
            <span>Question</span>
            <textarea v-model="editQuestion" rows="2" class="field-input" />
          </label>
          <label class="field">
            <span>Answer</span>
            <textarea v-model="editAnswer" rows="3" class="field-input" />
          </label>
          <label class="field">
            <span>Replace video (optional)</span>
            <input ref="editFileInput" type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov" />
          </label>
          <div class="panel__row">
            <button type="button" class="btn" :disabled="editBusy" @click="updateItem">
              {{ editBusy ? "Saving…" : "Save changes" }}
            </button>
            <button
              type="button"
              class="btn btn--accent"
              :disabled="processBusy || !transcribeConfigured"
              @click="processSelected(Boolean(selectedItem.processed))"
            >
              {{ processBusy ? "Processing…" : selectedItem.processed ? "Re-process" : "Process" }}
            </button>
            <button type="button" class="btn btn--danger" @click="deleteItem(selectedItem.id)">Delete</button>
          </div>
        </template>
        <p v-else class="panel__empty">Select an entry from the table to edit.</p>
      </section>
    </div>

    <section class="table-section dash__anim" style="--i: 5">
      <header class="table-section__head">
        <div>
          <h2 class="table-section__title">{{ searchActive ? "Search results" : "All entries" }}</h2>
          <p class="table-section__sub">{{ displayItems.length }} shown</p>
        </div>
        <div v-if="checkedCount" class="table-section__bulk">
          <span class="table-section__bulk-count">{{ checkedCount }} selected</span>
          <button type="button" class="btn btn--ghost btn--sm" :disabled="deleteBulkBusy" @click="clearChecked">
            Clear selection
          </button>
          <button
            type="button"
            class="btn btn--danger btn--sm"
            :disabled="deleteBulkBusy"
            @click="deleteSelected"
          >
            {{ deleteBulkBusy ? "Deleting…" : `Delete selected (${checkedCount})` }}
          </button>
        </div>
      </header>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th class="table__check">
                <input
                  type="checkbox"
                  class="table__checkbox"
                  :checked="allDisplayedChecked"
                  :disabled="!displayItems.length"
                  aria-label="Select all shown entries"
                  @click.stop
                  @change="toggleSelectAllDisplayed"
                />
              </th>
              <th>ID</th>
              <th>Question</th>
              <th>Answer</th>
              <th>Status</th>
              <th>CDN</th>
              <th>Error</th>
              <th>Size</th>
              <th>Updated</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!displayItems.length">
              <td colspan="10" class="table__empty">No entries yet — create one above.</td>
            </tr>
            <tr
              v-for="(item, idx) in displayItems"
              :key="item.id"
              class="table__row dash__anim"
              :class="{
                'table__row--selected': item.id === selectedId,
                'table__row--checked': isItemChecked(item.id),
                'table__row--error': item.process_error,
              }"
              :style="{ '--i': idx }"
              @click="selectItem(item.id)"
            >
              <td class="table__check" @click.stop>
                <input
                  type="checkbox"
                  class="table__checkbox"
                  :checked="isItemChecked(item.id)"
                  :aria-label="`Select ${item.id}`"
                  @change="toggleItemCheck(item.id, $event)"
                />
              </td>
              <td class="mono">{{ item.id }}</td>
              <td>{{ item.processed && !item.question ? "—" : item.question }}</td>
              <td>{{ item.answer }}</td>
              <td>
                <span v-if="item.process_error" class="badge badge--error">Error</span>
                <span v-else-if="item.processed" class="badge badge--ok">Processed</span>
                <span v-else class="badge badge--pending">Pending</span>
              </td>
              <td
                class="table__cdn"
                :title="
                  vodStatusForItem(item)?.vod_url ||
                  vodStatusForItem(item)?.error ||
                  vodStatusForItem(item)?.status_url ||
                  ''
                "
              >
                <span :class="cdnBadgeClass(item)">{{ cdnBadgeLabel(item) }}</span>
              </td>
              <td class="table__error" :title="item.process_error || ''">
                {{ item.process_error || "—" }}
              </td>
              <td class="mono">{{ fmtBytes(item.size_bytes) }}</td>
              <td class="mono">{{ fmtTs(item.updated_at) }}</td>
              <td>
                <button type="button" class="btn-mini btn-mini--danger" @click.stop="deleteItem(item.id)">
                  Delete
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dash {
  position: relative;
  min-height: 100dvh;
  padding: clamp(0.75rem, 2vw, 1.5rem);
  overflow: hidden;
  background: linear-gradient(165deg, #eef2f7 0%, #e4e2e2 45%, #ebe8f4 100%);
}

.dash__bg {
  pointer-events: none;
  position: absolute;
  inset: 0;
  overflow: hidden;
  z-index: 0;
}

.dash__orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(60px);
  opacity: 0.45;
  animation: orb-drift 18s ease-in-out infinite alternate;
}

.dash__orb--a {
  width: 28rem;
  height: 28rem;
  top: -8rem;
  right: -6rem;
  background: radial-gradient(circle, rgba(0, 180, 180, 0.35), transparent 70%);
}

.dash__orb--b {
  width: 24rem;
  height: 24rem;
  bottom: -6rem;
  left: -4rem;
  background: radial-gradient(circle, rgba(107, 82, 216, 0.3), transparent 70%);
  animation-delay: -6s;
}

.dash > *:not(.dash__bg) {
  position: relative;
  z-index: 1;
}

.dash__anim {
  opacity: 0;
  transform: translateY(14px);
}

.dash--ready .dash__anim {
  animation: rise-in 0.55s cubic-bezier(0.22, 1, 0.36, 1) forwards;
  animation-delay: calc(var(--i, 0) * 70ms);
}

.table__row.dash__anim {
  animation-delay: calc(350ms + var(--i, 0) * 35ms);
}

@keyframes rise-in {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes orb-drift {
  from {
    transform: translate(0, 0) scale(1);
  }
  to {
    transform: translate(-2rem, 1.5rem) scale(1.08);
  }
}

.preview-enter-active,
.preview-leave-active {
  transition: opacity 0.35s ease, transform 0.45s cubic-bezier(0.22, 1, 0.36, 1);
}

.preview-enter-from,
.preview-leave-to {
  opacity: 0;
  transform: translateY(12px) scale(0.985);
}

.preview-hero {
  margin-bottom: 1rem;
  padding: 1rem 1.1rem 1.1rem;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(0, 0, 0, 0.06);
  box-shadow: 0 12px 32px rgba(2, 6, 23, 0.08);
}

.preview-hero__head {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1rem;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.preview-hero__title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
}

.preview-hero__sub {
  margin: 0.25rem 0 0;
  font-size: 0.88rem;
  color: #555;
  max-width: 48rem;
}

.preview-hero__id {
  font-size: 0.72rem;
  padding: 0.25rem 0.5rem;
  border-radius: 8px;
  background: #f3f4f6;
  color: #444;
  word-break: break-all;
  max-width: 100%;
}

.preview-hero__stage {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08);
}

.preview-hero__player {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: contain;
  background: #000;
}

.preview-hero__shimmer {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    105deg,
    transparent 40%,
    rgba(255, 255, 255, 0.12) 50%,
    transparent 60%
  );
  background-size: 200% 100%;
  animation: shimmer 1.4s ease-in-out infinite;
}

@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.preview-hero__meta {
  margin: 0.55rem 0 0;
  font-size: 0.78rem;
  color: #666;
  word-break: break-all;
}

.preview-hero__err {
  margin: 0.45rem 0 0;
  font-size: 0.78rem;
  color: #b91c1c;
}

.dash__head {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1.25rem;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1rem;
  padding: 0.85rem 1rem;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(0, 0, 0, 0.06);
  box-shadow: 0 8px 24px rgba(2, 6, 23, 0.06);
}

.dash__head-main {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1.5rem;
  align-items: center;
}

.dash__title {
  margin: 0;
  font-size: clamp(1.35rem, 3vw, 1.85rem);
  font-weight: 800;
  letter-spacing: -0.02em;
}

.dash__nav {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.dash-link {
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  font-size: 0.85rem;
  font-weight: 600;
  text-decoration: none;
  color: #1a1a1a;
  background: rgba(0, 180, 180, 0.12);
  border: 1px solid rgba(0, 180, 180, 0.25);
  transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.dash-link:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 180, 180, 0.15);
}

.dash-link--ghost {
  background: transparent;
  border-color: rgba(0, 0, 0, 0.12);
}

.dash__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.88rem;
  color: #555;
}

.dash__qdrant {
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
}

.dash__qdrant--ok {
  color: #047857;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
}

.dash__qdrant--warn {
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
}

.dash__err {
  margin: 0 0 0.75rem;
  padding: 0.65rem 0.85rem;
  border-radius: 10px;
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.dash__status {
  margin: 0 0 0.75rem;
  padding: 0.65rem 0.85rem;
  border-radius: 10px;
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #a7f3d0;
}

.layout {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}

.panel {
  padding: 1rem;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(0, 0, 0, 0.06);
  box-shadow: 0 6px 16px rgba(2, 6, 23, 0.05);
  transition: box-shadow 0.25s ease, transform 0.25s ease;
}

.panel:hover {
  box-shadow: 0 10px 24px rgba(2, 6, 23, 0.08);
}

.panel__head {
  margin-bottom: 0.85rem;
}

.panel__title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
}

.panel__sub {
  margin: 0.25rem 0 0;
  font-size: 0.82rem;
  color: #666;
}

.panel__row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.panel__empty {
  margin: 0;
  font-size: 0.9rem;
  color: #666;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: #444;
}

.field-input {
  width: 100%;
  padding: 0.5rem 0.65rem;
  border-radius: 10px;
  border: 1px solid #ccc;
  font: inherit;
  font-weight: 400;
  resize: vertical;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.field-input:focus {
  outline: none;
  border-color: #00b4b4;
  box-shadow: 0 0 0 3px rgba(0, 180, 180, 0.15);
}

.detail-id {
  margin: 0 0 0.75rem;
  font-size: 0.85rem;
  color: #555;
}

.detail-id code {
  font-size: 0.82rem;
  background: #f3f4f6;
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
}

.table-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.5rem;
  flex-wrap: wrap;
}

.table-section__bulk {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.table-section__bulk-count {
  font-size: 0.85rem;
  font-weight: 600;
  color: #334155;
}

.table__check {
  width: 2.5rem;
  padding-left: 0.65rem !important;
  padding-right: 0.35rem !important;
  vertical-align: middle;
}

.table__checkbox {
  width: 1rem;
  height: 1rem;
  cursor: pointer;
  accent-color: #6b52d8;
}

.table__row--checked {
  background: rgba(0, 180, 180, 0.04);
}

.table-section__title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 700;
}

.table-section__sub {
  margin: 0.15rem 0 0;
  font-size: 0.82rem;
  color: #666;
}

.table-wrap {
  overflow: auto;
  max-width: 100%;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 6px 16px rgba(2, 6, 23, 0.06);
}

.table {
  width: 100%;
  min-width: 40rem;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.table th,
.table td {
  padding: 0.5rem 0.6rem;
  text-align: left;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  vertical-align: top;
}

.table th {
  background: #f6f6f6;
  font-weight: 600;
  white-space: nowrap;
  position: sticky;
  top: 0;
}

.table tbody tr {
  cursor: pointer;
  transition: background 0.2s ease, transform 0.2s ease;
}

.table tbody tr:hover {
  background: rgba(0, 180, 180, 0.06);
}

.table__row--selected {
  background: rgba(123, 97, 255, 0.1) !important;
  box-shadow: inset 3px 0 0 #6b52d8;
}

.table__empty {
  text-align: center;
  color: #666;
  padding: 2rem !important;
  cursor: default;
}

.mono {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.btn {
  padding: 0.5rem 1.1rem;
  border-radius: 999px;
  border: none;
  font: inherit;
  font-weight: 600;
  font-size: 0.88rem;
  cursor: pointer;
  color: #fff;
  background: linear-gradient(135deg, #00b4b4, #6b52d8);
  transition: transform 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
}

.btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 6px 18px rgba(107, 82, 216, 0.28);
}

.btn--ghost {
  color: #333;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.15);
}

.btn--danger {
  background: linear-gradient(135deg, #c62828, #8e24aa);
}

.btn--accent {
  background: linear-gradient(135deg, #0d9488, #0891b2);
  color: #fff;
}

.badge {
  display: inline-block;
  margin-left: 0.35rem;
  padding: 0.12rem 0.45rem;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 600;
  vertical-align: middle;
}

.badge--ok {
  background: #d1fae5;
  color: #065f46;
}

.badge--pending {
  background: #fef3c7;
  color: #92400e;
}

.badge--error {
  background: #fee2e2;
  color: #991b1b;
}

.badge--warn {
  background: #ffedd5;
  color: #9a3412;
}

.preview-hero__link {
  color: #5b21b6;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.preview-hero__link:hover {
  color: #4c1d95;
}

.table__row--error {
  background: rgba(254, 226, 226, 0.35);
}

.table__error {
  max-width: 14rem;
  font-size: 0.78rem;
  color: #b91c1c;
  word-break: break-word;
  line-height: 1.35;
}

.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  cursor: pointer;
  user-select: none;
  font-size: 0.82rem;
  font-weight: 600;
  color: #334155;
}

.auto-toggle--disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.auto-toggle input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.auto-toggle__track {
  position: relative;
  width: 2.35rem;
  height: 1.25rem;
  border-radius: 999px;
  background: #cbd5e1;
  transition: background 0.2s ease;
  flex-shrink: 0;
}

.auto-toggle__track::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: calc(1.25rem - 4px);
  height: calc(1.25rem - 4px);
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.25);
  transition: transform 0.2s ease;
}

.auto-toggle input:checked + .auto-toggle__track {
  background: linear-gradient(135deg, #0d9488, #0891b2);
}

.auto-toggle input:checked + .auto-toggle__track::after {
  transform: translateX(1.1rem);
}

.auto-toggle input:focus-visible + .auto-toggle__track {
  outline: 2px solid #0891b2;
  outline-offset: 2px;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-mini {
  padding: 0.25rem 0.55rem;
  border-radius: 8px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
  font: inherit;
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.15s ease;
}

.btn-mini:hover {
  transform: scale(1.03);
}

.btn-mini--danger {
  color: #b91c1c;
  border-color: #fecaca;
  background: #fef2f2;
}

.ask-result {
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  border-radius: 10px;
  background: rgba(0, 180, 180, 0.06);
  border: 1px solid rgba(0, 180, 180, 0.15);
}

.ask-result__title {
  margin: 0 0 0.35rem;
  font-size: 0.85rem;
  font-weight: 700;
  color: #444;
}

.ask-result__text {
  margin: 0;
  white-space: pre-wrap;
  line-height: 1.5;
}

.ask-export {
  margin-top: 0.75rem;
}

.ask-export__meta {
  margin: 0.25rem 0;
  font-size: 0.85rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
}

.ask-export__path {
  margin: 0.25rem 0;
  font-size: 0.78rem;
  color: #666;
  word-break: break-all;
}

.ask-matches {
  margin: 0.75rem 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
</style>
